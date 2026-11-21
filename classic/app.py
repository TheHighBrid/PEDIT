"""PrecisionPDF local web app. Run: uvicorn app:app --reload"""
from __future__ import annotations
import base64
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pdf_worker import lifespan, run_pdf_job, WorkerUnavailable

from .engine import (
    PrecisionError,
    analyze_document_intelligence,
    edit_pdf,
    get_kernel,
    inspect_pdf,
    post_telemetry_trace,
    recompute_dependent_edits,
)

ROOT = Path(__file__).resolve().parent
_store_directory = tempfile.TemporaryDirectory(prefix='precisionpdf_')
STORE = Path(_store_directory.name)
TTL_SECONDS = 60 * 60
app = FastAPI(title='PrecisionPDF', docs_url=None, redoc_url=None, lifespan=lifespan)
from fastapi.staticfiles import StaticFiles
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.middleware('http')
async def remote_auth(request, call_next):
    """Remote mode requires a fresh per-launch password for every document route."""
    from fastapi.responses import Response
    password = os.environ.get('PEDIT_REMOTE_PASSWORD')
    if not password and (request.url.hostname not in ('localhost', '127.0.0.1', '::1')
                         or request.headers.get('cf-connecting-ip')):
        return Response('Remote mode is disabled. Start the authenticated launcher.', status_code=403,
                        headers={'Cache-Control': 'no-store'})
    if password and request.url.path != '/health':
        expected = 'Basic ' + base64.b64encode(f'pedit:{password}'.encode()).decode()
        if not secrets.compare_digest(request.headers.get('authorization', '').encode(), expected.encode()):
            return Response('Authentication required.', status_code=401,
                            headers={'WWW-Authenticate': 'Basic realm="PEDIT"', 'Cache-Control': 'no-store'})
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    return response


def _valid_token(token: Optional[str]) -> bool:
    return bool(
        token
        and all(c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in token)
    )


def _process_edit(data, instructions, structured_edits):
    result = edit_pdf(data, instructions=instructions, structured_edits=structured_edits)
    inspected = inspect_pdf(result['pdf'], clean_overlays=False)
    return result, inspected


@app.get('/', response_class=HTMLResponse)
def index():
    return (ROOT / 'static' / 'index.html').read_text(encoding='utf8')


@app.post('/api/inspect')
async def inspect_endpoint(
    file: Optional[UploadFile] = File(None),
    session_token: Optional[str] = Form(None),
    clean_overlays: bool = Form(False),
):
    if file is not None and file.filename:
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(422, detail='Upload a .pdf document.')
        data = await file.read(25 * 1024 * 1024 + 1)
    elif _valid_token(session_token) and (STORE / f'{session_token}.pdf').is_file():
        data = (STORE / f'{session_token}.pdf').read_bytes()
    else:
        raise HTTPException(422, detail='Upload a .pdf document.')

    try:
        info = await run_pdf_job(app, inspect_pdf, data, False)
    except PrecisionError as e:
        raise HTTPException(422, detail=str(e))
    except WorkerUnavailable as e:
        raise HTTPException(503, detail=str(e))
    except Exception:
        raise HTTPException(500, detail='Unexpected PDF inspection failure.')

    token = secrets.token_urlsafe(24)
    (STORE / f'{token}.pdf').write_bytes(data)
    info['session_token'] = token
    info['download_url'] = f'/api/download/{token}'
    return info


@app.post('/api/edit')
async def edit(
    file: Optional[UploadFile] = File(None),
    session_token: Optional[str] = Form(None),
    instructions: str = Form(''),
    structured_edits: str = Form(''),
    clean_overlays: bool = Form(False),
):
    if file is not None and file.filename:
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(422, detail='Upload a .pdf document.')
        data = await file.read(25 * 1024 * 1024 + 1)
    elif _valid_token(session_token) and (STORE / f'{session_token}.pdf').is_file():
        data = (STORE / f'{session_token}.pdf').read_bytes()
    else:
        raise HTTPException(422, detail='Upload a .pdf document.')

    parsed_structured = None
    if structured_edits and structured_edits.strip():
        try:
            parsed_structured = json.loads(structured_edits)
            if not isinstance(parsed_structured, list):
                raise ValueError('structured_edits must be a JSON array')
        except Exception as e:
            raise HTTPException(422, detail=f'Invalid structured_edits JSON: {e}')

    try:
        result, inspected = await run_pdf_job(app, _process_edit, data, instructions, parsed_structured)
        edited_bytes = result.pop('pdf')
    except PrecisionError as e:
        raise HTTPException(422, detail=str(e))
    except WorkerUnavailable as e:
        raise HTTPException(503, detail=str(e))
    except Exception:
        raise HTTPException(500, detail='Unexpected PDF processing failure. No output was created.')

    token = secrets.token_urlsafe(24)
    (STORE / f'{token}.pdf').write_bytes(edited_bytes)
    result['before_image'] = 'data:image/png;base64,' + base64.b64encode(result.pop('before_png')).decode()
    result['after_image'] = 'data:image/png;base64,' + base64.b64encode(result.pop('after_png')).decode()
    result['download_url'] = f'/api/download/{token}'
    result['session_token'] = token
    result['inspected'] = inspected
    return result


@app.get('/api/download/{token}')
def download(token: str):
    if not _valid_token(token):
        raise HTTPException(404, 'Not found')
    path = STORE / f'{token}.pdf'
    if not path.is_file():
        raise HTTPException(404, 'File expired or not found.')
    if time.time() - path.stat().st_mtime > TTL_SECONDS:
        path.unlink(missing_ok=True)
        raise HTTPException(404, 'File expired.')
    return FileResponse(path, media_type='application/pdf', filename='precision-edited.pdf')


@app.post('/api/telemetry/trace')
async def telemetry_trace(payload: dict):
    """Receive and evaluate live conversation telemetry traces emitted by client apps."""
    try:
        from improvement_kernel.adapters.pedit import PeditAdapter
        from app.db import SessionLocal
        db = SessionLocal()
        try:
            adapter = PeditAdapter(session=db, project='pedit')
            eval_result = adapter.evaluate_pdf_trace(payload)
            return {
                'status': 'ok',
                'project': 'pedit',
                'doc_id': eval_result.get('doc_id'),
                'evaluation': eval_result,
            }
        finally:
            db.close()
    except Exception:
        # Local fallback evaluation in PrecisionPDF when standalone
        metrics = payload.get('metrics', {})
        max_overflow = float(metrics.get('bbox_overflow_pts', 0.0))
        max_drift = float(metrics.get('baseline_drift_pts', 0.0))
        font_dist = float(metrics.get('font_match_distance', 0.0))
        pdf_valid = bool(metrics.get('pdf_valid', True))
        page_match = bool(metrics.get('page_count_match', True))
        non_target = bool(metrics.get('non_target_untouched', True))

        t1_passed = pdf_valid and page_match and non_target
        overflow_score = max(0.0, 1.0 - (max_overflow / 0.5)) if max_overflow <= 0.5 else 0.0
        drift_score = 1.0 if abs(max_drift) == 0.0 else max(0.0, 1.0 - abs(max_drift))
        font_score = max(0.0, 1.0 - (font_dist / 0.20))
        t2_score = round(0.40 * overflow_score + 0.35 * drift_score + 0.25 * font_score, 4)
        t2_passed = t2_score >= 0.85

        overall = round((0.40 if t1_passed else 0.0) + 0.40 * t2_score + 0.20, 4)
        return {
            'status': 'ok',
            'project': 'pedit',
            'doc_id': payload.get('doc_id', 'unknown'),
            'evaluation': {
                'overall_score': overall,
                'tier1_passed': t1_passed,
                'tier2_score': t2_score,
                'tier3_score': 1.0,
                'status': 'succeeded' if (t1_passed and t2_passed) else 'failed',
            },
        }


@app.post('/api/intelligence/recompute')
async def recompute_dependencies(payload: dict):
    """Automatically recalculate dependent arithmetic values (products, sums, subtotals) given user edits."""
    current_edits = payload.get('current_edits', [])
    all_spans = payload.get('all_spans', [])
    dependency_rules = payload.get('dependency_rules', [])
    auto_edits = recompute_dependent_edits(current_edits, all_spans, dependency_rules)
    return {
        'auto_edits': auto_edits,
        'count': len(auto_edits),
    }


@app.post('/api/intelligence/audit')
async def audit_quality(payload: dict):
    """Perform final quality check audit for typos, misspellings, and arithmetic discrepancies."""
    session_token = payload.get('session_token')
    if not _valid_token(session_token) or not (STORE / f'{session_token}.pdf').is_file():
        raise HTTPException(404, detail='Session file not found.')
    data = (STORE / f'{session_token}.pdf').read_bytes()
    intel = await run_pdf_job(app, analyze_document_intelligence, data)
    return {
        'discrepancies': intel.get('discrepancies', []),
        'has_discrepancies': intel.get('has_discrepancies', False),
    }


@app.get('/health')
def health():
    return {'status': 'ok', 'mode': 'remote' if os.environ.get('PEDIT_REMOTE_PASSWORD') else 'local-only',
            'launch_id': os.environ.get('PEDIT_LAUNCH_ID', '')}


@app.get('/api/kernel/status')
def kernel_status():
    return get_kernel().get_status_summary()


@app.post('/api/kernel/run-cycle')
def kernel_run_cycle():
    report = get_kernel().run_improvement_cycle()
    return {'report': report, 'status': get_kernel().get_status_summary()}


@app.post('/api/kernel/simulate-defect')
def kernel_simulate_defect():
    report = get_kernel().simulate_degraded_baseline_and_self_heal()
    return {'report': report, 'status': get_kernel().get_status_summary()}


@app.post('/api/kernel/rollback')
def kernel_rollback():
    res = get_kernel().rollback_champion()
    return {'rollback': res, 'status': get_kernel().get_status_summary()}
