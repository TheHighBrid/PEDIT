"""Conservative, geometry-validated PDF text replacements.

This engine intentionally refuses unsupported edits instead of silently altering layout.
It preserves every untouched page object and reuses font programs where feasible.
"""
from __future__ import annotations

import dataclasses
import io
import math
import re
from typing import Any, Literal

import fitz
from PIL import Image, ImageChops, ImageDraw

MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PAGES = 40
MAX_EDITS = 30
MAX_PAGE_AREA_PT2 = 10_000_000
MAX_RENDER_PIXELS = 20_000_000
MAX_RENDER_SIDE = 16_384


class EditError(ValueError):
    """A user-fixable precision or input error."""


@dataclasses.dataclass(frozen=True)
class Edit:
    old: str
    new: str
    page: int | None = None  # 1-based
    occurrence: int | None = None  # 1-based; explicit when target repeats
    align: Literal["auto", "left", "right"] = "auto"

    def __post_init__(self):
        if not isinstance(self.old, str) or not isinstance(self.new, str):
            raise EditError('Edit fields old and new must be strings.')
        for name in ('page', 'occurrence'):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise EditError(f'Edit field {name} must be a positive integer or null.')
        if not isinstance(self.align, str) or self.align not in ('auto', 'left', 'right'):
            raise EditError('Edit alignment must be auto, left, or right.')


@dataclasses.dataclass
class Match:
    edit: Edit
    page_index: int
    block_index: int
    line_index: int
    span_index: int
    char_start: int
    char_end: int
    selected: list[dict]
    span: dict
    line: dict
    font_xref: int | None = None
    font_name: str = ""
    font_data: bytes | None = None
    font_obj: Any = None
    anchor: str = "left"
    start_x: float = 0
    baseline_y: float = 0
    width: float = 0
    rect: fitz.Rect | None = None
    new_rect: fitz.Rect | None = None


# Text-only instructions are deliberately strict: quoted source and replacement
# prevent dangerous natural-language guesses on legal / financial documents.
RULE = re.compile(
    r'''^\s*(?:on\s+page\s+(?P<page>\d+)\s*[:,]?\s*)?'''
    r'''(?:replace|change)\s+(?P<q>["'])(?P<old>.*?)(?P=q)'''
    r'''\s+(?:with|to)\s+(?P<q2>["'])(?P<new>.*?)(?P=q2)'''
    r'''(?:\s+align\s+(?P<align>left|right))?\s*\.?\s*$''',
    re.IGNORECASE,
)
REMOVE = re.compile(
    r'''^\s*(?:on\s+page\s+(?P<page>\d+)\s*[:,]?\s*)?'''
    r'''(?:remove|delete)\s+(?P<q>["'])(?P<old>.*?)(?P=q)\s*\.?\s*$''',
    re.IGNORECASE,
)


def parse_instructions(instructions: str) -> list[Edit]:
    edits: list[Edit] = []
    for number, raw in enumerate(instructions.splitlines(), 1):
        if not raw.strip():
            continue
        m = RULE.fullmatch(raw) or REMOVE.fullmatch(raw)
        if not m:
            raise EditError(
                f'Instruction line {number} is ambiguous. Use: replace "old text" with "new text", '
                'on page 2 replace "old" with "new", or remove "text".'
            )
        d = m.groupdict()
        edits.append(Edit(old=d['old'], new=d.get('new') or '',
                          page=int(d['page']) if d.get('page') else None,
                          align=(d.get('align') or 'auto').lower()))
    return edits


def _document(data: bytes) -> fitz.Document:
    if not data or len(data) > MAX_PDF_BYTES:
        raise EditError('Upload a PDF smaller than 12 MB.')
    if not data.lstrip().startswith(b'%PDF-'):
        raise EditError('This file is not a PDF. Screenshots cannot preserve original PDF text/font objects.')
    try:
        doc = fitz.open(stream=data, filetype='pdf')
    except Exception as exc:
        raise EditError('Cannot open this PDF. The file may be damaged.') from exc
    if doc.needs_pass:
        doc.close()
        raise EditError('Password-protected PDFs must be unlocked before editing.')
    if not 1 <= len(doc) <= MAX_PAGES:
        doc.close()
        raise EditError('PDF must have between 1 and 40 pages.')
    try:
        for page in doc:
            _check_page_size(page)
    except EditError:
        doc.close()
        raise
    return doc


def _span_matches(doc: fitz.Document, edit: Edit) -> list[Match]:
    if edit.page is not None and not 1 <= edit.page <= len(doc):
        raise EditError(f'Page {edit.page} does not exist (document has {len(doc)} pages).')
    matches: list[Match] = []
    pages = [edit.page - 1] if edit.page is not None else range(len(doc))
    for pi in pages:
        page = doc[pi]
        structure = page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT)
        for bi, block in enumerate(structure['blocks']):
            if block.get('type') != 0:
                continue
            for li, line in enumerate(block['lines']):
                for si, span in enumerate(line['spans']):
                    chars = span.get('chars', [])
                    text = ''.join(c['c'] for c in chars)
                    start = 0
                    while True:
                        index = text.find(edit.old, start)
                        if index < 0:
                            break
                        matches.append(Match(edit, pi, bi, li, si, index,
                                             index + len(edit.old), chars[index:index + len(edit.old)],
                                             span, line))
                        start = index + max(1, len(edit.old))
    return matches


def _get_font(doc: fitz.Document, page: fitz.Page, span_font: str):
    # Exact resource identity takes precedence over any family-name similarity.
    # Family prefixes are unsafe: Helvetica and Helvetica-Bold are different fonts.
    def norm(name):
        return re.sub(r'[^a-z0-9]', '', re.sub(r'^[A-Z]{6}\+', '', name).lower())
    target = norm(span_font)
    fonts = page.get_fonts(full=True)
    candidates = [record for record in fonts if target and norm(record[3]) == target]
    if not candidates and target:
        # Some producers expose a Book/Regular suffix only on the font resource.
        # Normalize those style-neutral suffixes, never family/weight prefixes.
        regular = lambda name: re.sub(r'(book|regular)$', '', norm(name))
        candidates = [record for record in fonts
                      if regular(record[3]) and regular(record[3]) == regular(span_font)]
    # Repeated references to the very same font xref do not create ambiguity.
    candidates = list({record[0]: record for record in candidates}.values())
    if len(candidates) > 1:
        raise EditError(f'Original font "{span_font}" matches several resources. No substitution was made.')
    for xref, ext, _ftype, basefont, resource, *_ in candidates:
        normalized = norm(basefont)
        if ext != 'n/a':
            _, extension, _, data = doc.extract_font(xref)
            if not data:
                continue
            try:
                return (xref, resource, data, fitz.Font(fontbuffer=data), extension)
            except Exception:
                continue
        base14 = {
            'helvetica': 'helv', 'helveticabold': 'hebo',
            'helveticaoblique': 'heit', 'helveticaboldoblique': 'hebi',
            'timesroman': 'tiro', 'timesbold': 'tibo',
            'timesitalic': 'tiit', 'timesbolditalic': 'tibi',
            'courier': 'cour', 'courierbold': 'cobo',
            'courieroblique': 'coit', 'courierboldoblique': 'cobi',
        }
        alias = base14.get(normalized)
        if alias:
            return (xref, alias, None, fitz.Font(fontname=alias), 'base14')
    raise EditError(f'Original font "{span_font}" cannot be safely re-embedded. No substitution was made.')


def _bbox_union(chars: list[dict]) -> fitz.Rect:
    rect = fitz.Rect(chars[0]['bbox'])
    for c in chars[1:]:
        rect |= fitz.Rect(c['bbox'])
    return rect


def _prepare(doc: fitz.Document, edits: list[Edit]) -> list[Match]:
    if not edits:
        raise EditError('Add at least one replacement or removal instruction.')
    if len(edits) > MAX_EDITS:
        raise EditError('Maximum 30 edits per document.')
    selected: list[Match] = []
    for i, edit in enumerate(edits, 1):
        if edit.old == edit.new:
            raise EditError(f'Edit {i}: original and replacement are identical; no change was made.')
        if not edit.old or '\n' in edit.old or '\n' in edit.new:
            raise EditError(f'Edit {i}: source and replacement must be single-line text; source cannot be empty.')
        if edit.align not in ('auto', 'left', 'right'):
            raise EditError(f'Edit {i}: alignment must be auto, left, or right.')
        found = _span_matches(doc, edit)
        if not found:
            raise EditError(f'Edit {i}: exact text "{edit.old}" not found in one PDF text span. '
                            'Scanned, outlined, fragmented, or misspelled text cannot be edited in safe mode.')
        if edit.occurrence is not None:
            if not 1 <= edit.occurrence <= len(found):
                raise EditError(f'Edit {i}: occurrence {edit.occurrence} is out of range ({len(found)} matches).')
            chosen = found[edit.occurrence - 1]
        elif len(found) > 1:
            pages = ', '.join(str(m.page_index + 1) for m in found[:8])
            raise EditError(f'Edit {i}: "{edit.old}" occurs {len(found)} times (pages {pages}). '
                            'Specify a page or occurrence; Pedit will not guess.')
        else:
            chosen = found[0]
        if tuple(round(v, 3) for v in chosen.line.get('dir', (1, 0))) != (1.0, 0.0):
            raise EditError(f'Edit {i}: rotated/skewed PDF text is not supported by the precision engine yet.')
        if chosen.span.get('alpha', 255) != 255:
            raise EditError(f'Edit {i}: text transparency cannot yet be preserved with certainty.')
        page = doc[chosen.page_index]
        xref, font_name, font_data, font, _ = _get_font(doc, page, chosen.span['font'])
        for char in edit.new:
            if not font.has_glyph(ord(char), fallback=0):
                raise EditError(f'Edit {i}: original font does not contain glyph {char!r}; '
                                'changing font would compromise fidelity.')
        actual_width = chosen.selected[-1]['bbox'][2] - chosen.selected[0]['origin'][0]
        measured = font.text_length(edit.old, fontsize=chosen.span['size'])
        if abs(actual_width - measured) > max(0.9, 0.025 * actual_width):
            raise EditError(f'Edit {i}: original PDF has custom character spacing/metrics '
                            f'(actual {actual_width:.2f} pt; font {measured:.2f} pt). '
                            'Precision mode will not recreate different tracking.')
        chosen.font_xref, chosen.font_name = xref, font_name
        chosen.font_data, chosen.font_obj = font_data, font
        chosen.rect = _bbox_union(chosen.selected)
        chosen.baseline_y = float(chosen.selected[0]['origin'][1])
        chosen.width = font.text_length(edit.new, fontsize=chosen.span['size']) if edit.new else 0
        # For dates, titles, etc. on the far right, preserve the original right edge.
        right_column = chosen.rect.x0 > page.rect.width * 0.58 and chosen.rect.x1 > page.rect.width * 0.80
        chosen.anchor = edit.align if edit.align != 'auto' else ('right' if right_column else 'left')
        chosen.start_x = float(chosen.rect.x1 - chosen.width) if chosen.anchor == 'right' else float(chosen.selected[0]['origin'][0])
        chosen.new_rect = fitz.Rect(chosen.start_x, chosen.rect.y0,
                                    chosen.start_x + chosen.width, chosen.rect.y1)
        if edit.new and (chosen.new_rect.x0 < page.rect.x0 + 2 or chosen.new_rect.x1 > page.rect.x1 - 2):
            raise EditError(f'Edit {i}: new text would exceed the page boundary. Original font size is not changed.')
        selected.append(chosen)

    for i, a in enumerate(selected):
        for b in selected[i + 1:]:
            if a.page_index == b.page_index and (
                a.rect.intersects(b.rect) or
                (not a.new_rect.is_empty and not b.new_rect.is_empty and
                 a.new_rect.intersects(b.new_rect))):
                raise EditError('Two edits overlap. Combine them into one replacement.')

    # A text-only redaction can erase any glyph whose box intersects it. Check
    # the deletion zone as well, not just the proposed replacement footprint.
    for i, m in enumerate(selected, 1):
        structure = doc[m.page_index].get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT)
        for bi, block in enumerate(structure['blocks']):
            if block.get('type') != 0:
                continue
            for li, line in enumerate(block['lines']):
                for si, span in enumerate(line['spans']):
                    for ci, char in enumerate(span.get('chars', [])):
                        if char['c'].isspace():
                            continue
                        if any((s.page_index == m.page_index and s.block_index == bi and
                                s.line_index == li and s.span_index == si and
                                s.char_start <= ci < s.char_end) for s in selected):
                            continue
                        r = m.rect
                        redact = fitz.Rect(r.x0 + 0.15, r.y0 + 0.15, r.x1 - 0.15, r.y1 - 0.15)
                        intersection = redact & fitz.Rect(char['bbox'])
                        if not intersection.is_empty and intersection.width > 0.5 and intersection.height > 0.5:
                            raise EditError(f'Edit {i}: original text overlaps a different glyph. '
                                            'Safe removal would erase unrelated text.')

    # Fail if the replacement occupies another, untouched text glyph's location.
    for i, m in enumerate(selected, 1):
        if not m.edit.new:
            continue
        page = doc[m.page_index]
        structure = page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT)
        for bi, block in enumerate(structure['blocks']):
            if block.get('type') != 0:
                continue
            for li, line in enumerate(block['lines']):
                for si, span in enumerate(line['spans']):
                    for ci, char in enumerate(span.get('chars', [])):
                        if char['c'].isspace():
                            continue
                        if any((s.page_index == m.page_index and s.block_index == bi and
                                s.line_index == li and s.span_index == si and
                                s.char_start <= ci < s.char_end) for s in selected):
                            continue
                        intersection = m.new_rect & fitz.Rect(char['bbox'])
                        if not intersection.is_empty and intersection.width > 0.5 and intersection.height > 0.5:
                            raise EditError(f'Edit {i}: new text would collide with existing text. '
                                            'Shorten the replacement or choose a different alignment.')
    return selected


def _rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16 & 255) / 255., (color >> 8 & 255) / 255., (color & 255) / 255.)


def _check_page_size(page, scale=1.35):
    width, height = page.rect.width, page.rect.height
    if (not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0
            or width * height > MAX_PAGE_AREA_PT2):
        raise EditError('A PDF page is too large to verify safely.')
    pixels = (page.rect * fitz.Matrix(scale, scale)).irect
    if (pixels.width > MAX_RENDER_SIDE or pixels.height > MAX_RENDER_SIDE
            or pixels.width * pixels.height > MAX_RENDER_PIXELS):
        raise EditError('A PDF page is too large to verify safely.')


def _render(page: fitz.Page, scale: float = 1.35) -> Image.Image:
    _check_page_size(page, scale)
    p = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
    return Image.frombytes('RGB', (p.width, p.height), p.samples)


def _difference_counts(before, after, masks):
    difference = ImageChops.difference(before, after).convert('L')
    allowed = Image.new('L', before.size, 0)
    draw = ImageDraw.Draw(allowed)
    for x0, y0, x1, y1 in masks:
        if x1 > x0 and y1 > y0:
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    changed = difference.point(lambda value: 255 if value > 16 else 0)
    inside_changed = ImageChops.multiply(changed, allowed).histogram()[255]
    outside_changed = changed.histogram()[255] - inside_changed
    outside_total = before.width * before.height - allowed.histogram()[255]
    return outside_total, outside_changed, inside_changed


def _png_data_uri(im: Image.Image) -> str:
    import base64
    stream = io.BytesIO()
    im.save(stream, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()


def edit_pdf(data: bytes, edits: list[Edit]) -> tuple[bytes, dict]:
    """Return an edited PDF and a visual-regression report, or reject unsafe edits."""
    original = _document(data)
    try:
        # These flags make redactions less likely to erase a neighboring text line.
        fitz.TOOLS.set_small_glyph_heights(True)
        matches = _prepare(original, edits)  # preflight all edits before mutation
        doc = _document(data)
        try:
            affected = sorted({m.page_index for m in matches})
            before = {n: _render(original[n]) for n in affected}
            font_aliases: dict[tuple[int, int], str] = {}
            for m in matches:
                page = doc[m.page_index]
                # Slight inset prevents erasing an adjacent glyph at a shared edge.
                r = m.rect
                inset_x = min(0.15, r.width / 5)
                inset_y = min(0.15, r.height / 5)
                page.add_redact_annot(fitz.Rect(r.x0 + inset_x, r.y0 + inset_y,
                                               r.x1 - inset_x, r.y1 - inset_y), fill=False, cross_out=False)
            for p in affected:
                doc[p].apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                        text=fitz.PDF_REDACT_TEXT_REMOVE)
            for m in matches:
                if not m.edit.new:
                    continue
                page = doc[m.page_index]
                if m.font_data is not None:
                    key = (m.page_index, m.font_xref)
                    alias = font_aliases.get(key)
                    if alias is None:
                        alias = f'PEDIT{m.font_xref}'
                        page.insert_font(fontname=alias, fontbuffer=m.font_data)
                        font_aliases[key] = alias
                else:
                    alias = m.font_name
                page.insert_text((m.start_x, m.baseline_y), m.edit.new,
                                 fontsize=m.span['size'], fontname=alias,
                                 color=_rgb(m.span['color']), overlay=True)
            result = doc.tobytes(garbage=3, deflate=True)
            # Textual verification after save+reload (never trust a successful draw call).
            verified = _document(result)
            try:
                for i, m in enumerate(matches, 1):
                    if m.edit.new:
                        new_hits = _span_matches(verified, Edit(old=m.edit.new, new='', page=m.page_index + 1))
                        if not any((_bbox_union(h.selected) & m.new_rect).get_area() >
                                   max(0.5, min(_bbox_union(h.selected).get_area(), m.new_rect.get_area()) * 0.5)
                                   for h in new_hits):
                            raise EditError(f'Edit {i}: replacement did not appear at the intended coordinates.')
                    if m.edit.old not in m.edit.new:
                        old_hits = _span_matches(verified, Edit(old=m.edit.old, new='', page=m.page_index + 1))
                        if any((_bbox_union(h.selected) & m.rect).get_area() >
                               max(0.5, min(_bbox_union(h.selected).get_area(), m.rect.get_area()) * 0.5)
                               for h in old_hits):
                            raise EditError(f'Edit {i}: original text still exists in the edited region.')
                # Compare all affected pages outside the allowed edit rectangles.
                reports = []
                for pi in affected:
                    b = before[pi]
                    a = _render(verified[pi])
                    if a.size != b.size:
                        raise EditError('Page dimensions changed during editing.')
                    scale_x = b.width / original[pi].rect.width
                    scale_y = b.height / original[pi].rect.height
                    masks = []
                    for m in matches:
                        if m.page_index != pi:
                            continue
                        union = m.rect | m.new_rect
                        masks.append((max(0, int(union.x0 * scale_x) - 3),
                                      max(0, int(union.y0 * scale_y) - 3),
                                      min(b.width, math.ceil(union.x1 * scale_x) + 3),
                                      min(b.height, math.ceil(union.y1 * scale_y) + 3)))
                    outside_total, outside_changed, inside_changed = _difference_counts(b, a, masks)
                    ratio = (outside_changed / outside_total) if outside_total else 0
                    # Reject substantial differences outside the intended regions.
                    if ratio > 0.0001:
                        raise EditError(f'Page {pi + 1} visual regression: {outside_changed} pixels '
                                        f'outside edited areas changed ({ratio * 100:.3f}%). Export blocked.')
                    reports.append(dict(page=pi + 1, outside_changed_pixels=outside_changed,
                                        outside_changed_percent=round(ratio * 100, 5),
                                        inside_changed_pixels=inside_changed))
                preview_index = affected[0]
                preview_before = before[preview_index]
                preview_after = _render(verified[preview_index])
                report = {
                    'status': 'passed', 'page_count': len(original),
                    'preview_page': preview_index + 1,
                    'changes': [{
                        'page': m.page_index + 1, 'old': m.edit.old, 'new': m.edit.new,
                        'font': m.span['font'], 'font_size': round(m.span['size'], 2),
                        'color': '#%06X' % m.span['color'],
                        'alignment': m.anchor,
                        'original_box': [round(z, 2) for z in m.rect],
                        'new_box': [round(z, 2) for z in m.new_rect],
                    } for m in matches],
                    'visual_regression': reports,
                    'before_preview': _png_data_uri(preview_before),
                    'after_preview': _png_data_uri(preview_after),
                    'note': 'Visual regression checks unchanged pixels, not semantic correctness. '
                            'Review the preview before using the PDF.',
                }
                return result, report
            finally:
                verified.close()
        finally:
            doc.close()
    finally:
        original.close()
