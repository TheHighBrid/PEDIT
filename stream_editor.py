"""Narrow PDF text-show editor: printable ASCII in unmodified Base-14 fonts.

No regex replacement of PDF syntax. String operands are decoded by a lexer,
associated with text-show operators and mapped to extracted glyphs before edits.
"""
from dataclasses import dataclass
from collections import Counter
import re
import fitz


class StreamError(ValueError):
    pass


@dataclass
class Token:
    kind: str
    value: object
    start: int
    end: int


class Lexer:
    def __init__(self, data):
        self.data=data
        self.pos=0

    def token(self, depth=0):
        if depth>32:
            raise StreamError('Content nesting exceeds the stream editor limit.')
        data=self.data
        while self.pos<len(data):
            if data[self.pos] in b'\x00\t\n\f\r ':
                self.pos+=1
            elif data[self.pos]==37:
                while self.pos<len(data) and data[self.pos] not in b'\r\n': self.pos+=1
            else: break
        start=self.pos
        if start==len(data): return None
        byte=data[self.pos];self.pos+=1
        if byte==40:
            output=bytearray();level=1
            while self.pos<len(data):
                c=data[self.pos];self.pos+=1
                if c==92:
                    if self.pos==len(data): break
                    c=data[self.pos];self.pos+=1
                    if c in b'\r\n':
                        if c==13 and data[self.pos:self.pos+1]==b'\n': self.pos+=1
                        continue
                    escapes={110:10,114:13,116:9,98:8,102:12}
                    if 48<=c<=55:
                        octal=bytes([c])
                        for _ in range(2):
                            if self.pos<len(data) and 48<=data[self.pos]<=55:
                                octal+=data[self.pos:self.pos+1];self.pos+=1
                            else: break
                        output.append(int(octal,8)&255)
                    else: output.append(escapes.get(c,c))
                elif c==40:
                    level+=1
                    if level>32: raise StreamError('Literal string nesting exceeds limit.')
                    output.append(c)
                elif c==41:
                    level-=1
                    if not level: return Token('string',bytes(output),start,self.pos)
                    output.append(c)
                elif c==13:
                    if data[self.pos:self.pos+1]==b'\n': self.pos+=1
                    output.append(10)
                else: output.append(c)
            raise StreamError('Unterminated PDF literal string.')
        if byte==60 and data[self.pos:self.pos+1]!=b'<':
            end=data.find(b'>',self.pos)
            if end<0: raise StreamError('Unterminated PDF hex string.')
            raw=re.sub(rb'[\x00\t\n\f\r ]',b'',data[self.pos:end])
            if re.search(rb'[^0-9a-fA-F]',raw): raise StreamError('Invalid PDF hex string.')
            if len(raw)%2: raw+=b'0'
            self.pos=end+1
            return Token('string',bytes.fromhex(raw.decode('ascii')),start,self.pos)
        if byte in (91,60):
            dictionary=byte==60
            if dictionary: self.pos+=1
            children=[]
            while True:
                item=self.token(depth+1)
                if item is None: raise StreamError('Unterminated PDF container.')
                if item.kind=='end':
                    if item.value!=(b'>>' if dictionary else b']'): raise StreamError('Mismatched PDF container.')
                    return Token('dict' if dictionary else 'array',children,start,self.pos)
                children.append(item)
        if byte==93: return Token('end',b']',start,self.pos)
        if byte==62:
            if data[self.pos:self.pos+1]!=b'>': raise StreamError('Invalid PDF delimiter.')
            self.pos+=1
            return Token('end',b'>>',start,self.pos)
        if byte==41: raise StreamError('Unexpected PDF string delimiter.')
        if byte==47:
            while self.pos<len(data) and data[self.pos] not in b'\x00\t\n\f\r ()<>[]{}/%': self.pos+=1
            name=data[start+1:self.pos]
            if re.search(rb'#(?![0-9a-fA-F]{2})',name): raise StreamError('Malformed PDF name escape.')
            name=re.sub(rb'#([0-9a-fA-F]{2})',lambda m:bytes([int(m[1],16)]),name)
            return Token('name',name,start,self.pos)
        while self.pos<len(data) and data[self.pos] not in b'\x00\t\n\f\r ()<>[]{}/%': self.pos+=1
        word=data[start:self.pos]
        if re.fullmatch(rb'[+-]?(?:\d+\.?\d*|\.\d+)',word): return Token('number',word,start,self.pos)
        return Token('word',word,start,self.pos)


def _fonts(doc,page):
    supported={}
    for record in page.get_fonts(full=True):
        xref,ext,kind,base,resource,encoding,referencer=record[:7]
        if xref<=0 or referencer or kind!='Type1' or base not in (
            'Helvetica','Helvetica-Bold','Helvetica-Oblique','Helvetica-BoldOblique',
            'Times-Roman','Times-Bold','Times-Italic','Times-BoldItalic',
            'Courier','Courier-Bold','Courier-Oblique','Courier-BoldOblique'):
            continue
        keys=set(doc.xref_get_keys(xref))
        if keys-{'Type','Subtype','BaseFont','Encoding','Name'}: continue
        if encoding not in ('WinAnsiEncoding','StandardEncoding',''): continue
        if doc.xref_get_key(xref,'Encoding')[0] not in ('name','null'): continue
        if resource in supported: raise StreamError('Ambiguous page font resource.')
        try: name=resource.encode('ascii')
        except UnicodeEncodeError: continue
        supported[name]=base
    return supported


def page_glyphs(doc,page,span_reader):
    fonts=_fonts(doc,page)
    shows=[];font=None;render_mode=0;stack=[];in_text=False
    for xref in page.get_contents():
        data=doc.xref_stream(xref)
        if len(data)>10*1024*1024: raise StreamError('Decoded content stream exceeds the native editing limit.')
        lexer=Lexer(data);operands=[]
        while (token:=lexer.token()) is not None:
            if token.kind!='word':
                operands.append(token)
                continue
            op=token.value
            if op==b'BI': raise StreamError('Inline images are not supported in content-stream mode.')
            if op==b'gs': raise StreamError('External graphics state is not supported in content-stream mode.')
            if op==b'q': stack.append((font,render_mode))
            elif op==b'Q':
                if not stack: raise StreamError('Unbalanced graphics state.')
                font,render_mode=stack.pop()
            elif op==b'BT':
                if in_text: raise StreamError('Nested text objects are unsupported.')
                in_text=True
            elif op==b'ET':
                if not in_text: raise StreamError('Unbalanced text objects.')
                in_text=False
            elif op==b'Tr':
                if len(operands)!=1 or operands[0].kind!='number': raise StreamError('Malformed text rendering mode.')
                render_mode=float(operands[0].value)
            elif op==b'Tf':
                if len(operands)!=2 or operands[0].kind!='name' or operands[1].kind!='number':
                    raise StreamError('Malformed font selection.')
                font=fonts.get(operands[0].value)
            elif op in (b'Tj',b'TJ',b"'",b'"'):
                if render_mode!=0: raise StreamError('Stroked, hidden or text-clipping paint is not supported in content-stream mode.')
                if not in_text or font is None: raise StreamError('Text uses an unsupported font encoding.')
                expected=3 if op==b'"' else 1
                if len(operands)!=expected: raise StreamError('Malformed text-show operands.')
                if op==b'"' and any(t.kind!='number' for t in operands[:2]): raise StreamError('Malformed text spacing operands.')
                value=operands[-1]
                if op==b'TJ':
                    if value.kind!='array' or any(t.kind not in ('string','number') for t in value.value):
                        raise StreamError('Malformed text-show array.')
                    strings=[t for t in value.value if t.kind=='string']
                else:
                    if value.kind!='string': raise StreamError('Malformed text-show string.')
                    strings=[value]
                for string in strings:
                    if any(c<32 or c>126 for c in string.value):
                        raise StreamError('Content-stream mode currently requires printable ASCII source text.')
                    if len(shows)+len(string.value)>100_000: raise StreamError('Page exceeds the native editing glyph limit.')
                    for index,char in enumerate(string.value): shows.append((chr(char),font,xref,string,index))
            operands=[]
    if in_text or stack: raise StreamError('Unbalanced page graphics or text state.')
    traces=page.get_texttrace()
    extracted=[]
    for trace in traces:
        if trace['type']!=0 or trace['opacity']!=1 or trace.get('layer') or tuple(trace['dir'])!=(1.0,0.0):
            raise StreamError('Content-stream mode requires horizontal, opaque, unlayered fill text.')
        for char in trace['chars']: extracted.append((chr(char[0]),trace['font'],char[2]))
    if len(shows)!=len(extracted) or any(a[:2]!=b[:2] for a,b in zip(shows,extracted)):
        raise StreamError('Source operators cannot be mapped unambiguously to the rendered glyphs.')
    # Raw extraction provides the same bounds/font inventory used by final validation.
    raw=[]
    for bi,li,si,span,line in span_reader(page):
        for ci,char in enumerate(span['chars']): raw.append((bi,li,si,ci,span,line,char))
    by_key={}
    for index,(_,_,_,_,span,_,char) in enumerate(raw):
        key=(char['c'],span['font'],round(char['origin'][0],2),round(char['origin'][1],2))
        if key in by_key: raise StreamError('Overlapping source glyphs are ambiguous.')
        by_key[key]=index
    mapping={}
    for shown,(char,font,origin) in zip(shows,extracted):
        key=(char,font,round(origin[0],2),round(origin[1],2))
        if key not in by_key: raise StreamError('Source glyph extraction disagrees with the text operators.')
        index=by_key[key]
        if index in mapping: raise StreamError('A source glyph maps to multiple operators.')
        mapping[index]=shown
    if len(mapping)!=len(raw): raise StreamError('Not all source glyphs can be mapped to content strings.')
    return raw,mapping


def plan(doc,edits,span_reader):
    """Return per-span selections and byte patches, with no document mutation."""
    references=Counter(xref for page in doc for xref in page.get_contents())
    cache={};selected=[];used=set();changes={};original_values={};metrics_cache={}
    for edit_index,edit in enumerate(edits):
        if len(edit.old)!=len(edit.new) or not edit.new or any(ord(c)<32 or ord(c)>126 for c in edit.old+edit.new):
            raise StreamError('Content-stream mode requires equal-length, nonempty printable ASCII replacements.')
        if edit.all_occurrences and edit.occurrence is not None: raise StreamError('Choose one occurrence or all occurrences, not both.')
        if edit.page is not None and not 1<=edit.page<=len(doc): raise StreamError('Requested page is outside this PDF.')
        matches=[]
        for page_index in (range(len(doc)) if edit.page is None else [edit.page-1]):
            page=doc[page_index]
            if any(references[xref]!=1 for xref in page.get_contents()): raise StreamError('Shared content streams require a separate editing path.')
            if page_index not in cache: cache[page_index]=page_glyphs(doc,page,span_reader)
            raw,mapping=cache[page_index]
            groups={}
            for index,entry in enumerate(raw): groups.setdefault(entry[:2],[]).append(index)
            for indices in groups.values():
                text=''.join(raw[i][6]['c'] for i in indices)
                start=0
                while (start:=text.find(edit.old,start))>=0:
                    match=indices[start:start+len(edit.old)]
                    # Never join separate columns or discontinuous baselines as one phrase.
                    for a,b in zip(match,match[1:]):
                        ca,cb=raw[a][6],raw[b][6]
                        if abs(ca['origin'][1]-cb['origin'][1])>.01 or cb['origin'][0]<ca['origin'][0]-.01 or cb['bbox'][0]-ca['bbox'][2]>max(raw[a][4]['size'],raw[b][4]['size'])*.5:
                            raise StreamError('A requested phrase crosses a discontinuous baseline or column.')
                    matches.append((page_index,match));start+=1 if edit.origin is not None else len(edit.old)
        if edit.origin is not None:
            matches=[(p,indices) for p,indices in matches if all(abs(a-b)<.01 for a,b in zip(cache[p][0][indices[0]][6]['origin'],edit.origin))]
        if not matches: raise StreamError(f'Could not find "{edit.old}" on one continuous text line.')
        if edit.occurrence is not None and edit.occurrence>len(matches): raise StreamError('Requested occurrence is outside the matching range.')
        if len(matches)>1 and not edit.all_occurrences and edit.occurrence is None: raise StreamError(f'"{edit.old}" appears {len(matches)} times. Choose a page and match number or all matches.')
        chosen=matches if edit.all_occurrences else [matches[(edit.occurrence or 1)-1]]
        for page_index,indices in chosen:
            raw,mapping=cache[page_index];fragments=[]
            for index,new in zip(indices,edit.new):
                if (page_index,index) in used: raise StreamError('Two edits overlap the same source glyph.')
                used.add((page_index,index))
                old,font,xref,token,offset=mapping[index]
                if font not in metrics_cache: metrics_cache[font]=fitz.Font(fontname=font)
                metrics=metrics_cache[font]
                if (old==' ')!=(new==' ') or abs(metrics.text_length(old)-metrics.text_length(new))>1e-6:
                    raise StreamError('Content-stream mode requires matching glyph advances and unchanged word-space positions.')
                entry=raw[index]
                if not fragments or fragments[-1]['entry'][:3]!=entry[:3]:
                    fragments.append({'entry':entry,'old':'','new':'','chars':[]})
                fragment=fragments[-1];fragment['old']+=old;fragment['new']+=new;fragment['chars'].append(entry[6])
                key=(page_index,xref,token.start,token.end)
                if key not in changes:
                    changes[key]=bytearray(token.value)
                    original_values[key]=token.value
                changes[key][offset]=ord(new)
            selected.append((edit_index,page_index,fragments))
    patches={};proof_patches={}
    for (page_index,xref,start,end),value in changes.items():
        patches.setdefault(page_index,[]).append((xref,start,end,b'<'+bytes(value).hex().encode('ascii')+b'>'))
        original=original_values[(page_index,xref,start,end)]
        proof_patches.setdefault(page_index,[]).append((xref,start,end,b'<'+original.hex().encode('ascii')+b'>'))
    return selected,patches,proof_patches


def apply(doc,patches):
    grouped={}
    for xref,start,end,value in patches: grouped.setdefault(xref,[]).append((start,end,value))
    for xref,changes in grouped.items():
        data=doc.xref_stream(xref)
        for start,end,value in sorted(changes,reverse=True): data=data[:start]+value+data[end:]
        doc.update_stream(xref,data)
