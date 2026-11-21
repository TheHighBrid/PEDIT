"""Small regression coverage for embedded-font aliases and extracted Unicode."""
import fitz
import pytest
from pathlib import Path
from classic.engine import (
    PrecisionError, _choose_font_record, _native_glyphs_confirm_replacement,
    edit_pdf,
)


def _embedded_font():
    candidates = (
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/System/Library/Fonts/Supplemental/Arial.ttf'),
    )
    font = next((p for p in candidates if p.exists()), None)
    if font is None:
        pytest.skip('An installed font is required for this fixture')
    return font


def test_two_resource_aliases_same_embedded_object_are_not_ambiguous():
    doc = fitz.open()
    page = doc.new_page(width=460, height=190)
    font = str(_embedded_font())
    xref_a = page.insert_font(fontname='AliasA', fontfile=font)
    xref_b = page.insert_font(fontname='AliasB', fontfile=font)
    assert xref_a == xref_b
    page.insert_text((40, 90), 'POSTER DESIGN -$2.49', fontsize=12, fontname='AliasA')
    original = doc.tobytes()
    output = edit_pdf(original, structured_edits=[
        {'page': 1, 'old': 'POSTER DESIGN', 'new': 'POSTER DRAFT'},
        {'page': 1, 'old': '-$2.49', 'new': '-$3.49'},
    ], record_telemetry=False)
    with fitz.open(stream=output['pdf'], filetype='pdf') as after:
        assert 'POSTER DRAFT' in after[0].get_text()
        assert '-$3.49' in after[0].get_text()
        assert '-$2.49' not in after[0].get_text()


def _record(xref, base, resource, referrer=0):
    return (xref, 'ttf', 'TrueType', base, resource, 'WinAnsiEncoding', referrer)


def test_futura_name_variants_choose_exact_font_identity():
    names = [
        _record(6, 'Futura-PT-Book', 'F2'),
        _record(5, 'FuturaPT-Book', 'F1'),
    ]
    assert _choose_font_record(names, 'FuturaPT-Book', 0)[0] == 5
    assert _choose_font_record(names, 'Futura-PT-Book', 0)[0] == 6


def test_truly_ambiguous_distinct_font_objects_are_rejected():
    names = [_record(5, 'FuturaPT-Book', 'F1'), _record(6, 'Futura-PT-Book', 'F2')]
    with pytest.raises(PrecisionError, match='distinct embedded font objects'):
        _choose_font_record(names, 'FuturaPTBook', 0)


def test_page_font_outranks_xobject_reference():
    names = [_record(4, 'FuturaPT-Book', 'Fm1', 99),
             _record(5, 'FuturaPT-Book', 'F1', 0)]
    assert _choose_font_record(names, 'FuturaPT-Book', 0)[0] == 5


def test_selectability_glyph_inventory_is_native_and_position_locked():
    doc = fitz.open()
    page = doc.new_page(width=400, height=190)
    page.insert_text((50, 85), 'POSTER DESIGN', fontsize=12)
    origin = fitz.Point(50, 85)
    area = fitz.Rect(45, 67, 180, 96)
    assert _native_glyphs_confirm_replacement(page, 'POSTER DESIGN', origin, (1, 0), area)
    assert not _native_glyphs_confirm_replacement(page, 'POSTER DRAFT', origin, (1, 0), area)
    assert not _native_glyphs_confirm_replacement(page, 'POSTER DESIGN', fitz.Point(100, 120), (1, 0), area)


def test_selectability_never_uses_ocr_pixels_to_approve_text():
    doc = fitz.open()
    page = doc.new_page(width=400, height=190)
    page.draw_rect((40, 40, 180, 95), color=(0, 0, 0))
    assert not _native_glyphs_confirm_replacement(
        page, '-$2.49', fitz.Point(50, 85), (1, 0), fitz.Rect(40, 40, 180, 95))
