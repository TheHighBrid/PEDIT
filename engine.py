"""Conservative, locally executed PDF text replacement with measurable visual regression checks.

MVP: horizontal, opaque, printable text in a single font span. If a change cannot be
validated, fail closed instead of silently changing formatting or surrounding content.
"""
from __future__ import annotations
import io
import re
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
            raise PrecisionError('Password-protected PDFs are not supported in this version.')
        if not 1 <= len(doc) <= MAX_PAGES:
            raise PrecisionError(f'PDF must contain 1 to {MAX_PAGES} pages.')
        return doc
    except PrecisionError:
        raise
    except Exception as e:
        raise PrecisionError(f'PDF could not be opened: {str(e)[:100]}') from e

def _all_spans(page: fitz.Page):
    d = page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT)
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
                start=stop
    return result

def _resolve_font(doc: fitz.Document, page_index: int, matched: Match):
    font_name=matched.font
    normalized=re.sub(r'^[A-Z]{6}\+', '', font_name)
    def fingerprint(name):
        compact=re.sub(r'[^a-z0-9]','',re.sub(r'^[A-Z]{6}\+','',name).lower())
        return re.sub(r'(book|regular)$','',compact)
    fonts=doc[page_index].get_fonts(full=True)
    plausible=[]
    for record in fonts:
        xref,ext,ftype,base,resource,encoding=record[:6]
        b=re.sub(r'^[A-Z]{6}\+','',base)
        if b==normalized or b.lower()==normalized.lower() or fingerprint(b)==fingerprint(normalized):
            plausible.append(record)
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

def _render(page, scale=2.0):
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False, colorspace=fitz.csRGB, annots=True)
    return Image.frombytes('RGB',(pix.width,pix.height),pix.samples)

def _validate_untouched(original: Image.Image, edited: Image.Image, rects: list[fitz.Rect], scale=2.0):
    if original.size!=edited.size: return False,1.0
    mask=Image.new('L',original.size,0)
    painter=ImageDraw.Draw(mask)
    for rect in rects:
        # 3 px safety margin accounts for antialiasing and raster rounding
        painter.rectangle((int(rect.x0*scale)-4,int(rect.y0*scale)-4,
                           int(rect.x1*scale)+4,int(rect.y1*scale)+4),fill=255)
    diff=ImageChops.difference(original,edited).convert('L')
    changed=diff.point(lambda n: 255 if n>5 else 0)
    changed_outside=ImageChops.subtract(changed,mask)
    outside_pixels=sum(v for value,v in enumerate(changed_outside.histogram()) if value>0)
    total=original.size[0]*original.size[1]
    frac=outside_pixels/total
    return outside_pixels==0,frac

def edit_pdf(data: bytes, instructions: str) -> dict:
    original=validate_pdf(data)
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
            width=float(font.text_length(edit.new,fontsize=match.size))
            match.font_name=font_name
            match.font_bytes=font_bytes
            match.target_length=width
            if match.origin.x + width>original[match.page].rect.x1-2:
                raise PrecisionError(f'"{edit.new}" would extend beyond page {match.page+1}.')
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
    # Capture original page render before mutating anything
    pages=sorted({m.page for _,m in plans})
    before={p:_render(original[p]) for p in pages}
    for p in pages:
        page=original[p]
        targets=[(e,m) for e,m in plans if m.page==p]
        # redaction removes only text, leaving page vector graphics and images alone
        for e,m in targets:
            page.add_redact_annot(m.rect,fill=False,cross_out=False)
        page.apply_redactions(images=0,graphics=0,text=0)
        for e,m in targets:
            if m.font_bytes:
                page.insert_font(fontname=m.font_name,fontbuffer=m.font_bytes)
            # A new PDF content object is laid at the original text origin/baseline
            page.insert_text(m.origin,e.new,fontname=m.font_name,fontsize=m.size,
                             color=tuple(((m.color>>offset)&255)/255 for offset in (16,8,0)),
                             overlay=True)
    out=original.tobytes(garbage=4,deflate=True)
    result=validate_pdf(out)
    for p in pages:
        rects=[]
        for e,m in plans:
            if m.page!=p: continue
            rects.append(fitz.Rect(m.origin.x-2,min(m.rect.y0,m.origin.y-m.size*1.6)-1,
                                   max(m.rect.x1,m.origin.x+m.target_length)+2,
                                   max(m.rect.y1,m.origin.y+m.size*.6)+1))
        ok, fraction=_validate_untouched(before[p],_render(result[p]),rects)
        if not ok:
            raise PrecisionError(f'Validation failed: pixels outside the intended edit region changed on page {p+1} ({fraction:.4%}). The output has been withheld.')
    # Validate text replacement outcome with new extraction, plus page count invariance
    for e,m in plans:
        area=fitz.Rect(m.origin.x-2,m.rect.y0-4,m.origin.x+m.target_length+5,m.rect.y1+4)
        text=result[m.page].get_text(clip=area)
        if e.new not in text:
            raise PrecisionError(f'Validation failed: replacement "{e.new}" is not selectable on page {m.page+1}.')
    first=pages[0]
    prev_after=_render(result[first])
    bf=io.BytesIO(); af=io.BytesIO()
    before[first].save(bf,format='PNG');prev_after.save(af,format='PNG')
    return {
        'pdf':out,'before_png':bf.getvalue(),'after_png':af.getvalue(),
        'preview_page':first+1,'pages':len(result),
        'edits':[{'page':m.page+1,'old':e.old,'new':e.new,'font':m.font,
                 'font_size':round(m.size,2),'font_color':f'#{m.color:06X}',
                 'position':{'x':round(m.origin.x,2),'y':round(m.origin.y,2)}}
                 for e,m in plans],
        'validation':'PASS: all changed-page pixels outside the edit regions are unchanged at 144 DPI; replacement text is selectable.',
        'limitation':'The replacement uses the original embedded font where available. This is a validated, conservative rewrite, not guaranteed byte-identical PDF content streams.'
    }
