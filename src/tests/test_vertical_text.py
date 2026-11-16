"""Rotated editable text must preserve the source paint and writing direction."""

import fitz
import pytest

from engine import PrecisionError, edit_pdf


def make_vertical_pdf(rotation=90, color=(0, 0, 0), text='041806'):
    doc = fitz.open()
    page = doc.new_page(width=280, height=420)
    page.insert_text((140, 320), text, fontsize=11, fontname='helv',
                     color=color, rotate=rotation)
    page.insert_text((15, 390), 'Reference text', fontsize=9, color=(0.2, 0.3, 0.4))
    return doc.tobytes()


def find_span(page, target):
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                if target in ''.join(c['c'] for c in span['chars']):
                    return span, line
    raise AssertionError(f'Missing text {target!r}')


@pytest.mark.parametrize('rotation', [90, 180, 270])
@pytest.mark.parametrize('color', [(0, 0, 0), (0.1, 0.2, 0.3)])
def test_rotated_text_retains_original_color_font_and_direction(rotation, color):
    src = make_vertical_pdf(rotation, color)
    out = edit_pdf(src, 'Replace "041806" with "041808"')
    with fitz.open(stream=src, filetype='pdf') as original, fitz.open(stream=out['pdf'], filetype='pdf') as edited:
        before, old_line = find_span(original[0], '041806')
        after, new_line = find_span(edited[0], '041808')
        assert before['color'] == after['color']
        assert before['font'] == after['font']
        assert before['size'] == after['size']
        assert new_line['dir'] == pytest.approx(old_line['dir'], abs=1e-5)
        assert after['chars'][0]['origin'] == pytest.approx(before['chars'][0]['origin'], abs=0.01)
        assert edited[0].get_text().count('041808') == 1
    assert out['edits'][0]['font_color'] == f"#{before['color']:06X}"


def test_rotated_partial_text_change_does_not_color_shift_neighbors():
    src = make_vertical_pdf(90, (0, 0, 0), 'SBSAV16000_8158468_002_0310541_041806')
    out = edit_pdf(src, 'Replace "041806" with "041808"')
    with fitz.open(stream=out['pdf'], filetype='pdf') as edited:
        span, _ = find_span(edited[0], '041808')
        assert span['color'] == 0
        assert '041808' in edited[0].get_text()
        assert '041806' not in edited[0].get_text()


def test_unsupported_diagonal_text_is_refused_not_redrawn():
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((150, 250), '041806', morph=(fitz.Point(150, 250), fitz.Matrix(0.866, 0.5, -0.5, 0.866, 0, 0)))
    with pytest.raises(PrecisionError, match='rotated|orthogonal|transformed'):
        edit_pdf(doc.tobytes(), 'Replace "041806" with "041808"')


@pytest.mark.parametrize('rotation', [90, 270])
def test_vertical_length_change_uses_baseline_advances(rotation):
    src = make_vertical_pdf(rotation, (0, 0, 0))
    out = edit_pdf(src, 'Replace "041806" with "0418081"')
    assert out['edits'][0]['spacing_mode'] == 'measured uniform tracking'
    with fitz.open(stream=out['pdf'], filetype='pdf') as edited:
        new_span, new_line = find_span(edited[0], '0418081')
        assert new_span['color'] == 0
        x0, y0 = new_span['chars'][0]['origin']
        x1, y1 = new_span['chars'][1]['origin']
        assert abs(x1-x0) < .001
        assert (y1 < y0) == (rotation == 90)


def test_rotated_page_horizontal_text_preserves_black_color():
    doc = fitz.open()
    page = doc.new_page(width=250, height=350)
    page.insert_text((50, 120), '041806', fontsize=12, color=(0, 0, 0))
    page.set_rotation(90)
    src = doc.tobytes()
    result = edit_pdf(src, 'Replace "041806" with "041808"')
    with fitz.open(stream=result['pdf'], filetype='pdf') as edited:
        span, _ = find_span(edited[0], '041808')
        assert span['color'] == 0
        assert edited[0].rotation == 90
