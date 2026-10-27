import fitz
from fastapi.testclient import TestClient
from app import app
from test_engine import make_pdf

client=TestClient(app)


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
