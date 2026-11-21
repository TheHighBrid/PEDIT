"""Mark revised copies visibly and record their real revision metadata."""
from __future__ import annotations

import fitz

from integrity import stamp_revision

REVISION_LABEL = 'EDITED COPY - NOT ORIGINAL'


def revision_areas(page):
    return page.search_for(REVISION_LABEL)


def mark_revised_copy(doc):
    """Make the revision apparent in ordinary viewers and printed copies."""
    for page in doc:
        if revision_areas(page):
            continue
        bounds = page.rect * page.derotation_matrix
        size = min(12.0, (bounds.width - 12) / fitz.get_text_length(REVISION_LABEL, fontname='hebo', fontsize=1))
        if size < 5 or bounds.height < 16:
            raise ValueError('Page is too small for a legible revision label.')
        width = fitz.get_text_length(REVISION_LABEL, fontname='hebo', fontsize=size)
        x = bounds.x0 + (bounds.width - width) / 2
        y = bounds.y0 + size + 4
        page.insert_text((x, y), REVISION_LABEL, fontsize=size,
                         fontname='hebo', color=(0.75, 0.03, 0.05), overlay=True)
        if not revision_areas(page):
            raise ValueError('Could not verify the revised-copy label.')
    return stamp_revision(doc)
