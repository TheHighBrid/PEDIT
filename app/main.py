"""Pedit server. Files are processed in memory; no remote AI calls."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .editor import Edit, EditError, MAX_PDF_BYTES, edit_pdf, parse_instructions

ROOT = Path(__file__).resolve().parent
app = FastAPI(title='Pedit', description='Layout-preserving PDF replacement engine', version='0.1.0')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
async def home():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
async def health():
    return {'status': 'ok', 'engine': 'pymupdf', 'storage': 'in-memory'}


@app.post('/api/edit')
async def edit(file: UploadFile = File(...), instructions: str = Form(''), edits_json: str = Form('[]')):
    if not (file.filename or '').lower().endswith('.pdf'):
        raise HTTPException(422, 'Upload an original .pdf file, not an image.')
    try:
        # Limit before buffering arbitrarily large input (zip-bomb defense for basic use).
        data = await file.read(MAX_PDF_BYTES + 1)
        edits = parse_instructions(instructions)
        try:
            structured = json.loads(edits_json)
            if not isinstance(structured, list):
                raise ValueError('must be a list')
            edits.extend(Edit(**item) for item in structured)
        except (ValueError, TypeError) as exc:
            raise EditError(f'Invalid structured edits: {exc}') from exc
        pdf, report = edit_pdf(data, edits)
        return {'pdf_base64': base64.b64encode(pdf).decode('ascii'), 'report': report}
    except EditError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()
