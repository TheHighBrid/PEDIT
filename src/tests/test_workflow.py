"""Source selection, extraction reuse and conservative single-glyph expansion."""
import io
import fitz
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app import app
import engine
from test_engine import make_pdf


def test_inspect_is_read_only_and_returns_exact_typography():
    source=make_pdf(second=True)
    result=engine.inspect_pdf(source,2)
    assert result['pages']==2 and result['page']==2
    assert result['spans'] and any('2025' in s['text'] for s in result['spans'])
    assert all(s['font'] and s['size']>0 and s['color'].startswith('#') for s in result['spans'])
    assert Image.open(io.BytesIO(result['preview_png'])).width>0
    assert not result['signature_present']
    assert 'pdf' not in result
    with fitz.open(stream=source,filetype='pdf') as doc:
        assert '2025' in doc[1].get_text()


@pytest.mark.parametrize('page',[0,3,True,1.5])
def test_inspect_rejects_invalid_pages(page):
    with pytest.raises(engine.PrecisionError,match='Choose a page'):
        engine.inspect_pdf(make_pdf(second=True),page)


def test_image_payloads_are_excluded_from_text_planning(monkeypatch):
    original=fitz.Page.get_text
    def checked(page, option, **kwargs):
        assert not kwargs["flags"] & fitz.TEXT_PRESERVE_IMAGES
        return original(page,option,**kwargs)
    monkeypatch.setattr(fitz.Page,"get_text",checked)
    with fitz.open(stream=make_pdf(),filetype='pdf') as doc:
        image=io.BytesIO();Image.new('RGB',(100,100),'red').save(image,format='PNG')
        doc[0].insert_image(fitz.Rect(300,300,400,400),stream=image.getvalue())
        records=list(engine._all_spans(doc[0]))
        assert records and all('image' not in s for _,_,_,s,_ in records)
        # No image block in extraction: text remains block zero even with an image.
        assert all(s['chars'] for _,_,_,s,_ in records)


def test_multiple_edits_reuse_source_extraction_and_trace(monkeypatch):
    with fitz.open() as doc:
        p=doc.new_page();p.insert_text((50,100),'2025');p.insert_text((50,200),'3035')
        source=doc.tobytes()
    calls={'spans':0,'trace':0,'fonts':0}
    spans,trace,fonts=engine._all_spans,fitz.Page.get_texttrace,engine._resolve_font
    def count_spans(page):
        calls['spans']+=1
        return spans(page)
    def count_trace(page):
        calls['trace']+=1
        return trace(page)
    def count_fonts(*args):
        calls['fonts']+=1
        return fonts(*args)
    monkeypatch.setattr(engine,'_all_spans',count_spans)
    monkeypatch.setattr(fitz.Page,'get_texttrace',count_trace)
    monkeypatch.setattr(engine,'_resolve_font',count_fonts)
    engine.edit_pdf(source,'',[{'old':'2025','new':'2026'},{'old':'3035','new':'3036'}])
    # Once for planning and once for independent post-save character validation.
    assert calls=={'spans':2,'trace':1,'fonts':1}


def test_single_glyph_can_expand_using_verified_uniform_span_tracking():
    with fitz.open() as doc:
        doc.new_page().insert_text((50,100),'Note 1',fontsize=10)
        source=doc.tobytes()
    result=engine.edit_pdf(source,'',[{'old':'1','new':'23'}])
    with fitz.open(stream=result['pdf'],filetype='pdf') as doc:
        assert 'Note 23' in doc[0].get_text()
    assert result['edits'][0]['spacing_mode']=='measured uniform tracking'


def test_isolated_glyph_expansion_still_refuses_to_guess_tracking():
    with fitz.open() as doc:
        doc.new_page().insert_text((50,100),'1')
        source=doc.tobytes()
    with pytest.raises(engine.PrecisionError,match='Variable kerning'):
        engine.edit_pdf(source,'',[{'old':'1','new':'23'}])


@pytest.mark.parametrize('text',['bad\rtext','bad\x00text','bad\ttext'])
def test_control_characters_are_rejected_before_drawing(text):
    with pytest.raises(engine.PrecisionError):
        engine.Edit('2025',text)


def test_inspection_api_uses_worker_and_handles_invalid_input():
    with TestClient(app) as client:
        files={'file':('source.pdf',make_pdf(),'application/pdf')}
        response=client.post('/api/inspect',files=files,data={'page':'1'})
        assert response.status_code==200,response.text
        assert response.json()['image'].startswith('data:image/png;base64,')
        assert client.post('/api/inspect',files=files,data={'page':'2'}).status_code==422
        assert client.post('/api/inspect',files={'file':('source.pdf',b'bad')}).status_code==422


def test_source_inspection_reports_signature_indicators_without_modifying_pdf():
    with fitz.open(stream=make_pdf(),filetype='pdf') as doc:
        widget=fitz.Widget();widget.field_name='Approval';widget.field_type=fitz.PDF_WIDGET_TYPE_SIGNATURE
        widget.rect=fitz.Rect(200,400,400,450);doc[0].add_widget(widget)
        source=doc.tobytes()
    assert engine.inspect_pdf(source)['signature_present'] is True
    with pytest.raises(engine.PrecisionError,match='signature'):
        engine.edit_pdf(source,'Replace "2025" with "2026"')
