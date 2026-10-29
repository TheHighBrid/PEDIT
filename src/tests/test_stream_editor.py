"""Direct operator edits preserve text state and prove exact selected-glyph mapping."""
import re
import fitz
import pytest
from fastapi.testclient import TestClient
from app import app
from engine import edit_pdf, PrecisionError
from stream_editor import Lexer, StreamError


def source(body, second=False):
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_font(fontname='helv');page.insert_font(fontname='hebo')
        xref=doc.get_new_xref();doc.update_object(xref,'<<>>');doc.update_stream(xref,body)
        page.set_contents(xref)
        if second: doc.new_page().insert_text((70,100),'Untouched second page')
        return doc.tobytes()


def stream(data):
    with fitz.open(stream=data,filetype='pdf') as doc:
        return doc.xref_stream(doc[0].get_contents()[0])


def test_mixed_font_phrase_keeps_fonts_and_original_operators():
    body=b'q BT 1 0 0 1 70 700 Tm /helv 11 Tf (20) Tj /hebo 11 Tf (25) Tj ET Q'
    data=source(body,second=True)
    with pytest.raises(PrecisionError,match='continuous editable text'):
        edit_pdf(data,'Replace "2025" with "2026"')
    result=edit_pdf(data,'Replace "2025" with "2026"',mode='stream')
    assert stream(result['pdf'])==body.replace(b'(20)',b'<3230>').replace(b'(25)',b'<3236>')
    assert [e['font'] for e in result['edits']]==['Helvetica','Helvetica-Bold']
    assert all(e['edit_method']=='content-stream' for e in result['edits'])
    assert result['integrity']['pages_verified']==2


def test_irregular_tj_and_horizontal_scale_are_retained_byte_for_byte():
    body=b'q BT /helv 11 Tf 75 Tz 1 0 0 1 70 700 Tm [(2) 37 (0) -83 (2) 19 (5)] TJ ( tail) Tj ET Q'
    data=source(body)
    result=edit_pdf(data,'Replace "2025" with "2026"',mode='stream')
    after=stream(result['pdf'])
    assert after==body.replace(b'(2)',b'<32>').replace(b'(0)',b'<30>').replace(b'(5)',b'<36>')
    assert b'75 Tz' in after and b'37' in after and b'-83' in after


def test_only_selected_duplicate_occurrence_is_changed():
    body=b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj 0 -40 Td (2025) Tj ET'
    result=edit_pdf(source(body),'',[{'old':'2025','new':'2026','occurrence':2}],mode='stream')
    assert stream(result['pdf'])==body.replace(b'(2025) Tj ET',b'<32303236> Tj ET')


def test_two_separate_edits_share_one_string_without_losing_either():
    body=b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025 and 3035) Tj ET'
    result=edit_pdf(source(body),'',[{'old':'2025','new':'2026'},{'old':'3035','new':'3036'}],mode='stream')
    assert b'<3230323620616e642033303336>' in stream(result['pdf'])


def test_literal_escapes_comments_and_non_text_strings_are_not_confused():
    body=b'% (2025) Tj ignored\nBT /helv 11 Tf 1 0 0 1 70 700 Tm (20\\0625) Tj ET /Tag << /Note (2025) >> BDC EMC'
    result=edit_pdf(source(body),'Replace "2025" with "2026"',mode='stream')
    expected=body.replace(b'(20\\0625)',b'<32303236>')
    assert stream(result['pdf'])==expected


@pytest.mark.parametrize('body',[
    b'BT /helv 11 Tf 1 0 0 1 70 700 Tm <3230 3235> Tj ET',
    b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) \' ET',
    b'BT /helv 11 Tf 1 0 0 1 70 700 Tm 0 0 (2025) " ET',
    b'BT /helv 11 Tf 1 0 0 1 70 700 Tm [(20) 0 (25)] TJ ET',
])
def test_text_show_operator_variants(body):
    result=edit_pdf(source(body),'Replace "2025" with "2026"',mode='stream')
    with fitz.open(stream=result['pdf'],filetype='pdf') as doc:
        assert '2026' in doc[0].get_text()


def test_graphics_state_restores_font_across_text_objects():
    body=b'/helv 11 Tf q /hebo 11 Tf BT 1 0 0 1 70 700 Tm (3035) Tj ET Q BT 1 0 0 1 70 650 Tm (2025) Tj ET'
    result=edit_pdf(source(body),'Replace "2025" with "2026"',mode='stream')
    assert b'(3035)' in stream(result['pdf'])
    assert result['edits'][0]['font']=='Helvetica'


@pytest.mark.parametrize('replacement',['20260','','WWWW'])
def test_length_or_width_changes_are_refused(replacement):
    with pytest.raises(PrecisionError,match='equal-length|matching glyph advances'):
        edit_pdf(source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj ET'),'',
                 [{'old':'2025','new':replacement}],mode='stream')


def test_shared_page_content_is_refused_before_mutation():
    data=source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj ET')
    with fitz.open(stream=data,filetype='pdf') as doc:
        xref=doc[0].get_contents()[0]
        doc.new_page().set_contents(xref)
        data=doc.tobytes()
    with pytest.raises(PrecisionError,match='Shared content'):
        edit_pdf(data,'On page 1 replace "2025" with "2026"',mode='stream')


def test_embedded_or_custom_encoding_is_not_silently_mapped():
    data=source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj ET')
    with fitz.open(stream=data,filetype='pdf') as doc:
        font=doc[0].get_fonts()[0][0]
        doc.xref_set_key(font,'Encoding','<< /BaseEncoding /WinAnsiEncoding /Differences [53 /six] >>')
        data=doc.tobytes()
    with pytest.raises(PrecisionError,match='unsupported font encoding'):
        edit_pdf(data,'Replace "2026" with "2027"',mode='stream')


def test_conflicting_selections_are_refused():
    with pytest.raises(PrecisionError,match='overlap'):
        edit_pdf(source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj ET'),'',
            [{'old':'2025','new':'2026'},{'old':'2025','new':'2027'}],mode='stream')


@pytest.mark.parametrize('raw,expected',[
    (b'(a\\(b\\)c)',b'a(b)c'),(b'(a(b)c)',b'a(b)c'),
    (b'(a\\\r\nb)',b'ab'),(b'(a\r\nb)',b'a\nb'),
    (b'(\\060\\061\\062)',b'012'),(b'<32303>',b'20\x30'),
])
def test_lexer_strings(raw,expected):
    lexer=Lexer(raw);token=lexer.token()
    assert token.kind=='string' and token.value==expected and lexer.token() is None


@pytest.mark.parametrize('raw',[b'(unterminated',b'<ZZ>',b'[(text)>>',b'<123',b'/bad#XX'])
def test_lexer_refuses_malformed_tokens(raw):
    with pytest.raises(StreamError): Lexer(raw).token()


def test_stream_mode_api_and_report():
    data=source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj ET')
    with TestClient(app) as client:
        response=client.post('/api/edit',files={'file':('source.pdf',data)},
                             data={'instructions':'Replace "2025" with "2026"','mode':'stream'})
        assert response.status_code==200,response.text
        result=response.json()
        assert 'operators retained' in result['validation']
        assert client.get(result['report_url']).json()['edits'][0]['edit_method']=='content-stream'
        assert client.post('/api/edit',files={'file':('source.pdf',data)},
                           data={'instructions':'Replace "2025" with "2026"','mode':'unknown'}).status_code==422


def test_position_anchor_selects_exact_span_even_with_overlapping_text_matches():
    body=b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2) Tj /hebo 11 Tf (22) Tj ET'
    data=source(body)
    with fitz.open(stream=data,filetype='pdf') as doc:
        origin=list(doc[0].get_text('rawdict')['blocks'][0]['lines'][0]['spans'][1]['chars'][0]['origin'])
    result=edit_pdf(data,'',[{'old':'22','new':'33','page':1,'origin':origin}],mode='stream')
    assert stream(result['pdf'])==body.replace(b'(22)',b'<3333>')
    assert result['replacement_count']==1


@pytest.mark.parametrize('origin',[[float('nan'),0],[True,0],[1],['1',0]])
def test_invalid_origin_anchor_is_rejected(origin):
    with pytest.raises(PrecisionError,match='origin anchor'):
        edit_pdf(source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj ET'),'',
                 [{'old':'2025','new':'2026','page':1,'origin':origin}],mode='stream')


@pytest.mark.parametrize('paint',[b'1 Tr',b'4 Tr',b'/External gs'])
def test_unsupported_paint_states_fail_closed(paint):
    with pytest.raises(PrecisionError,match='paint|graphics state'):
        edit_pdf(source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm '+paint+b' (2025) Tj ET'),
                 'Replace "2025" with "2026"',mode='stream')


def test_serialization_proof_detects_a_broken_string_writer(monkeypatch):
    import stream_editor
    apply=stream_editor.apply
    def broken(doc,patches):
        # A broken writer corrupting source text during round-trip proof.
        changed=[(xref,start,end,b'<32303237>') for xref,start,end,value in patches]
        apply(doc,changed)
    monkeypatch.setattr(stream_editor,'apply',broken)
    with pytest.raises(PrecisionError,match='pixel-for-pixel'):
        edit_pdf(source(b'BT /helv 11 Tf 1 0 0 1 70 700 Tm (2025) Tj ET'),
                 'Replace "2025" with "2026"',mode='stream')


def test_native_output_preserves_unaffected_pixels_in_poppler(tmp_path):
    import shutil
    import subprocess
    from PIL import Image
    from engine import _validate_untouched
    if not shutil.which('pdftoppm'): pytest.skip('Poppler is not installed')
    body=b'q 0.2 0.4 0.6 RG 70 680 m 450 680 l S BT /helv 11 Tf 75 Tz 1 0 0 1 70 700 Tm [(2) 37 (0) -83 (2) 19 (5)] TJ ET Q'
    data=source(body)
    result=edit_pdf(data,'Replace "2025" with "2026"',mode='stream')
    with fitz.open(stream=data,filetype='pdf') as doc:
        rect=doc[0].search_for('2025')[0]
    for name,pdf in [('source',data),('output',result['pdf'])]:
        path=tmp_path/(name+'.pdf');path.write_bytes(pdf)
        subprocess.run(['pdftoppm','-r','300','-png','-singlefile',str(path),str(tmp_path/name)],check=True,capture_output=True)
    with Image.open(tmp_path/'source.png') as before, Image.open(tmp_path/'output.png') as after:
        assert _validate_untouched(before.convert('RGB'),after.convert('RGB'),[rect])[0]
