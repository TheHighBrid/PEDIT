from __future__ import annotations

import io
import os

import fitz
import pytest
from fastapi.testclient import TestClient

from app.editor import Edit, EditError, edit_pdf, parse_instructions
from app.main import app

FONT_PATH = next((p for p in [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/System/Library/Fonts/Supplemental/Arial.ttf',
    '/Library/Fonts/Arial.ttf',
    'C:/Windows/Fonts/arial.ttf',
] if os.path.isfile(p)), None)


def make_pdf(embedded=True):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    if embedded and FONT_PATH:
        page.insert_font(fontname='TESTFONT', fontfile=FONT_PATH)
        name = 'TESTFONT'
    else:
        name = 'helv'
    page.insert_text((60, 56), 'TEST DOCUMENT', fontsize=20, fontname=name, color=(0, 0, 0))
    page.insert_text((60, 115), 'Collection and Credit Officer', fontsize=11, fontname=name)
    page.insert_text((470, 115), 'Mar 2024', fontsize=11, fontname=name, color=(0.2, 0.3, 0.4))
    page.insert_text((60, 150), 'Additional content stays unchanged.', fontsize=11, fontname=name)
    data = doc.tobytes()
    doc.close()
    return data


def spans(data):
    d = fitz.open(stream=data, filetype='pdf')
    result = [s for b in d[0].get_text('dict')['blocks'] if b['type'] == 0
              for l in b['lines'] for s in l['spans']]
    d.close()
    return result


def test_parse():
    edits = parse_instructions('replace "a" with "b"\non page 2 remove "xyz"\nchange \'one\' to \'two\' align right')
    assert [e.new for e in edits] == ['b', '', 'two']
    assert edits[1].page == 2
    assert edits[2].align == 'right'
    with pytest.raises(EditError, match='ambiguous'):
        parse_instructions('fix dates in the PDF')


def test_embedded_font_color_baseline_and_untouched_content():
    before = make_pdf()
    after, report = edit_pdf(before, [Edit(old='Mar 2024', new='Mar 2025')])
    assert report['status'] == 'passed'
    assert report['changes'][0]['alignment'] == 'right'
    old = next(s for s in spans(before) if 'Mar 2024' in s['text'])
    new = next(s for s in spans(after) if 'Mar 2025' in s['text'])
    assert old['font'] == new['font']
    assert abs(old['size'] - new['size']) < 0.01
    assert old['color'] == new['color']
    assert abs(old['origin'][1] - new['origin'][1]) < 0.01
    assert abs(old['bbox'][2] - new['bbox'][2]) < 0.5
    assert 'Additional content stays unchanged.' in [s['text'] for s in spans(after)]
    assert all(r['outside_changed_percent'] <= .01 for r in report['visual_regression'])


def test_remove_text_with_original_font_and_no_reflow():
    before = make_pdf()
    after, report = edit_pdf(before, [Edit(old='TEST DOCUMENT', new='')])
    assert all('TEST DOCUMENT' not in s['text'] for s in spans(after))
    assert len(report['changes']) == 1
    assert any('Collection and Credit Officer' in s['text'] for s in spans(after))


def test_builtin_font_and_fragment_replacement():
    after, report = edit_pdf(make_pdf(embedded=False), [Edit(old='2024', new='2025')])
    assert any('2025' in s['text'] for s in spans(after))
    d = fitz.open(stream=after, filetype='pdf')
    assert 'Mar 2025' in d[0].get_text(sort=True)
    d.close()
    assert report['changes'][0]['font_size'] == 11


def test_fail_closed_missing_and_ambiguity():
    data = make_pdf()
    with pytest.raises(EditError, match='not found'):
        edit_pdf(data, [Edit(old='something else', new='abc')])
    with pytest.raises(EditError, match='does not exist'):
        edit_pdf(data, [Edit(old='Mar 2024', new='Mar 2025', page=2)])
    with pytest.raises(EditError, match='collide'):
        edit_pdf(data, [Edit(old='Collection', new='Collection officer with much longer description')])


def test_multi_edits_and_pdf_pages_unchanged():
    original = make_pdf()
    edited, report = edit_pdf(original, [Edit(old='TEST DOCUMENT', new='SAMPLE DOCUMENT'),
                                                Edit(old='Mar 2024', new='Mar 2025')])
    assert len(report['changes']) == 2
    assert {s['text'] for s in spans(edited)} >= {'SAMPLE DOCUMENT', 'Mar 2025'}
    assert report['visual_regression'][0]['outside_changed_pixels'] == 0


def test_api_serves_ui_and_returns_binary_result():
    client = TestClient(app)
    assert client.get('/').status_code == 200
    assert client.get('/api/health').json()['status'] == 'ok'
    response = client.post('/api/edit', data={'instructions': 'replace "Mar 2024" with "Mar 2025"'},
                           files={'file': ('test.pdf', make_pdf(), 'application/pdf')})
    assert response.status_code == 200, response.text
    assert response.json()['report']['status'] == 'passed'
    response = client.post('/api/edit', data={'instructions': 'change date please'},
                           files={'file': ('test.pdf', make_pdf(), 'application/pdf')})
    assert response.status_code == 422


def test_reject_non_pdf():
    with pytest.raises(EditError, match='not a PDF'):
        edit_pdf(b'abcdefg', [Edit(old='a', new='b')])


def test_specified_occurrence_does_not_destroy_other_matching_text():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 80), '2024', fontsize=12, fontname='helv')
    page.insert_text((300, 80), '2024', fontsize=12, fontname='helv')
    data = doc.tobytes()
    doc.close()
    with pytest.raises(EditError, match='occurs 2 times'):
        edit_pdf(data, [Edit(old='2024', new='2025')])
    edited, report = edit_pdf(data, [Edit(old='2024', new='2025', occurrence=2)])
    d = fitz.open(stream=edited, filetype='pdf')
    assert d[0].get_text().count('2024') == 1
    assert d[0].get_text().count('2025') == 1
    d.close()
    assert report['status'] == 'passed'


def test_new_text_containing_original_phrase_is_valid():
    before = make_pdf()
    after, report = edit_pdf(before, [Edit(old='Mar 2024', new='Mar 2024 Q4')])
    assert any('Mar 2024 Q4' in s['text'] for s in spans(after))
    assert report['status'] == 'passed'
