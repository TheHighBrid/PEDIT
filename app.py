"""PrecisionPDF local web app. Run: uvicorn app:app --reload"""
from __future__ import annotations
import base64
import json
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from pdf_worker import lifespan, run_pdf_job, WorkerUnavailable

from engine import PrecisionError, edit_pdf

ROOT=Path(__file__).resolve().parent
STORE=Path(tempfile.mkdtemp(prefix='precisionpdf_'))
TTL_SECONDS=60*60
app=FastAPI(title='PrecisionPDF', docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount('/static',StaticFiles(directory=ROOT/'src'/'static'),name='static')

@app.get('/', response_class=HTMLResponse)
def index():
    return (ROOT/'src'/'static'/'index.html').read_text(encoding='utf8')

@app.post('/api/edit')
async def edit(file: UploadFile=File(...), instructions: str=Form(''), edits_json: str=Form('[]')):
    # Per-request size limit, including a check after reading.
    data=await file.read(25*1024*1024+1)
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(422,detail='Upload a .pdf document.')
    try:
        try:
            structured=json.loads(edits_json)
            if not isinstance(structured,list) or any(not isinstance(item,dict) for item in structured):
                raise ValueError('Expected a list of edit objects.')
        except (ValueError,TypeError) as exc:
            raise PrecisionError(f'Invalid structured edits: {exc}') from exc
        result=await run_pdf_job(app,edit_pdf,data,instructions,structured)
    except PrecisionError as e:
        raise HTTPException(422,detail=str(e))
    except WorkerUnavailable as e:
        raise HTTPException(503,detail=str(e))
    except Exception:
        raise HTTPException(500,detail='Unexpected PDF processing failure. No output was created.')
    token=secrets.token_urlsafe(24)
    (STORE/f'{token}.pdf').write_bytes(result.pop('pdf'))
    report={'integrity':result['integrity'],'edits':result['edits'],'validation':result['validation'],'limitations':result['limitation']}
    (STORE/f'{token}.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    for preview in result['previews']:
        preview['before_image']='data:image/png;base64,'+base64.b64encode(preview.pop('before_png')).decode()
        preview['after_image']='data:image/png;base64,'+base64.b64encode(preview.pop('after_png')).decode()
    result['report_url']=f'/api/report/{token}'
    result['before_image']='data:image/png;base64,'+base64.b64encode(result.pop('before_png')).decode()
    result['after_image']='data:image/png;base64,'+base64.b64encode(result.pop('after_png')).decode()
    result['download_url']=f'/api/download/{token}'
    return result

def stored_file(token: str, extension: str):
    if not token or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in token):
        raise HTTPException(404,'Not found')
    path=STORE/f'{token}.{extension}'
    if not path.is_file(): raise HTTPException(404,'File expired or not found.')
    if time.time()-path.stat().st_mtime>TTL_SECONDS:
        path.unlink(missing_ok=True)
        raise HTTPException(404,'File expired.')
    return path

@app.get('/api/download/{token}')
def download(token: str):
    return FileResponse(stored_file(token,'pdf'),media_type='application/pdf',filename='precision-edited.pdf')

@app.get('/api/report/{token}')
def report_download(token: str):
    return FileResponse(stored_file(token,'json'),media_type='application/json',filename='pedit-verification.json')

@app.get('/health')
def health(): return {'status':'ok','mode':'local-only'}
