"""PrecisionPDF local web app. Run: uvicorn app:app --reload"""
from __future__ import annotations
import base64
import os
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from engine import PrecisionError, edit_pdf

ROOT=Path(__file__).resolve().parent
STORE=Path(tempfile.mkdtemp(prefix='precisionpdf_'))
TTL_SECONDS=60*60
app=FastAPI(title='PrecisionPDF', docs_url=None, redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

@app.get('/', response_class=HTMLResponse)
def index():
    return (ROOT/'static'/'index.html').read_text(encoding='utf8')

@app.post('/api/edit')
async def edit(file: UploadFile=File(...), instructions: str=Form(...)):
    # Per-request size limit, including a check after reading.
    data=await file.read(25*1024*1024+1)
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(422,detail='Upload a .pdf document.')
    try:
        result=edit_pdf(data,instructions)
    except PrecisionError as e:
        raise HTTPException(422,detail=str(e))
    except Exception:
        raise HTTPException(500,detail='Unexpected PDF processing failure. No output was created.')
    token=secrets.token_urlsafe(24)
    (STORE/f'{token}.pdf').write_bytes(result.pop('pdf'))
    result['before_image']='data:image/png;base64,'+base64.b64encode(result.pop('before_png')).decode()
    result['after_image']='data:image/png;base64,'+base64.b64encode(result.pop('after_png')).decode()
    result['download_url']=f'/api/download/{token}'
    return result

@app.get('/api/download/{token}')
def download(token: str):
    if not token or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in token):
        raise HTTPException(404,'Not found')
    path=STORE/f'{token}.pdf'
    if not path.is_file(): raise HTTPException(404,'File expired or not found.')
    if time.time()-path.stat().st_mtime>TTL_SECONDS:
        path.unlink(missing_ok=True)
        raise HTTPException(404,'File expired.')
    return FileResponse(path,media_type='application/pdf',filename='precision-edited.pdf')

@app.get('/health')
def health(): return {'status':'ok','mode':'local-only'}
