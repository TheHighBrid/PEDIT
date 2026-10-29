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

Upload an unsigned PDF. Open **Browse source text and typography**, choose a page, and click **Inspect page**. The read-only browser shows the original page and filterable text candidates with font, size, and color. Select a candidate to fill a replacement row with its exact text, page, and occurrence. This does not promise that the candidate is editable: the full precision checks still run at export. Scanned pages report that no selectable text was found, and signature indicators are shown before editing. Inspection is limited to 2,000 text spans per page; use manual rows for denser pages.

Add structured replacement rows to choose a page, a match occurrence, and left, right or automatic alignment. Leave a row's replacement blank to remove the selected text. You can also enter one quoted instruction per line:

```text
Replace "2025" with "2026"
On page 2, replace "Draft" with "Final"
Replace all "CONFIDENTIAL" with "PUBLIC"
```

In the default reconstruction mode, matches must be contiguous within one text span. The original-operator mode can also match adjacent spans on one continuous line. The match number is one-based across the chosen page, or across the document if no page is specified. Repeated matches require an explicit match number, a page that uniquely identifies one occurrence, or **ALL MATCHES** / `Replace all`. ALL MATCHES is scoped to the chosen page when one is specified. A match number and ALL MATCHES cannot be combined. Up to 20 rows may be added; quoted instructions count toward the same 20-edit limit. Source-browser selections carry a page/position anchor so they keep targeting the selected text when switching editing methods. Changing the original text, page, match number, all-matches option, or uploaded file releases that anchor. The original upload is never overwritten. Review each modified page using the page selector and before/after slider. Download the PDF and its JSON verification report only after validation succeeds.

## Preserve original text operators

Under **EDITING METHOD**, choose **Preserve original text operators** when the reconstruction mode refuses a supported phrase split across font styles, or when original text-state operators need to remain in place. The API accepts `mode=stream` as a multipart field; the default is `mode=precision`.

This mode changes decoded string operands of `Tj`, `TJ`, `'` and `"` operators. It retains their font selections, text matrices, horizontal scaling, character/word spacing, numeric `TJ` adjustments and surrounding page operators. A phrase can cross adjacent regular/bold/italic spans; each replacement character inherits the font and source position of its corresponding original character. This is position-preserving replacement, not automatic restyling or reflow.

The first implementation deliberately has a narrow encoding contract:

- Source text on each inspected page and replacement text must be printable ASCII, using unmodified PDF standard Helvetica, Times or Courier fonts in supported Standard/WinAnsi encodings. Embedded fonts, Type 0/Type 3 fonts, custom width/encoding dictionaries and complex-script shaping are outside this mode. Use the default mode for supported embedded-font edits.
- Replacements must have the same character count and the same per-character font advance, including the last glyph. Space positions must stay unchanged. Digits are a common supported case; arbitrary wider letters, insertions and removals are refused.
- Text must be horizontal, opaque, unlayered fill text. Stroked/hidden text, text-generated clipping, external graphics states, inline images and shared page content streams are refused. Existing rectangular page clipping and supported horizontal scaling stay in place.
- Every decoded text-show glyph must map unambiguously to both rendered text traces and the character inventory. Pages with text inside Form XObjects, overlapping glyphs or other unmappable text are refused. A native request scans pages in its search scope, so specify a page when other pages use unsupported fonts.
- The native parser limits each decoded content stream to 10 MB, each page to 100,000 shown glyphs, and container/string nesting to 32 levels, in addition to existing file/page/render limits.

Before export, source string operands are decoded and reserialized with their original values. The resulting page must reproduce the source pixel-for-pixel at 300 DPI. After replacement, the same all-page pixel comparison, independent character inventory, document geometry, graphics/interactions and metadata gates apply. Signed inputs remain blocked. Edited string operands are serialized as hex strings; their syntax can change while the surrounding operators remain intact.

The verification report identifies `editing_mode`, the count of replacement occurrences, and each affected text span's `edit_method`. A mixed-font replacement has multiple span records but counts as one replacement. Native `tracking` is null because the original operators can contain several different kerning adjustments; no single tracking value is invented.

## Precision checks

- Reuses an extractable embedded font or the original PDF Base-14 font. Missing glyphs and ambiguous resources are rejected.
- Reads individual character origins, baseline, size and paint color. Tracks explicit letter spacing without treating extraction-generated spaces as document content.
- Retains original glyph origins when character advances match. Otherwise uses measured uniform tracking and the replacement glyphs' own widths. Irregular kerning that cannot be inferred safely is rejected. Single-character expansion may use uniform tracking measured from the rest of the same source span. An isolated character or irregular surrounding spacing cannot establish that tracking and is refused. Text is never silently squeezed or resized to fit.
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

## Planning performance

Each edit operation caches source text extraction per page, text paint traces per affected page, and font resolution per page/font. Source dictionaries are reused for character inventory and released before the full-document pixel validation pass. Text-only extraction disables image payload decoding, as recommended in the [PyMuPDF extraction documentation](https://pymupdf.readthedocs.io/en/latest/app1.html). Post-save extraction remains independent and every page is still verified at 300 DPI.

A local synthetic benchmark compared main commit `1b84841` with this change using PyMuPDF 1.26.6: six pages, 100 source spans per page, 20 replacements, both engines warmed, then three alternating runs. Median total engine time was 2.204 s before and 1.842 s after, approximately 16% lower. This is one workload, not a general speed guarantee; raster verification dominates small documents. A regression test verifies two source edits reuse one source extraction, one trace read, and one font resolution, while retaining separate post-save extraction.

## Tests

```bash
python -m pytest -q
node --test src/tests/ui.test.cjs
```

The suite covers embedded fonts, tracking, glyph metrics, fine print, table rules, metadata/XMP, signature detection, graphics/images/interactions, source reconstruction, all-page checks, source inspection and duplicate occurrence targeting, request races, control-character validation, and the web upload/download/report workflow. Native-mode tests verify operator bytes, mixed-font phrases, kerning/scaling, string escapes, anchored selection, safe refusals, serializer-proof failure, and an additional optional Poppler comparison. Install Poppler (`pdftoppm`) to run the optional independent renderer test.

## Local operation and licensing

Use with trusted documents on localhost. Temporary PDF/report downloads expire after one hour; unrequested expired files are not automatically removed. This is not a hardened public upload service. Public deployment requires authentication, parser isolation, automatic cleanup, resource limits and a privacy/security review.

PyMuPDF is dual licensed under AGPL and commercial terms. Review the license before proprietary or hosted use: https://pymupdf.io/licensing

## Next work

Validate representative, non-sensitive PDFs from multiple authoring tools against expected edits. The next fidelity work is extending the native path to embedded-font encodings and carefully supported width-changing edits, with explicit handling of text movement and shaping, keeping the existing verification gates. Complex scripts and arbitrary layout reconstruction remain unsupported. A separate renderer as a runtime export gate would provide stronger cross-renderer evidence. Automatic expiry cleanup and a bounded job queue remain useful server hardening work before any public deployment.
