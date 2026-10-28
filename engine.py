"""Conservative, locally executed PDF text replacement with measurable visual regression checks.

MVP: horizontal, opaque, printable text in a single font span. If a change cannot be
validated, fail closed instead of silently changing formatting or surrounding content.
"""
from __future__ import annotations
import io
import re
import math
from contextlib import ExitStack
from collections import Counter
from dataclasses import field

import integrity
from dataclasses import dataclass
from typing import Optional

import fitz
from PIL import Image, ImageChops, ImageDraw

MAX_PAGES = 60
MAX_FILE_BYTES = 25 * 1024 * 1024

class PrecisionError(ValueError):
    pass

@dataclass
class Edit:
    old: str
    new: str
    page: Optional[int] = None  # 1-based
    all_occurrences: bool = False

@dataclass
class Match:
    page: int
    block: int
    line: int
    span: int
    start: int
    end: int
    text: str
    rect: fitz.Rect
    origin: fitz.Point
    size: float
    color: int
    font: str
    alpha: int
    direction: tuple
    span_rect: fitz.Rect
    original_length: float
    target_length: float = 0
    font_name: str = ''
    font_bytes: bytes = b''
    origins: list = field(default_factory=list)
    new_origins: list = field(default_factory=list)
    paint_color: tuple = ()
    tracking: float = 0.0
    spacing_mode: str = ''

BASE14 = {
    'Helvetica': 'helv', 'Helvetica-Bold': 'hebo', 'Helvetica-Oblique': 'heit',
    'Helvetica-BoldOblique': 'hebi', 'Times-Roman': 'tiro', 'Times-Bold': 'tibo',
    'Times-Italic': 'tiit', 'Times-BoldItalic': 'tibi', 'Courier': 'cour',
    'Courier-Bold': 'cobo', 'Courier-Oblique': 'coit', 'Courier-BoldOblique': 'cobi',
    'Symbol': 'symb', 'ZapfDingbats': 'zadb'
}

PATTERN = re.compile(
    r'^\s*(?:(?:on\s+)?page\s+(?P<page>\d+)\s*[:,]?\s*)?'
    r'(?P<verb>replace\s+all|replace|change|edit)\s+'
    r'(?P<quote>["“\'‘])(?P<old>.+?)["”\'’]\s+'
    r'(?:with|to)\s+(?P<quote2>["“\'‘])(?P<new>.*?)["”\'’]\s*\.?\s*$',
    re.IGNORECASE
)

def parse_instructions(instructions: str) -> list[Edit]:
    lines = [s.strip() for s in instructions.splitlines() if s.strip()]
    if not lines:
        raise PrecisionError('Enter at least one instruction, such as: Replace "2025" with "2026"')
    edits = []
    for i, s in enumerate(lines, 1):
        m = PATTERN.match(s)
        if not m:
            raise PrecisionError(f'Instruction {i} is not supported. Try: On page 1, replace "Old" with "New". Put each edit on its own line.')
        old, new = m.group('old'), m.group('new')
        if old == new:
            raise PrecisionError(f'Instruction {i} has identical original and replacement text.')
        if not old or not new:
            raise PrecisionError(f'Instruction {i} must contain nonempty original and replacement text.')
        edits.append(Edit(old=old, new=new, page=int(m.group('page')) if m.group('page') else None,
                          all_occurrences='all' in m.group('verb').lower()))
    if len(edits) > 20:
        raise PrecisionError('Maximum 20 edit instructions per operation.')
    return edits

def validate_pdf(data: bytes) -> fitz.Document:
    if not data or len(data) > MAX_FILE_BYTES:
        raise PrecisionError('Upload a PDF no larger than 25 MB.')
    if not data.lstrip()[:6].startswith(b'%PDF-'):
        raise PrecisionError('The uploaded file is not a valid PDF.')
    try:
        doc = fitz.open(stream=data, filetype='pdf')
        if doc.needs_pass:
            doc.close()
            raise PrecisionError('Password-protected PDFs are not supported in this version.')
        if not 1 <= len(doc) <= MAX_PAGES:
            doc.close()
            raise PrecisionError(f'PDF must contain 1 to {MAX_PAGES} pages.')
        for page in doc:
            _check_render_size(page)
        return doc
    except PrecisionError:
        if 'doc' in locals() and not doc.is_closed:
            doc.close()
        raise
    except Exception as e:
        raise PrecisionError(f'PDF could not be opened: {str(e)[:100]}') from e

def _all_spans(page: fitz.Page):
    d = page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT | fitz.TEXT_INHIBIT_SPACES)
    for bi, block in enumerate(d['blocks']):
        for li, line in enumerate(block.get('lines', [])):
            for si, span in enumerate(line.get('spans', [])):
                if span.get('chars'):
                    yield bi, li, si, span, line

def _find_matches(doc: fitz.Document, edit: Edit) -> list[Match]:
    if edit.page is not None and not (1 <= edit.page <= len(doc)):
        raise PrecisionError(f'Page {edit.page} is outside this PDF ({len(doc)} pages).')
    result=[]
    for page_index in (range(len(doc)) if edit.page is None else [edit.page - 1]):
        page=doc[page_index]
        for bi,li,si,span,line in _all_spans(page):
            chars=span['chars']
            sentence=''.join(c['c'] for c in chars)
            start=0
            while True:
                start=sentence.find(edit.old,start)
                if start == -1: break
                stop=start+len(edit.old)
                chosen=chars[start:stop]
                rect=fitz.Rect(chosen[0]['bbox'])
                for c in chosen[1:]: rect.include_rect(fitz.Rect(c['bbox']))
                # glyph origins, not bounding-box corners, give true baseline placement
                origin=fitz.Point(chosen[0]['origin'])
                end_x = (chars[stop]['origin'][0] if stop<len(chars) else chosen[-1]['bbox'][2])
                result.append(Match(page_index,bi,li,si,start,stop,sentence,rect,origin,
                             float(span['size']), int(span['color']), span['font'],
                             int(span.get('alpha',255)), tuple(line.get('dir',(1,0))),
                             fitz.Rect(span['bbox']),max(0,end_x-origin.x)))
                result[-1].origins = [fitz.Point(c['origin']) for c in chosen]
                start=stop
    return result

def _resolve_font(doc: fitz.Document, page_index: int, matched: Match):
    font_name=matched.font
    normalized=re.sub(r'^[A-Z]{6}\+', '', font_name)
    def fingerprint(name):
        compact=re.sub(r'[^a-z0-9]','',re.sub(r'^[A-Z]{6}\+','',name).lower())
        return re.sub(r'(book|regular)$','',compact)
    fonts=doc[page_index].get_fonts(full=True)
    exact=[]
    aliases=[]
    for record in fonts:
        xref,ext,ftype,base,resource,encoding=record[:6]
        b=re.sub(r'^[A-Z]{6}\+','',base)
        if normalized and b.lower()==normalized.lower():
            exact.append(record)
        elif normalized and fingerprint(b)==fingerprint(normalized):
            aliases.append(record)
    plausible=list({record[0]:record for record in (exact or aliases)}.values())
    if len(plausible)>1:
        raise PrecisionError(f'Multiple embedded font resources match "{font_name}" on page {page_index+1}; exact replacement is ambiguous.')
    if plausible:
        xref,ext,ftype,base,resource,_=plausible[0][:6]
        if ext != 'n/a' and ext:
            _, ext2, _, data=doc.extract_font(xref)
            if not data: raise PrecisionError(f'Cannot extract original font "{font_name}".')
            try: font=fitz.Font(fontbuffer=data)
            except Exception as e: raise PrecisionError(f'Cannot reload font "{font_name}": {e}')
            return f'PF_{page_index}_{xref}', data, font
    if normalized in BASE14:
        code=BASE14[normalized]
        return code, b'', fitz.Font(fontname=code)
    raise PrecisionError(f'The original font "{font_name}" is not extractable or safely mappable. No substitute font was used.')

VERIFY_SCALE = 300 / 72

def _check_render_size(page, scale=VERIFY_SCALE):
    width,height=page.rect.width,page.rect.height
    if not math.isfinite(width) or not math.isfinite(height) or width<=0 or height<=0:
        raise PrecisionError('Page has invalid dimensions.')
    pixels=(page.rect * fitz.Matrix(scale,scale)).irect
    if pixels.width>16_384 or pixels.height>16_384 or pixels.width*pixels.height>40_000_000:
        raise PrecisionError('Page exceeds the safe 300 DPI verification size limit.')


def _render(page, scale=VERIFY_SCALE):
    _check_render_size(page,scale)
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False, colorspace=fitz.csRGB, annots=True)
    return Image.frombytes('RGB',(pix.width,pix.height),pix.samples)

def _validate_untouched(original: Image.Image, edited: Image.Image, rects: list[fitz.Rect], scale=VERIFY_SCALE):
    if original.size!=edited.size: return False,1.0
    mask=Image.new('L',original.size,0)
    painter=ImageDraw.Draw(mask)
    for rect in rects:
        # 4 px safety margin accounts for antialiasing and raster rounding
        painter.rectangle((int(rect.x0*scale)-4,int(rect.y0*scale)-4,
                           int(rect.x1*scale)+4,int(rect.y1*scale)+4),fill=255)
    # Per-channel maximum avoids hiding a one-level RGB change in luminance rounding.
    bands=ImageChops.difference(original,edited).split()
    diff=ImageChops.lighter(ImageChops.lighter(bands[0],bands[1]),bands[2])
    changed=diff.point(lambda n: 255 if n else 0)
    changed_outside=ImageChops.subtract(changed,mask)
    outside_pixels=sum(v for value,v in enumerate(changed_outside.histogram()) if value>0)
    total=original.size[0]*original.size[1]
    frac=outside_pixels/total
    return outside_pixels==0,frac

def _char_key(page, char, origin, font, size, color):
    font=re.sub(r'^[A-Z]{6}\+', '', font)
    return (page,char,round(origin[0],2),round(origin[1],2),font,round(size,3),color)


def _character_inventory(doc):
    return Counter(_char_key(p.number,c['c'],c['origin'],s['font'],s['size'],s['color'])
                   for p in doc for _,_,_,s,_ in _all_spans(p) for c in s['chars'])


def _plan_typography(page, match, edit, font):
    traces=[]
    for trace in page.get_texttrace():
        if any(abs(c[2][0]-match.origin.x)<.01 and abs(c[2][1]-match.origin.y)<.01
               and chr(c[0])==edit.old[0] for c in trace['chars']):
            traces.append(trace)
    if len(traces)!=1:
        raise PrecisionError('Overlapping or ambiguous text paint operations cannot be safely reproduced.')
    trace=traces[0]
    if trace['type']!=0 or trace['opacity']!=1 or trace.get('layer'):
        raise PrecisionError('Stroked, hidden, transparent or layered text requires a content-stream editor.')
    match.paint_color=tuple(trace['color'])
    if len(match.paint_color) not in (1,3):
        raise PrecisionError('This text color space cannot be faithfully reproduced.')
    advances=[float(font.text_length(c,fontsize=match.size)) for c in edit.old]
    offsets=[match.origins[i+1].x-match.origins[i].x-advances[i]
             for i in range(len(advances)-1)]
    if any(abs(p.y-match.origin.y)>.001 for p in match.origins):
        raise PrecisionError('Raised or transformed glyphs require individual review.')
    new_widths=[float(font.text_length(c,fontsize=match.size)) for c in edit.new]
    uniform=bool(offsets) and max(offsets)-min(offsets)<=.015
    if uniform:
        match.tracking=sum(offsets)/len(offsets)
    same_advances=(len(edit.old)==len(edit.new) and
                   all(abs(a-b)<.015 for a,b in zip(advances[:-1],new_widths[:-1])))
    if same_advances:
        match.new_origins=list(match.origins)
        match.spacing_mode='original glyph origins'
    else:
        if not uniform:
            raise PrecisionError('Variable kerning cannot be extrapolated to different glyph metrics. Use a replacement with matching glyph advances or edit a longer uniformly spaced phrase.')
        match.spacing_mode='measured uniform tracking'
        x=match.origin.x
        for width in new_widths:
            match.new_origins.append(fitz.Point(x,match.origin.y))
            x += width+match.tracking
    # Do not squeeze wider letters into original positions at the cost of overlap.
    new_widths=[float(font.text_length(c,fontsize=match.size)) for c in edit.new]
    for i in range(len(edit.new)-1):
        original_overlap=(match.origins[i+1].x-match.origins[i].x-advances[i]
                          if len(edit.old)==len(edit.new) else match.tracking)
        if match.new_origins[i].x+new_widths[i]>match.new_origins[i+1].x+max(0,-original_overlap)+.02:
            raise PrecisionError('Replacement glyphs would overlap at the original spacing; edit refused.')
    match.target_length=match.new_origins[-1].x-match.origin.x+new_widths[-1]


def _apply(page, targets, reconstruct=False):
    for e,m in targets:
        page.add_redact_annot(m.rect,fill=False,cross_out=False)
    page.apply_redactions(images=0,graphics=0,text=0)
    for e,m in targets:
        if m.font_bytes:
            page.insert_font(fontname=m.font_name,fontbuffer=m.font_bytes)
        text=e.old if reconstruct else e.new
        origins=m.origins if reconstruct else m.new_origins
        for char,origin in zip(text,origins):
            page.insert_text(origin,char,fontname=m.font_name,fontsize=m.size,
                             color=m.paint_color,overlay=True)


def _edit_pdf(data: bytes, instructions: str, resources) -> dict:
    original=resources.enter_context(validate_pdf(data))
    reference=resources.enter_context(validate_pdf(data))
    if integrity.signature_present(original):
        raise PrecisionError('This PDF contains signature fields or signature indicators. Editing is blocked to protect the signed original. Obtain an unsigned source and re-sign the edited document.')
    source_metadata=dict(original.metadata)
    source_geometry=integrity.geometry(original)
    source_structure=integrity.structure(original)
    edits=parse_instructions(instructions)
    plans=[]
    occupied=[]
    for edit in edits:
        matches=_find_matches(original,edit)
        if not matches:
            raise PrecisionError(f'Could not find "{edit.old}" as continuous editable text. It may be scanned, outlined, or split across font spans.')
        if len(matches)>1 and not edit.all_occurrences:
            pages=', '.join(str(m.page+1) for m in matches[:12])
            raise PrecisionError(f'"{edit.old}" appears {len(matches)} times (pages {pages}). Specify a page or use Replace all to prevent accidental changes.')
        selected=matches if edit.all_occurrences else matches[:1]
        for match in selected:
            if match.alpha!=255 or abs(match.direction[0]-1)>0.0001 or abs(match.direction[1])>0.0001:
                raise PrecisionError('Transparent or rotated text cannot be matched safely in this version.')
            if match.rect.width<=0:
                raise PrecisionError('Text has an invalid glyph bounding box.')
            # Interactive objects are intentionally not silently rewritten.
            current_page=original[match.page]
            for widget in current_page.widgets() or []:
                if fitz.Rect(widget.rect).intersects(match.rect):
                    raise PrecisionError('The requested edit overlaps a form field. Edit the form field instead; no output was created.')
            for link in current_page.get_links():
                if fitz.Rect(link['from']).intersects(match.rect):
                    raise PrecisionError('The requested edit overlaps a link. Link-preserving replacement is not supported yet.')
            for annotation in current_page.annots() or []:
                if fitz.Rect(annotation.rect).intersects(match.rect):
                    raise PrecisionError('The requested edit overlaps an annotation. This edit requires review before replacement.')
            font_name, font_bytes, font = _resolve_font(original,match.page,match)
            for char in edit.new:
                if char!=' ' and not font.has_glyph(ord(char),fallback=False):
                    raise PrecisionError(f'The original font "{match.font}" lacks glyph "{char}". A lookalike font would not be pixel-exact; edit refused.')
            _plan_typography(original[match.page], match, edit, font)
            width=match.target_length
            match.font_name=font_name
            match.font_bytes=font_bytes
            match.target_length=width
            if match.origin.x + width>(original[match.page].rect * original[match.page].derotation_matrix).x1-2:
                raise PrecisionError(f'"{edit.new}" would extend beyond page {match.page+1}.')
            target_area=fitz.Rect(match.rect)
            target_area.x1=max(target_area.x1,match.origin.x+width)
            if any(fitz.Rect(obj['from']).intersects(target_area) for obj in current_page.get_links()):
                raise PrecisionError('Replacement would overlap a link; edit refused.')
            if any(fitz.Rect(w.rect).intersects(target_area) for w in current_page.widgets() or []):
                raise PrecisionError('Replacement would overlap a form field; edit refused.')
            if any(fitz.Rect(a.rect).intersects(target_area) for a in current_page.annots() or []):
                raise PrecisionError('Replacement would overlap an annotation; edit refused.')
            # Width and collision check against all untargeted characters on same line
            for bi,li,si,span,line in _all_spans(original[match.page]):
                if tuple(line.get('dir',(1,0)))!=(1.0,0.0): continue
                if fitz.Rect(span['bbox']).y1 <= match.rect.y0 or fitz.Rect(span['bbox']).y0 >= match.rect.y1:
                    continue
                for ci,char_obj in enumerate(span['chars']):
                    if bi==match.block and li==match.line and si==match.span and match.start<=ci<match.end:
                        continue
                    box=fitz.Rect(char_obj['bbox'])
                    # Compare mostly baseline-horizontal overlap; limit intrusion into other glyphs
                    if box.x0 < match.origin.x + width - 0.4 and box.x1 > match.origin.x + 0.4:
                        raise PrecisionError(f'"{edit.new}" would collide with existing text on page {match.page+1}. Shorten replacement or edit the whole phrase.')
            for prior in occupied:
                if prior.page==match.page and prior.rect.intersects(match.rect):
                    raise PrecisionError('Two edit instructions overlap the same text region.')
            occupied.append(match)
            plans.append((edit,match))
    # Prove that the proposed drawing method can reproduce the source glyphs.
    # This catches transforms, font metrics, clipping and paint-state mismatches.
    proof=resources.enter_context(validate_pdf(data))
    for p in sorted({m.page for _,m in plans}):
        _apply(proof[p], [(e,m) for e,m in plans if m.page==p], reconstruct=True)
        if ImageChops.difference(_render(original[p]), _render(proof[p])).getbbox():
            raise PrecisionError(f'Original typography could not be reproduced pixel-for-pixel at 300 DPI on page {p+1}. No substitute or approximate rendering was exported.')
    expected_chars=_character_inventory(original)
    for e,m in plans:
        for ch,origin in zip(e.old,m.origins):
            expected_chars[_char_key(m.page,ch,origin,m.font,m.size,m.color)] -= 1
        for ch,origin in zip(e.new,m.new_origins):
            expected_chars[_char_key(m.page,ch,origin,m.font,m.size,m.color)] += 1
    # Apply only fully planned and source-reconstruction-verified edits.
    pages=sorted({m.page for _,m in plans})
    for p in pages:
        page=original[p]
        targets=[(e,m) for e,m in plans if m.page==p]
        _apply(page, targets)
    try:
        expected_metadata, expected_xmp=integrity.stamp_revision(original)
    except ValueError as exc:
        raise PrecisionError(str(exc)) from exc
    out=original.tobytes(garbage=4,deflate=False)
    result=resources.enter_context(validate_pdf(out))
    previews=[]
    if integrity.geometry(result)!=source_geometry:
        raise PrecisionError('Validation failed: page count, dimensions, rotation or page boxes changed.')
    if integrity.structure(result)!=source_structure:
        raise PrecisionError('Validation failed: graphics, images, links, annotations, forms, attachments or navigation changed unexpectedly.')
    if result.metadata!=expected_metadata or result.get_xml_metadata()!=expected_xmp:
        raise PrecisionError('Validation failed: metadata did not survive export accurately.')
    if +_character_inventory(result) != +expected_chars:
        raise PrecisionError('Validation failed: character content, fonts, sizes, colors or positions changed unexpectedly.')
    for p in range(len(result)):
        rects=[]
        for e,m in plans:
            if m.page!=p: continue
            rects.append(fitz.Rect(m.origin.x-2,min(m.rect.y0,m.origin.y-m.size*1.6)-1,
                                   max(m.rect.x1,m.origin.x+m.target_length)+2,
                                   max(m.rect.y1,m.origin.y+m.size*.6)+1))
        rects=[rect * reference[p].rotation_matrix for rect in rects]
        ok, fraction=_validate_untouched(_render(reference[p]),_render(result[p]),rects)
        if not ok:
            raise PrecisionError(f'Validation failed: pixels outside the intended edit region changed on page {p+1} ({fraction:.4%}). The output has been withheld.')
        if p in pages:
            bf=io.BytesIO(); af=io.BytesIO()
            _render(reference[p],scale=2).save(bf,format='PNG')
            _render(result[p],scale=2).save(af,format='PNG')
            previews.append({'page':p+1,'before_png':bf.getvalue(),'after_png':af.getvalue()})
    # Validate text replacement outcome with new extraction, plus page count invariance
    for e,m in plans:
        area=fitz.Rect(m.origin.x-2,m.rect.y0-4,m.origin.x+m.target_length+5,m.rect.y1+4)
        text=result[m.page].get_text(clip=area, flags=fitz.TEXTFLAGS_TEXT | fitz.TEXT_INHIBIT_SPACES)
        if e.new not in text:
            raise PrecisionError(f'Validation failed: replacement "{e.new}" is not selectable on page {m.page+1}.')
    first=pages[0]
    return {
        'pdf':out,'before_png':previews[0]['before_png'],'after_png':previews[0]['after_png'],
        'previews':previews,
        'preview_page':first+1,'pages':len(result),
        'edits':[{'page':m.page+1,'old':e.old,'new':e.new,'font':m.font,
                 'spacing_mode':m.spacing_mode,
                 'tracking':round(m.tracking,5),'font_size':round(m.size,5),'font_color':f'#{m.color:06X}',
                 'position':{'x':round(m.origin.x,2),'y':round(m.origin.y,2)}}
                 for e,m in plans],
        'integrity':integrity.report(data,out,source_metadata,result.metadata,len(result)),
        'validation':'PASS: source typography reconstructed exactly; unaffected pixels on every page are identical at 300 DPI; character placement, page geometry and metadata verified.',
        'limitation':'Validation uses MuPDF at 300 DPI. Edited glyphs differ by design; other renderers, print separations, accessibility and PDF/A conformance are not certified. Signed documents are blocked. Source authorship is retained; revision date and editing software are recorded honestly.'
    }


def edit_pdf(data: bytes, instructions: str) -> dict:
    with ExitStack() as resources:
        return _edit_pdf(data, instructions, resources)
