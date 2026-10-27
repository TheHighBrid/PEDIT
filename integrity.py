"""Preserve source provenance while recording the actual editing application."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import xml.etree.ElementTree as ET

import fitz

PRODUCER = 'Pedit 0.2 / PyMuPDF ' + fitz.VersionBind
XMP = 'http://ns.adobe.com/xap/1.0/'
PDF = 'http://ns.adobe.com/pdf/1.3/'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'


def signature_present(doc):
    # Include orphan signature dictionaries and certification permissions, not just
    # visible fields. Presence is not a cryptographic validity determination.
    if doc.get_sigflags() > 0:
        return True
    for page in doc:
        if any(w.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE for w in page.widgets() or []):
            return True
    for xref in range(1, doc.xref_length()):
        obj = doc.xref_object(xref, compressed=False)
        if re.search(r'/(?:ByteRange|DocMDP)\b|/Type\s*/Sig\b|/FT\s*/Sig\b', obj):
            return True
    return False


def geometry(doc):
    return [(tuple(p.mediabox), tuple(p.cropbox), p.rotation,
             tuple(p.bleedbox), tuple(p.trimbox), tuple(p.artbox)) for p in doc]


def stamp_revision(doc):
    """Keep authorship/creation/creator; truthfully update revision time/producer."""
    now = datetime.now(timezone.utc)
    metadata = dict(doc.metadata)
    metadata['modDate'] = now.strftime("D:%Y%m%d%H%M%S+00'00'")
    metadata['producer'] = PRODUCER
    doc.set_metadata(metadata)
    xml = doc.get_xml_metadata()
    if xml:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ValueError('Malformed XMP metadata cannot be safely updated.') from exc
        descriptions = root.findall('.//{' + RDF + '}Description')
        if not descriptions:
            raise ValueError('XMP metadata has no RDF description; edit refused.')
        for namespace, name, value in (
            (XMP, 'ModifyDate', now.isoformat(timespec='seconds')),
            (XMP, 'MetadataDate', now.isoformat(timespec='seconds')),
            (PDF, 'Producer', PRODUCER),
        ):
            key = '{' + namespace + '}' + name
            found = False
            for description in descriptions:
                if key in description.attrib:
                    description.set(key, value)
                    found = True
                for element in description.findall(key):
                    element.text = value
                    found = True
            if not found:
                ET.SubElement(descriptions[0], key).text = value
        doc.set_xml_metadata(ET.tostring(root, encoding='unicode'))
    return dict(doc.metadata), doc.get_xml_metadata()


def report(source, output, source_metadata, output_metadata, pages):
    return {
        'source_sha256': hashlib.sha256(source).hexdigest(),
        'output_sha256': hashlib.sha256(output).hexdigest(),
        'source_metadata': source_metadata,
        'output_metadata': output_metadata,
        'signature_status': 'No signature indicators detected; cryptographic validity was not assessed.',
        'preserved_fields': ['author', 'creationDate', 'creator', 'title', 'subject', 'keywords'],
        'revision_fields': ['producer', 'modDate', 'XMP revision dates and producer, when present'],
        'geometry_verified': True,
        'structure_verified': ['image streams', 'vector paths', 'links', 'annotations', 'form field values', 'attachments', 'table of contents', 'page labels'],
        'verification_dpi': 300,
        'pages_verified': pages,
        'pixel_tolerance': 0,
        'scope': 'Unaffected pixels, source-style reconstruction, text placement, page geometry and metadata. Not universal PDF conformance or authenticity certification.',
    }


def _stable(value):
    if isinstance(value, dict):
        return tuple(sorted((k,_stable(v)) for k,v in value.items() if k not in ('xref','id','seqno')))
    if isinstance(value, (list,tuple,fitz.Rect,fitz.Point,fitz.Quad)):
        return tuple(_stable(v) for v in value)
    return value


def structure(doc):
    """Compare interaction, image streams, vector paths and navigation after save."""
    pages=[]
    for page in doc:
        images=[]
        for image in page.get_images(full=True):
            # Compressed source image streams and soft masks must survive unchanged.
            images.append((tuple(image[2:7]),hashlib.sha256(doc.xref_stream_raw(image[0]) or b'').hexdigest(),
                           hashlib.sha256(doc.xref_stream_raw(image[1]) or b'').hexdigest() if image[1] else None))
        annotations=[]
        for a in page.annots() or []:
            annotations.append(_stable((a.type,a.rect,a.info,a.colors,a.border,a.flags,a.opacity,a.vertices)))
        widgets=[_stable((w.field_type,w.field_name,w.field_value,w.rect,w.field_flags)) for w in page.widgets() or []]
        pages.append((_stable(page.get_links()),tuple(annotations),tuple(widgets),
                      _stable(page.get_drawings()),tuple(images)))
    attachments=tuple((name,hashlib.sha256(doc.embfile_get(name)).hexdigest()) for name in doc.embfile_names())
    return (tuple(pages),_stable(doc.get_toc()),_stable(doc.get_page_labels()),attachments)
