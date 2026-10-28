"""Regression cases for geometry, measured typography and truthful provenance."""
import hashlib
import xml.etree.ElementTree as ET
import fitz
import pytest
from PIL import Image
from engine import edit_pdf, PrecisionError, _validate_untouched
from test_engine import make_pdf


def test_single_channel_pixel_change_is_not_hidden_by_luminance():
    original=Image.new('RGB',(20,20),(100,100,100))
    changed=original.copy();changed.putpixel((10,10),(101,100,100))
    assert _validate_untouched(original,changed,[])[0] is False


def tracked_pdf(text='2025',tracking=1.25):
    doc=fitz.open();p=doc.new_page()
    # Explicit PDF text state, not separate synthetic text spans.
    p.insert_text((70,95),text,fontsize=8)
    xref=p.get_contents()[0]
    stream=doc.xref_stream(xref).replace(b'8 Tf',f'8 Tf {tracking} Tc'.encode())
    doc.update_stream(xref,stream)
    return doc.tobytes()


def origins(data):
    doc=fitz.open(stream=data,filetype='pdf')
    return [c[2] for t in doc[0].get_texttrace() for c in t['chars']]


def test_tracked_fine_print_retains_every_original_origin():
    src=tracked_pdf()
    out=edit_pdf(src,'Replace "2025" with "2026"')
    for a,b in zip(origins(src),origins(out['pdf'])):
        assert max(abs(x-y) for x,y in zip(a,b))<.001
    assert out['edits'][0]['spacing_mode']=='original glyph origins'


def test_different_character_count_retains_uniform_tracking():
    src=tracked_pdf()
    out=edit_pdf(src,'Replace "2025" with "20260"')
    pts=origins(out['pdf'])
    assert len(pts)==5
    expected=fitz.Font('helv').text_length('2',fontsize=8)+1.25
    assert pts[1][0]-pts[0][0]==pytest.approx(expected,abs=.001)


def test_metadata_preserves_source_and_records_actual_revision():
    doc=fitz.open(stream=make_pdf(),filetype='pdf')
    doc.set_metadata({'author':'Designer','creator':'Original Design Tool','producer':'Original PDF Tool',
                      'creationDate':"D:20200102030405+00'00'",'modDate':'D:20200102030405','title':'Brand guide'})
    kind,value=doc.xref_get_key(-1,'Info');xref=int(value.split()[0])
    doc.xref_set_key(xref,'CustomBrand','(Original brand)')
    src=doc.tobytes();out=edit_pdf(src,'Replace "2025" with "2026"')
    result=fitz.open(stream=out['pdf'],filetype='pdf')
    for name in ('author','creator','creationDate','title'):
        assert result.metadata[name]==doc.metadata[name]
    assert result.metadata['producer'].startswith('Pedit 0.2')
    assert result.metadata['modDate']!=doc.metadata['modDate']
    info=int(result.xref_get_key(-1,'Info')[1].split()[0])
    assert result.xref_get_key(info,'CustomBrand')==('string','Original brand')
    assert out['integrity']['source_sha256']==hashlib.sha256(src).hexdigest()
    assert out['integrity']['output_sha256']==hashlib.sha256(out['pdf']).hexdigest()


def test_xmp_source_values_and_custom_namespaces_survive_revision():
    doc=fitz.open(stream=make_pdf(),filetype='pdf')
    xml='''<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:brand="urn:brand" xmlns:pdf="http://ns.adobe.com/pdf/1.3/"><rdf:RDF><rdf:Description xmp:CreateDate="2020-01-02T00:00:00Z" xmp:ModifyDate="2020-01-02T00:00:00Z" pdf:Producer="Original tool"><brand:Collection>OVUM</brand:Collection></rdf:Description></rdf:RDF></x:xmpmeta>'''
    doc.set_xml_metadata(xml)
    out=edit_pdf(doc.tobytes(),'Replace "2025" with "2026"')
    result=fitz.open(stream=out['pdf'],filetype='pdf')
    root=ET.fromstring(result.get_xml_metadata())
    desc=root.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
    assert desc.get('{http://ns.adobe.com/xap/1.0/}CreateDate')=='2020-01-02T00:00:00Z'
    assert desc.get('{http://ns.adobe.com/pdf/1.3/}Producer').startswith('Pedit 0.2')
    assert root.find('.//{urn:brand}Collection').text=='OVUM'


def test_signature_field_blocks_even_when_not_overlapping_edit():
    doc=fitz.open(stream=make_pdf(),filetype='pdf')
    w=fitz.Widget();w.field_name='Approval';w.field_type=fitz.PDF_WIDGET_TYPE_SIGNATURE;w.rect=fitz.Rect(200,400,400,450)
    doc[0].add_widget(w)
    with pytest.raises(PrecisionError,match='signature'):
        edit_pdf(doc.tobytes(),'Replace "2025" with "2026"')


def test_orphan_signature_dictionary_is_detected():
    doc=fitz.open(stream=make_pdf(),filetype='pdf')
    xref=doc.get_new_xref();doc.update_object(xref,'<< /Type /Sig /ByteRange [0 100 200 300] >>')
    with pytest.raises(PrecisionError,match='signature'):
        edit_pdf(doc.tobytes(),'Replace "2025" with "2026"')


def test_stroked_text_is_refused_instead_of_silently_filled():
    doc=fitz.open();p=doc.new_page();p.insert_text((70,95),'2025',render_mode=1)
    with pytest.raises(PrecisionError,match='Stroked'):
        edit_pdf(doc.tobytes(),'Replace "2025" with "2026"')


def test_tables_branding_boxes_and_all_page_previews():
    doc=fitz.open(stream=make_pdf(second=True),filetype='pdf')
    for p in doc:
        p.draw_line((50,110),(500,110),color=(.1,.2,.3),width=.25)
        p.insert_text((70,180),'TM / Fine print 2025',fontsize=5)
        p.set_trimbox(fitz.Rect(10,10,600,780))
    src=doc.tobytes();out=edit_pdf(src,'Replace all "2025" with "2026"')
    final=fitz.open(stream=out['pdf'],filetype='pdf')
    assert out['integrity']['pages_verified']==2
    assert [x['page'] for x in out['previews']]==[1,2]
    assert all(a.trimbox==b.trimbox for a,b in zip(doc,final))
    assert len(final[0].get_drawings())==len(doc[0].get_drawings())


def test_wider_letters_use_original_tracking_instead_of_stretched_gaps():
    doc=fitz.open();p=doc.new_page();p.insert_text((70,95),'iiii',fontsize=11)
    out=edit_pdf(doc.tobytes(),'Replace "iiii" with "WWWW"')
    pts=origins(out['pdf'])
    assert pts[1][0]-pts[0][0]==pytest.approx(fitz.Font('helv').text_length('W',fontsize=11),abs=.001)
    assert out['edits'][0]['spacing_mode']=='measured uniform tracking'


def test_secondary_poppler_renderer_preserves_unaffected_pixels(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('pdftoppm'):
        pytest.skip('Poppler is not installed')
    src=make_pdf(embedded=True)
    out=edit_pdf(src,'Replace "2025" with "2026"')
    for name,data in [('source',src),('edited',out['pdf'])]:
        path=tmp_path/(name+'.pdf');path.write_bytes(data)
        subprocess.run(['pdftoppm','-r','300','-png','-singlefile',str(path),str(tmp_path/name)],check=True,capture_output=True)
    with fitz.open(stream=src,filetype='pdf') as doc:
        rect=doc[0].search_for('2025')[0]
    with Image.open(tmp_path/'source.png') as before, Image.open(tmp_path/'edited.png') as after:
        assert _validate_untouched(before.convert('RGB'),after.convert('RGB'),[rect])[0]


def test_unrelated_logo_link_annotation_and_attachment_preserved():
    doc=fitz.open(stream=make_pdf(),filetype='pdf');p=doc[0]
    image=Image.new('RGB',(30,30),(20,100,200))
    import io
    buf=io.BytesIO();image.save(buf,format='PNG')
    p.insert_image(fitz.Rect(400,60,450,100),stream=buf.getvalue())
    p.insert_link({'kind':fitz.LINK_URI,'from':fitz.Rect(70,300,200,320),'uri':'https://example.com/'})
    p.add_text_annot((400,300),'Review note')
    doc.embfile_add('brand.txt',b'Brand source data')
    doc.set_toc([[1,'Brand guide',1]])
    result=edit_pdf(doc.tobytes(),'Replace "2025" with "2026"')
    final=fitz.open(stream=result['pdf'],filetype='pdf')
    assert final[0].get_links()[0]['uri']=='https://example.com/'
    assert next(final[0].annots()).info['content']=='Review note'
    assert final.embfile_get('brand.txt')==b'Brand source data'
    assert final.get_toc()==[[1,'Brand guide',1]]


def test_inexact_font_reconstruction_is_blocked(monkeypatch):
    import engine
    monkeypatch.setattr(engine,'_resolve_font',lambda *args: ('cour',b'',fitz.Font('cour')))
    with pytest.raises(PrecisionError,match='pixel-for-pixel'):
        edit_pdf(make_pdf(text='2025'),'Replace "2025" with "2026"')


def test_untouched_page_pixel_change_is_blocked(monkeypatch):
    import engine
    apply=engine._apply
    def corrupt(page,targets,reconstruct=False):
        apply(page,targets,reconstruct)
        if not reconstruct:
            page.parent[1].insert_text((300,300),'Unexpected change')
    monkeypatch.setattr(engine,'_apply',corrupt)
    with pytest.raises(PrecisionError,match='character content'):
        edit_pdf(make_pdf(second=True),'On page 1, replace "2025" with "2026"')


@pytest.mark.parametrize('document_subject', ['rdf:about=""', ''])
def test_xmp_revision_only_updates_document_subject(document_subject):
    from integrity import XMP, PDF, RDF
    xml=f'''<x:xmpmeta xmlns:x="adobe:ns:meta/"
        xmlns:rdf="{RDF}" xmlns:xmp="{XMP}" xmlns:pdf="{PDF}" xmlns:brand="urn:brand">
      <rdf:RDF>
        <rdf:Description rdf:about="urn:source:original"
            xmp:ModifyDate="2001-01-01T00:00:00Z" pdf:Producer="Source software">
          <xmp:MetadataDate>2001-01-02T00:00:00Z</xmp:MetadataDate>
        </rdf:Description>
        <rdf:Description {document_subject} xmp:ModifyDate="2020-01-01T00:00:00Z">
          <brand:Source><rdf:Description rdf:about="" pdf:Producer="Nested software">
            <xmp:ModifyDate>1999-01-01T00:00:00Z</xmp:ModifyDate>
          </rdf:Description></brand:Source>
        </rdf:Description>
        <rdf:Description {document_subject}><pdf:Producer>Old document software</pdf:Producer></rdf:Description>
        <rdf:Description rdf:nodeID="source" xmp:ModifyDate="2002-01-01T00:00:00Z"/>
        <rdf:Description rdf:ID="resource" pdf:Producer="Other resource software"/>
      </rdf:RDF>
    </x:xmpmeta>'''
    before=ET.fromstring(xml)
    with fitz.open(stream=make_pdf(),filetype='pdf') as doc:
        doc.set_xml_metadata(xml)
        result=edit_pdf(doc.tobytes(),'Replace "2025" with "2026"')
    with fitz.open(stream=result['pdf'],filetype='pdf') as final:
        after=ET.fromstring(final.get_xml_metadata())
    rdf='{'+RDF+'}'; xmp='{'+XMP+'}'; pdf='{'+PDF+'}'
    original_descriptions=before.find(rdf+'RDF').findall(rdf+'Description')
    descriptions=after.find(rdf+'RDF').findall(rdf+'Description')
    # Compare parsed trees, independent of XML namespace-prefix serialization.
    def semantic(node):
        return (node.tag,dict(node.attrib),(node.text or '').strip(),[semantic(c) for c in node])
    for i in (0,3,4):
        assert semantic(descriptions[i])==semantic(original_descriptions[i])
    assert semantic(descriptions[1].find('{urn:brand}Source'))==semantic(original_descriptions[1].find('{urn:brand}Source'))
    assert descriptions[1].get(xmp+'ModifyDate')!='2020-01-01T00:00:00Z'
    assert descriptions[2].find(pdf+'Producer').text.startswith('Pedit 0.2')
    assert descriptions[1].find(xmp+'MetadataDate') is not None
    # Missing properties belong on the document, never the first named resource.
    assert descriptions[0].find(pdf+'Producer') is None


def test_xmp_named_subjects_only_get_separate_document_revision():
    from integrity import stamp_revision, XMP, PDF, RDF
    xml=f'''<rdf:RDF xmlns:rdf="{RDF}" xmlns:xmp="{XMP}" xmlns:pdf="{PDF}">
      <rdf:Description rdf:about="urn:source:original" xmp:ModifyDate="2001-01-01T00:00:00Z" pdf:Producer="Source software"/>
    </rdf:RDF>'''
    with fitz.open(stream=make_pdf(),filetype='pdf') as doc:
        doc.set_xml_metadata(xml)
        _,updated=stamp_revision(doc)
    root=ET.fromstring(updated)
    descriptions=root.findall('{'+RDF+'}Description')
    assert len(descriptions)==2
    assert descriptions[0].get('{'+XMP+'}ModifyDate')=='2001-01-01T00:00:00Z'
    assert descriptions[0].get('{'+PDF+'}Producer')=='Source software'
    assert descriptions[1].get('{'+RDF+'}about')==''
    assert descriptions[1].find('{'+PDF+'}Producer').text.startswith('Pedit 0.2')


@pytest.mark.parametrize('property_name', ['xmp:ModifyDate', 'xmp:MetadataDate', 'pdf:Producer'])
@pytest.mark.parametrize('representation', ['resource', 'description'])
def test_qualified_xmp_revision_updates_value_and_preserves_qualifiers(property_name,representation):
    from integrity import XMP, PDF, RDF
    qualified='<rdf:value xml:lang="en">Old revision</rdf:value><brand:Evidence>Keep this qualifier</brand:Evidence>'
    property_xml=(f'<{property_name} rdf:parseType="Resource">{qualified}</{property_name}>'
                  if representation=='resource' else
                  f'<{property_name}><rdf:Description>{qualified}</rdf:Description></{property_name}>')
    xml=f'''<rdf:RDF xmlns:rdf="{RDF}" xmlns:xmp="{XMP}" xmlns:pdf="{PDF}" xmlns:brand="urn:brand">
      <rdf:Description rdf:about="">{property_xml}</rdf:Description>
    </rdf:RDF>'''
    with fitz.open(stream=make_pdf(),filetype='pdf') as doc:
        doc.set_xml_metadata(xml)
        result=edit_pdf(doc.tobytes(),'Replace "2025" with "2026"')
    with fitz.open(stream=result['pdf'],filetype='pdf') as final:
        root=ET.fromstring(final.get_xml_metadata())
    namespace,local=property_name.split(':')
    element=root.find('./{'+RDF+'}Description/{'+({'xmp':XMP,'pdf':PDF}[namespace])+'}'+local)
    container=element if representation=='resource' else element.find('{'+RDF+'}Description')
    value=container.find('{'+RDF+'}value')
    assert value.text!='Old revision'
    assert value.text.startswith('Pedit 0.2') if namespace=='pdf' else value.text.endswith('+00:00')
    assert value.get('{http://www.w3.org/XML/1998/namespace}lang')=='en'
    assert container.find('{urn:brand}Evidence').text=='Keep this qualifier'
    assert not (element.text or '').strip()
    if representation=='resource':
        assert element.get('{'+RDF+'}parseType')=='Resource'
    else:
        assert not (container.text or '').strip()


@pytest.mark.parametrize('property_xml', [
    '<xmp:ModifyDate rdf:parseType="Resource"><brand:Evidence>Missing value</brand:Evidence></xmp:ModifyDate>',
    '<xmp:ModifyDate rdf:parseType="Resource"><rdf:value>One</rdf:value><rdf:value>Two</rdf:value></xmp:ModifyDate>',
    '<xmp:ModifyDate rdf:parseType="Resource">Mixed text<rdf:value>Old</rdf:value></xmp:ModifyDate>',
    '<xmp:ModifyDate rdf:parseType="Resource"><rdf:value rdf:resource="urn:date"/></xmp:ModifyDate>',
    '<xmp:ModifyDate rdf:parseType="Resource"><rdf:value><rdf:Bag/></rdf:value></xmp:ModifyDate>',
    '<xmp:ModifyDate rdf:resource="urn:date"/>',
])
def test_unsupported_qualified_xmp_revision_is_blocked(property_xml):
    from integrity import XMP, RDF
    xml=f'''<rdf:RDF xmlns:rdf="{RDF}" xmlns:xmp="{XMP}" xmlns:brand="urn:brand">
      <rdf:Description rdf:about="">{property_xml}</rdf:Description>
    </rdf:RDF>'''
    with fitz.open(stream=make_pdf(),filetype='pdf') as doc:
        doc.set_xml_metadata(xml)
        source=doc.tobytes()
    with pytest.raises(PrecisionError,match='Unsupported XMP revision property'):
        edit_pdf(source,'Replace "2025" with "2026"')


@pytest.mark.parametrize('dimensions',[(14400,14400),(100000,5)])
def test_oversized_or_extremely_narrow_pages_are_blocked_before_rendering(dimensions):
    from engine import validate_pdf
    with fitz.open() as doc:
        doc.new_page(width=dimensions[0],height=dimensions[1])
        source=doc.tobytes()
    with pytest.raises(PrecisionError,match='size limit'):
        validate_pdf(source)


def test_font_lookup_prefers_exact_name_before_regular_alias():
    from engine import _resolve_font
    from types import SimpleNamespace
    class Page:
        def get_fonts(self,full):
            return [(1,'n/a','Type1','Helvetica-Regular','F1',''),(2,'n/a','Type1','Helvetica','F2','')]
    name,data,font=_resolve_font([Page()],0,SimpleNamespace(font='Helvetica'))
    assert name=='helv'
