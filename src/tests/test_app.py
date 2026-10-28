import fitz
import pytest
from fastapi.testclient import TestClient
from app import app
from test_engine import make_pdf

client=TestClient(app)

@pytest.fixture(scope='module',autouse=True)
def running_worker():
    with client:
        yield


def test_ui_and_export_with_verification_report():
    assert client.get('/').status_code==200
    assert client.get('/static/index.html').status_code==200
    response=client.post('/api/edit',files={'file':('test.pdf',make_pdf(second=True),'application/pdf')},
                         data={'instructions':'Replace all "2025" with "2026"'})
    assert response.status_code==200,response.text
    data=response.json()
    assert [p['page'] for p in data['previews']]==[1,2]
    assert all(p['before_image'].startswith('data:image/png;base64,') for p in data['previews'])
    output=client.get(data['download_url'])
    assert output.status_code==200
    doc=fitz.open(stream=output.content,filetype='pdf')
    assert all('2026' in p.get_text() for p in doc)
    report=client.get(data['report_url'])
    assert report.status_code==200
    assert report.json()['integrity']['verification_dpi']==300


def test_invalid_edit_never_returns_download():
    response=client.post('/api/edit',files={'file':('test.pdf',make_pdf(),'application/pdf')},
                         data={'instructions':'Replace "missing" with "new"'})
    assert response.status_code==422
    assert 'download_url' not in response.json()


def test_worker_is_isolated_and_health_is_responsive_while_edit_is_queued():
    import asyncio
    import os
    import time
    import httpx
    async def scenario():
        pool=app.state.pdf_pool
        pid=await asyncio.wrap_future(pool.submit(os.getpid))
        assert pid!=os.getpid()
        pool.submit(time.sleep,1)
        queued_pid=pool.submit(os.getpid)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as browser:
            pending=asyncio.create_task(browser.post('/api/edit',
                files={'file':('test.pdf',make_pdf(),'application/pdf')},
                data={'instructions':'Replace "2025" with "2026"'}))
            await asyncio.sleep(.05)
            response=await asyncio.wait_for(browser.get('/health'),timeout=.5)
            assert response.status_code==200
            assert not pending.done()
            result=await pending
            assert result.status_code==200,result.text
        assert await asyncio.wrap_future(queued_pid)==pid
    asyncio.run(scenario())
