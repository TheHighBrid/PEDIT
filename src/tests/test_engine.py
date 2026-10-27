import fitz
import pytest
from pathlib import Path
from engine import edit_pdf, PrecisionError, parse_instructions


def make_pdf(text='Invoice 2025 / 0081', second=False, embedded=False):
    doc=fitz.open()
    for i in range(2 if second else 1):
        p=doc.new_page(width=612,height=792)
        p.draw_rect((50,50,510,220),color=(.2,.3,.5),fill=(.93,.95,.9))
        if embedded:
            candidates=[Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/System/Library/Fonts/Supplemental/Arial.ttf'), Path('/Library/Fonts/Arial.ttf')]
            available=next((f for f in candidates if f.exists()),None)
            if available is None: pytest.skip('No suitable embedded-font fixture installed')
            p.insert_font(fontname='designer',fontfile=str(available))
            p.insert_text((70,95),text,fontsize=11,fontname='designer',color=(.2,.3,.4))
        else:
            p.insert_text((70,95),text,fontsize=11,fontname='helv',color=(.2,.3,.4))
        p.insert_text((70,137),'Do not touch this line.',fontsize=12,fontname='hebo')
    return doc.tobytes()


def test_parser():
    e=parse_instructions('On page 2, replace "Draft" with "Final"')
    assert (e[0].old,e[0].new,e[0].page)==('Draft','Final',2)


def test_single_replacement_and_render_validation():
    output=edit_pdf(make_pdf(),'Replace "2025" with "2026"')
    doc=fitz.open(stream=output['pdf'],filetype='pdf')
    assert '2026' in doc[0].get_text()
    assert '2025' not in doc[0].get_text()
    assert 'Do not touch this line.' in doc[0].get_text()
    assert len(output['edits'])==1
    assert output['before_png']!=output['after_png']


def test_embedded_font():
    output=edit_pdf(make_pdf(embedded=True),'Replace "2025" with "2026"')
    doc=fitz.open(stream=output['pdf'],filetype='pdf')
    assert '2026' in doc[0].get_text()
    assert '2025' not in doc[0].get_text()
    assert output['edits'][0]['font']


def test_scanned_rejected():
    doc=fitz.open();p=doc.new_page();p.draw_rect((10,20,150,90),fill=(1,1,0))
    with pytest.raises(PrecisionError,match='Could not find'):
        edit_pdf(doc.tobytes(),'Replace "hello" with "world"')


def test_repeated_requires_explicit_choice():
    with pytest.raises(PrecisionError,match='appears 2 times'):
        edit_pdf(make_pdf(second=True),'Replace "2025" with "2026"')
    output=edit_pdf(make_pdf(second=True),'On page 2, replace "2025" with "2026"')
    doc=fitz.open(stream=output['pdf'],filetype='pdf')
    assert '2025' in doc[0].get_text() and '2026' in doc[1].get_text()


def test_replace_all():
    out=edit_pdf(make_pdf(second=True),'Replace all "2025" with "2026"')
    assert len(out['edits'])==2


def test_overlong_replacement_rejected():
    with pytest.raises(PrecisionError,match='collide'):
        edit_pdf(make_pdf(),'Replace "2025" with "THIS IS A VERY LONG PIECE OF TEXT"')


def test_no_silent_fallback_for_nonexistent_glyph():
    with pytest.raises(PrecisionError,match='lacks glyph'):
        edit_pdf(make_pdf(),'Replace "2025" with "🎃"')


def test_does_not_modify_source_file():
    original=make_pdf()
    source=bytes(original)
    edit_pdf(original,'Replace "2025" with "2026"')
    assert original==source


def test_overlapping_edits_rejected():
    with pytest.raises(PrecisionError,match='overlap'):
        edit_pdf(make_pdf(),'Replace "2025" with "2026"\nReplace "2025" with "2027"')


def test_original_font_size_color_and_baseline():
    src=make_pdf()
    out=edit_pdf(src,'Replace "2025" with "2026"')
    original=fitz.open(stream=src,filetype='pdf')[0]
    final=fitz.open(stream=out['pdf'],filetype='pdf')[0]
    def find(page,token):
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line.get('spans',[]):
                    text=''.join(ch['c'] for ch in span['chars'])
                    if token in text:
                        i=text.index(token)
                        return span,span['chars'][i]['origin']
        raise ValueError('not found')
    a, old_origin=find(original,'2025')
    b, new_origin=find(final,'2026')
    assert a['font']==b['font']
    assert a['size']==b['size']
    assert a['color']==b['color']
    assert max(abs(x-y) for x,y in zip(old_origin,new_origin))<0.01
