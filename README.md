# Pedit 0.2: precision edits with export verification

Pedit is a local web app for conservative text replacement in digitally generated PDFs. It reuses the original font and measured typography, preserves surrounding artwork, and withholds output when its checks fail. It uses no external AI or API keys.

## Run

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. On macOS, `start_mac.command` also installs dependencies and opens the browser. Keep its Terminal window open. Windows activation: `.venv\Scripts\activate`.

## Editing

Upload an unsigned PDF and enter one instruction per line:

```text
Replace "2025" with "2026"
On page 2, replace "Draft" with "Final"
Replace all "CONFIDENTIAL" with "PUBLIC"
```

Matches must be contiguous within one text span. Repeated matches require a page that uniquely identifies one occurrence, or `Replace all`. The original upload is never overwritten. Review each modified page using the page selector and before/after slider. Download the PDF and its JSON verification report only after validation succeeds.

## Precision checks

- Reuses an extractable embedded font or the original PDF Base-14 font. Missing glyphs and ambiguous resources are rejected.
- Reads individual character origins, baseline, size and paint color. Tracks explicit letter spacing without treating extraction-generated spaces as document content.
- Retains original glyph origins when character advances match. Otherwise uses measured uniform tracking and the replacement glyphs' own widths. Irregular kerning that cannot be inferred safely is rejected. Text is never silently squeezed or resized to fit.
- Before editing, reconstructs the **original text** using the proposed font, positions and drawing method. The entire reconstructed page must render identically at 300 DPI. This tests source typography, not the appearance of characters that were absent from the original selection.
- Removes selected PDF text, retaining image and vector content. Checks text collisions and overlaps with interactive objects before applying changes.
- Compares every output page against the source at 300 DPI in MuPDF. Outside the edit rectangles and a four-pixel raster margin, any RGB channel difference fails validation. Edit rectangles include the source bounds, replacement extent and conservative font-height padding; pixels within those masks are intentionally excluded.
- Checks the character inventory, including glyph positions to 0.01 points, font identity, size and RGB color. Unexpected deletion or movement of neighboring text fails export.
- Verifies page count, rotation, media/crop/bleed/trim/art boxes; vector paths; compressed image and mask streams; links; annotation properties; form names/values/flags; attachments; table of contents; and page labels. This is a defined comparison of these properties, not an exhaustive comparison of every PDF object or interactive action.

Existing tables, logos, fine print and symbols are retained and checked as surrounding content. This version edits text inside tables when safe; it does not add rows, redesign tables, replace logos, or reconstruct arbitrary layouts.

## Metadata and signatures

Pedit preserves original author, creator, creation date, title, subject and keywords. Custom PDF Info entries remain in the document. Original metadata is included in the report as source information, not a verified claim about its authenticity.

The output honestly records a new modification date and Pedit/PyMuPDF as producer. For XMP packets, revision dates and producer are updated only in top-level descriptions of the document subject (empty `rdf:about`, or legacy descriptions without a subject identifier). Named subjects, blank-node resources and nested structured descriptions retain their metadata. When the packet contains only other resources, a separate document description is added. Qualified revision properties update their scalar `rdf:value` and retain qualifiers in supported `rdf:parseType="Resource"` and nested `rdf:Description` forms. Unsupported or ambiguous revision representations are rejected. XMP serialization and namespace prefixes can change; malformed packets are rejected. Exported standard metadata and the revised XMP packet are read back and compared. Original producer metadata remains available in the report.

**Signed PDFs are blocked.** Detection includes signature fields, signature flags, signature dictionaries, byte-range indicators and certification references. This is conservative presence detection, not cryptographic validation. Pedit does not copy signatures, assert an edited document is still signed, spoof editing software, or backdate modifications. Use an unsigned source and have the authorized signer sign the completed revision.

Every verification report includes source/output SHA-256 hashes, source/output standard metadata, edit details, checks performed and limitations. A hash binds the report to the supplied bytes; it does not establish authorship or trust. The report itself is not digitally signed.

## Limits

This is **not a universal pixel-perfect PDF editor**. Changes necessarily alter the selected content. Matching source glyphs in one renderer does not guarantee unseen replacement glyphs have the correct contextual shaping or appearance in every renderer. Variable kerning, complex scripts, scans, outlines, rotated or transparent text, layered text, stroked text, clipped/transformed text, unavailable fonts, unsupported encodings and complex color workflows may be refused.

Runtime visual verification uses MuPDF at 300 DPI. The test suite also checks a fixture with Poppler, when installed; Poppler is not a second runtime export gate. Print separations, original color-space operators, tagged-PDF accessibility, PDF/A/PDF/X compliance, JavaScript/actions and all low-level document objects are not exhaustively certified. Conformance-sensitive documents require an appropriate independent validator and professional review. The source reconstruction gate is deliberately strict and can reject documents that a less conservative editor would accept.

Limits: 25 MB input/output, 60 pages, 20 instructions, 40 million rendered pixels per page at verification resolution, and 16,384 pixels per rendered side. Page sizes are checked before processing. PDF processing runs in one spawned worker process per server instance so health requests remain responsive and MuPDF is not shared across threads. All modified-page previews are available at 144 DPI; validation runs at 300 DPI. Large files may take time to verify.

## Tests

```bash
python -m pytest -q
node --test src/tests/ui.test.cjs
```

The suite covers embedded fonts, tracking, glyph metrics, fine print, table rules, metadata/XMP, signature detection, graphics/images/interactions, source reconstruction, all-page checks, and the web upload/download/report workflow. Install Poppler (`pdftoppm`) to run the optional independent renderer test.

## Local operation and licensing

Use with trusted documents on localhost. Temporary PDF/report downloads expire after one hour; unrequested expired files are not automatically removed. This is not a hardened public upload service. Public deployment requires authentication, parser isolation, automatic cleanup, resource limits and a privacy/security review.

PyMuPDF is dual licensed under AGPL and commercial terms. Review the license before proprietary or hosted use: https://pymupdf.io/licensing

## Next work

Validate representative, non-sensitive PDFs from multiple authoring tools against expected edits. Then add a font-encoding-aware content-stream editor for cases currently refused, keeping the existing verification gates.
