# Pedit

**Alter the text. Not the document.**

Pedit is a preservation-first PDF editor. Upload an original PDF, enter exact changes, preview the before/after pages, inspect the regression report, and download the modified PDF.

### What works in v0.1

- Replace or remove **selectable, horizontal text contained in one PDF text span**.
- Preserve the original text's **embedded font program** (or a standard Base-14 font), size, RGB color, and baseline.
- Preserve the original *right edge* for right-column values such as résumé dates (automatically or explicitly).
- Keep untouched page content in place; apply localized redaction, not whole-page reconstruction.
- Reject missing/ambiguous targets, unsupported fonts/glyphs, custom tracking, text collisions, and edge overflow.
- Verify the replacement remains searchable, the old text is removed, and rendered pixels outside replacement zones remain unchanged (strict threshold).
- Compare original and edited renderings using a blend slider before download.
- No API keys, cloud storage, or LLM are required. Documents are processed in memory.

### Run locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. To run tests: `pip install -r requirements-dev.txt && pytest -q`.

### Editing instructions

Use the UI to enter the exact source and replacement, optionally page and alignment. Leave the new text blank to remove the original text. You can also enter multiple newline-separated instructions:

```text
replace "Mar 2024 - Dec 2024" with "Mar 2024 - Dec 2025"
on page 1 replace "EDUCATION & SYSTEMS" with "EDUCATION"
remove "BILINGUAL ENGLISH/FRENCH"
```

By default, a text target must occur only once. Specify a page number or match occurrence when needed. No guesswork.

### Reality check

PDFs are *drawing instructions*, not editable Word documents. Exact in-place edits cannot be guaranteed for arbitrary PDFs. Pedit v0.1 only accepts safe single-span text. Replacing text changes the original content stream, even though output is visually validated. This version does **not** support scanned pages, OCR, image-based text, rotated/skewed text, reflow, multi-span selections, custom color spaces, embedded font glyphs missing from the source, or advanced transparency. Upload the **actual PDF**, not a screenshot.

Font matching is not a promise of perfect metrics for every PDF. Pedit fails closed rather than silently using a replacement font or decreasing type size. The pixel regression is intentionally limited to text outside the editing regions; users should visually verify the replacement itself before relying on the export.

**Privacy:** Files are not persisted by this app and are not sent to any third-party AI service. If you deploy publicly, add authentication, rate limits, request/CPU timeouts and PDF sandboxing first.

### Stack

- FastAPI + PyMuPDF backend
- Plain HTML/CSS/JS frontend, no build tools
- Pillow image-based regression comparison
- Pytest validation suite

**License:** no license selected yet. The repository owner should choose an explicit license before external redistribution.
