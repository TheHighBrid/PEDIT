"""Exercise the approved editor, revision integrity and remote access on synthetic drafts."""
import hashlib
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from classic.app import app
from classic.engine import PrecisionError, edit_pdf, inspect_pdf
from classic.provenance import REVISION_LABEL


def draft(text='Draft poster', *, angle=0, font='helv', color=(0, 0, 0)):
    doc = fitz.open()
    page = doc.new_page(width=480, height=400)
    anchor = (100, 240) if angle == 90 else (100, 100)
    page.insert_text(anchor, text, fontsize=16, fontname=font, color=color, rotate=angle)
    doc.set_metadata({'author': 'Test author', 'producer': 'Source generator'})
    return doc


def find_span(page, text):
    return next((span, line['dir']) for block in page.get_text('dict')['blocks']
                for line in block.get('lines', []) for span in line['spans'] if span['text'] == text)


@pytest.mark.parametrize('angle', [90, 270])
@pytest.mark.parametrize('color', [(0, 0, 0), (0.7, 0.1, 0.2)])
def test_vertical_orientation_and_color(angle, color):
    doc = draft('VERTICAL', angle=angle, color=color)
    before, direction = find_span(doc[0], 'VERTICAL')
    result = edit_pdf(doc.tobytes(), 'Replace "VERTICAL" with "UPDATED"')
    with fitz.open('pdf', result['pdf']) as revised:
        after, after_direction = find_span(revised[0], 'UPDATED')
        assert after_direction == direction
        assert (after['font'], after['size'], after['color']) == (before['font'], before['size'], before['color'])


@pytest.mark.parametrize('font', ['hebo', 'heit', 'hebi'])
def test_existing_font_style(font):
    doc = draft('DRAFT', font=font)
    before, _ = find_span(doc[0], 'DRAFT')
    output = edit_pdf(doc.tobytes(), 'Replace "DRAFT" with "FINAL"')
    with fitz.open('pdf', output['pdf']) as revised:
        after, _ = find_span(revised[0], 'FINAL')
        assert after['font'] == before['font']
        assert after['flags'] == before['flags']


def test_revised_copy_provenance_and_reedit():
    doc = draft()
    doc.new_page(width=480, height=400)
    original = doc.tobytes()
    first = edit_pdf(original, 'Replace "Draft poster" with "Final poster"')
    with fitz.open('pdf', first['pdf']) as revised:
        assert all(REVISION_LABEL in page.get_text() for page in revised)
        assert revised.metadata['author'] == 'Test author'
        assert revised.metadata['producer'] != 'Source generator'
        assert revised.metadata['modDate']
    assert first['integrity']['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert first['integrity']['output_sha256'] == hashlib.sha256(first['pdf']).hexdigest()
    next_copy = edit_pdf(first['pdf'], 'Replace "Final poster" with "New poster"')
    with fitz.open('pdf', next_copy['pdf']) as revised:
        assert revised[0].get_text().count(REVISION_LABEL) == 1
    with pytest.raises(PrecisionError, match='protected'):
        edit_pdf(first['pdf'], f'On page 1, replace "{REVISION_LABEL}" with "Original"')


def test_signed_draft_refused():
    doc = draft()
    w = fitz.Widget()
    w.field_name = 'Approval'
    w.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
    w.rect = fitz.Rect(200, 200, 350, 240)
    doc[0].add_widget(w)
    with pytest.raises(PrecisionError, match='Signed or certified'):
        edit_pdf(doc.tobytes(), 'Replace "Draft" with "Final"')


def test_remote_requires_auth_everywhere(monkeypatch):
    monkeypatch.setenv('PEDIT_REMOTE_PASSWORD', 'test-only-password')
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        for endpoint in ('/', '/static/index.html', '/api/download/abc', '/api/kernel/status'):
            assert client.get(endpoint).status_code == 401
        assert client.post('/api/inspect').status_code == 401
        assert client.post('/api/edit').status_code == 401
        assert client.get('/', auth=('pedit', 'bad')).status_code == 401
        response = client.get('/', auth=('pedit', 'test-only-password'))
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert 'cell-box' in response.text and 'slot-box' in response.text


def test_classic_http_upload_edit_download(monkeypatch):
    monkeypatch.delenv('PEDIT_REMOTE_PASSWORD', raising=False)
    data = draft().tobytes()
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/', headers={'Host': 'untrusted.example'}).status_code == 403
        assert client.get('/', headers={'CF-Connecting-IP': '203.0.113.5'}).status_code == 403
        response = client.post('/api/inspect', files={'file': ('draft.pdf', data, 'application/pdf')})
        assert response.status_code == 200, response.text
        body = response.json()
        assert any(s['text'] == 'Draft poster' for s in body['pages'][0]['spans'])
        result = client.post('/api/edit', data={'session_token': body['session_token'],
                                               'instructions': 'Replace "Draft poster" with "Final poster"'})
        assert result.status_code == 200, result.text
        downloaded = client.get(result.json()['download_url'])
        assert downloaded.status_code == 200
        with fitz.open('pdf', downloaded.content) as revised:
            assert 'Final poster' in revised[0].get_text()
            assert REVISION_LABEL in revised[0].get_text()


def test_arabic_shaping_pending():
    doc = fitz.open()
    page = doc.new_page(width=480, height=400)
    page.insert_htmlbox((70, 80, 370, 145), '<p dir="rtl" style="font-size:16pt">مرحبا</p>')
    data = doc.tobytes()
    target = inspect_pdf(data)['pages'][0]['spans'][0]
    # A safe failure remains explicit until contextual shaping and extraction are implemented.
    with pytest.raises(PrecisionError):
        edit_pdf(data, structured_edits=[{'page': 1, 'old': target['text'], 'new': 'أهلا', 'bbox': target['bbox']}])


def test_french_and_literal_spacing():
    doc = draft('Café été')
    revised = edit_pdf(doc.tobytes(), 'Replace "Café été" with "École à Noël"')
    with fitz.open('pdf', revised['pdf']) as out:
        assert 'École à Noël' in out[0].get_text()
    doc = draft('A B C')
    revised = edit_pdf(doc.tobytes(), 'Replace "A B C" with "X Y Z"')
    with fitz.open('pdf', revised['pdf']) as out:
        assert 'X Y Z' in out[0].get_text()


def test_unicode_symbols_with_embedded_font():
    font_path = next((p for p in (
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/System/Library/Fonts/Supplemental/Arial.ttf'),
    ) if p.exists()), None)
    if font_path is None:
        pytest.skip('No suitable installed font for this fixture')
    doc = fitz.open()
    page = doc.new_page(width=480, height=400)
    page.insert_font(fontname='design', fontfile=str(font_path))
    page.insert_text((70, 100), 'Café ©', fontsize=16, fontname='design')
    output = edit_pdf(doc.tobytes(), 'Replace "Café ©" with "Noël ®"')
    with fitz.open('pdf', output['pdf']) as revised:
        assert 'Noël ®' in revised[0].get_text()


@pytest.mark.parametrize('offset', [3, -6])
def test_existing_decoration_line_not_removed(offset):
    doc = draft('Short')
    page = doc[0]
    page.draw_line((100, 100 + offset), (138, 100 + offset), width=.6)
    expected = page.get_drawings()[0]['rect']
    output = edit_pdf(doc.tobytes(), 'Replace "Short" with "Longer"')
    with fitz.open('pdf', output['pdf']) as revised:
        assert revised[0].get_drawings()[0]['rect'] == expected


def test_underlined_annotation_requires_review():
    doc = draft('Short')
    page = doc[0]
    page.add_underline_annot(page.search_for('Short'))
    with pytest.raises(PrecisionError, match='overlaps an annotation'):
        edit_pdf(doc.tobytes(), 'Replace "Short" with "Longer"')
