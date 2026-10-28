# PrecisionPDF (local-first proof-of-concept)

A small web app for conservative, typography-aware editing of **existing, digitally-generated PDFs**.

**Workflow:** Upload PDF → write plain-English `replace "old" with "new"` instructions → view visual before/after → download only if verification passes. All operations run locally without external AI, accounts, or API keys.

## Start

Requires Python 3.10+.

```bash
cd Pedit
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

**macOS shortcut:** If you have Python 3.10+, you can double-click `start_mac.command` (or launch it in Terminal). It creates a local virtual environment, installs dependencies, starts the server and opens your browser. Keep the Terminal window open while using the app. The first launch needs internet to install dependencies. If macOS blocks the downloaded script, run it explicitly from Terminal instead.

## Supported instructions

```text
Replace "2025" with "2026"
On page 2, replace "Draft" with "Final"
Replace all "CONFIDENTIAL" with "PUBLIC"
```

One line per instruction. Matches must be contiguous within a single PDF text span. Repeated matches need a page, or an explicit **Replace all** directive.

## What is actually preserved and verified

- Reads original font, font size, fill color, individual character bounds and baseline.
- Uses the **same embedded font program** where extractable and glyphs are present, or same PDF Base-14 standard font.
- Places replacement text at its original glyph baseline, and refuses missing glyphs or collisions with surrounding text.
- Redacts the selected original text as actual PDF text, rather than painting opaque white over it. Text outside the target region is preserved.
- Visual regression at 144 DPI of *every modified page*, excluding only edit rectangles and a small antialiasing margin, must be pixel-identical.
- Checks that the new text is selectable in the PDF and preserved page count.
- Original upload is never overwritten.

## Limits (important)

This MVP is not a universal, pixel-perfect editor. PDF is an output-graphics format, not a reliably editable layout model. Some PDFs contain embedded subsets lacking desired glyphs, irregular kerning, image-only text, inaccessible fonts, transparency, rotated letters, or multi-span words. This version deliberately **rejects** ambiguous or unsafe edits, rather than silently using a lookalike font, shifting the layout, or leaving a ghosted previous value.

Replacing one word of a larger run may change the pixels within its edited region, including antialiasing. Visual validation of unaffected areas at 144 DPI does not guarantee identical appearance in all renderers, or that modified words use exactly the original kerning. It does not check tagged-PDF accessibility, hyperlink preservation within edits, digital signatures, color-managed CMYK equivalence, PDF/A conformance, encrypted PDFs, or safety of arbitrary uploads against unknown parser vulnerabilities. Original PDF digital signatures will generally be invalidated by editing. This version is for trusted local documents and human review, not a public internet service.

## Security/privacy

Files are processed locally. Download files are placed in the machine's OS temp directory and URLs expire after one hour (old temp files are not automatically vacuumed). If exposing the app to the internet, add authentication, sandboxing, upload constraints, malware scanning, isolation, automatic deletion, logging policy and rate limits.

## Suggested next iterations

1. Add a PDF content-stream editor with font encoding/kerning state preservation for **surgically replaceable** same-width text objects.
2. Support live on-page target selection with multiple-occurrence disambiguation.
3. Add optional AI to translate informal instructions into structured target replacements, with review before applying. AI must never redraw the PDF.
4. Add explicit OCR/scan handling as a separate, lower-certainty workflow.
5. Test with a multi-source corpus and compare using multiple independent renderers at 300 DPI.

## Tests

```bash
python -m pytest -q
```

`sample/` includes original and edited demo PDFs for a quick first run. `tests` creates synthetic sample PDFs to check typography metadata, replacement text, pixel invariance, ambiguity handling and collision failure.

## Licensing

PyMuPDF is dual-licensed under AGPL and commercial terms. Check the license obligations before using this engine in a proprietary or hosted commercial product: https://pymupdf.io/licensing

## Review fixes for the Pedit app branch

The Pedit application in this branch starts with `python -m uvicorn app.main:app`. The root legacy `app.py` is not this branch's entry point. The macOS launcher uses the package entry point.

PDF edits run in one spawned worker process per server instance, keeping ASGI health requests responsive and avoiding shared MuPDF threads. Large pages are refused before rendering: maximum 10 million square points, 20 million rendered pixels, and 16,384 pixels per side at the current verification scale.

Structured edit fields validate strings, positive integer page/match values, and supported alignment. Font lookup prefers exact normalized resource names, permits only unambiguous Book/Regular aliases, and never matches font-family prefixes. Replacement rectangles are checked pairwise for collisions. Verification masks are transformed into rendered coordinates on rotated pages, while edit bounds remain in unrotated text coordinates. Stroked, fill-and-stroke, hidden, layered or transparent source text is refused when its paint state cannot be preserved. Pillow masks and histograms replace Python pixel loops without changing the existing threshold of 16.

Changing the PDF, instructions, or replacement rows clears previous results and invalidates pending responses. Downloads use the submitted file name. Non-JSON server responses show an HTTP status message.

Validation commands:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/ui.test.cjs
```

Issue coverage: #3/#9/#14 font identity; #4/#10/#15 replacement collisions; #5 stale results; #6/#11/#12 structured input validation; #13 rendering limits; #16 pixel-loop performance; #17 event-loop blocking and MuPDF isolation; #18 non-JSON errors. The selected-target removal review finding is already covered by tests for partial occurrence edits and replacements containing the original phrase.

PR #2 is a separate precision-engine branch. These tests validate this branch independently; combining the implementations requires reconciling their application entry points.
