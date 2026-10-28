import json
import fitz
import pytest
from fastapi.testclient import TestClient
from app import app
from engine import edit_pdf, PrecisionError
from test_engine import make_pdf


def two_occurrences():
    doc=fitz.open();page=doc.new_page();page.insert_text((50,100),'2025',fontsize=11);page.insert_text((300,100),'2025',fontsize=11)
    return doc.tobytes()


def test_specific_occurrence_and_removal():
    result=edit_pdf(two_occurrences(),'', [{'old':'2025','new':'2026','occurrence':2}])
    doc=fitz.open(stream=result['pdf'],filetype='pdf')
    assert doc[0].get_text().count('2025')==1 and doc[0].get_text().count('2026')==1
    result=edit_pdf(two_occurrences(),'', [{'old':'2025','new':'','occurrence':1}])
    doc=fitz.open(stream=result['pdf'],filetype='pdf')
    assert doc[0].get_text().count('2025')==1


def test_right_alignment_preserves_right_edge():
    data=two_occurrences();before=fitz.open(stream=data,filetype='pdf');old=before[0].search_for('2025')[0]
    result=edit_pdf(data,'',[{'old':'2025','new':'20260','align':'right','occurrence':1}])
    doc=fitz.open(stream=result['pdf'],filetype='pdf');new=doc[0].search_for('20260')[0]
    assert new.x1==pytest.approx(old.x1,abs=.02)


@pytest.mark.parametrize('edit',[{'old':'2025','new':None},{'old':'2025','new':'2026','occurrence':1.5},{'old':'2025','new':'2026','page':True},{'old':'2025','new':'2026','align':'center'}])
def test_invalid_edits_rejected(edit):
    with pytest.raises(PrecisionError): edit_pdf(make_pdf(),'', [edit])


def test_web_structured_edit_download_and_report():
    with TestClient(app) as client:
        response=client.post('/api/edit',files={'file':('original.pdf',two_occurrences(),'application/pdf')},data={'edits_json':json.dumps([{'old':'2025','new':'2026','occurrence':2}])})
        assert response.status_code==200,response.text
        data=response.json()
        assert client.get(data['download_url']).status_code==200
        assert client.get(data['report_url']).json()['integrity']['verification_dpi']==300
        html=client.get('/').text
        assert '/static/app.js' in html and client.get('/static/styles.css').status_code==200
