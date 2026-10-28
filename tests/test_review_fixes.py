import asyncio
import json
import os
import time

import fitz
import httpx
import pytest
from fastapi.testclient import TestClient

from app.editor import Edit, EditError, _document, _get_font, edit_pdf
from app.main import app
from test_editor import make_pdf, spans


@pytest.mark.parametrize('kwargs', [
    {'old':None}, {'new':None}, {'old':4}, {'new':[]},
    {'page':'1'}, {'page':True}, {'page':1.5}, {'page':0},
    {'occurrence':1.5}, {'occurrence':True}, {'occurrence':-1},
    {'align':None}, {'align':[]}, {'align':'center'},
])
def test_bad_structured_edit_returns_422(kwargs):
    values={'old':'2024','new':'2025',**kwargs}
    with TestClient(app) as client:
        response=client.post('/api/edit',files={'file':('sample.pdf',make_pdf(False),'application/pdf')},
                             data={'edits_json':json.dumps([values])})
    assert response.status_code==422,response.text
    assert 'Invalid structured edits' in response.json()['detail']


def test_exact_bold_font_is_used_when_regular_resource_comes_first():
    doc=fitz.open();p=doc.new_page()
    p.insert_text((50,50),'Regular',fontname='helv')
    p.insert_text((50,100),'2024',fontname='hebo')
    out,report=edit_pdf(doc.tobytes(),[Edit('2024','2025')])
    changed=next(s for s in spans(out) if s['text']=='2025')
    assert changed['font']=='Helvetica-Bold'
    assert report['changes'][0]['font']==changed['font']


def test_ambiguous_exact_font_resources_are_refused():
    class Page:
        def get_fonts(self,full):
            return [(4,'ttf','Type0','AAAAAA+Arial','F1'),(5,'ttf','Type0','BBBBBB+Arial','F2')]
    with pytest.raises(EditError,match='several resources'):
        _get_font(None,Page(),'Arial')


def test_empty_or_family_prefix_font_is_never_selected():
    class Page:
        def get_fonts(self,full):
            return [(4,'n/a','Type1','','F1'),(5,'n/a','Type1','Helvetica-Bold','F2')]
    with pytest.raises(EditError,match='No substitution'):
        _get_font(None,Page(),'Helvetica')


@pytest.mark.parametrize('reverse',[False,True])
def test_expanded_replacements_cannot_overlap(reverse):
    doc=fitz.open();p=doc.new_page()
    p.insert_text((60,100),'AAA');p.insert_text((120,100),'BBB')
    edits=[Edit('AAA','LONG LONG LONG'),Edit('BBB','CCC')]
    with pytest.raises(EditError,match='overlap'):
        edit_pdf(doc.tobytes(),list(reversed(edits)) if reverse else edits)


@pytest.mark.parametrize('dimensions',[(14400,14400),(100000,5)])
def test_large_pages_are_refused_before_rendering(dimensions):
    doc=fitz.open();doc.new_page(width=dimensions[0],height=dimensions[1])
    with pytest.raises(EditError,match='too large'):
        _document(doc.tobytes())


def test_pdf_worker_is_isolated_serial_and_health_stays_responsive():
    async def scenario():
        async with app.router.lifespan_context(app):
            pool=app.state.pdf_pool
            pid=await asyncio.wrap_future(pool.submit(os.getpid))
            assert pid!=os.getpid()
            pool.submit(time.sleep,1)
            queued_pid=pool.submit(os.getpid)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                pending=asyncio.create_task(client.post('/api/edit',
                    files={'file':('sample.pdf',make_pdf(False),'application/pdf')},
                    data={'edits_json':json.dumps([{'old':'2024','new':'2025'}])}))
                await asyncio.sleep(.05)
                response=await asyncio.wait_for(client.get('/api/health'),timeout=.5)
                assert response.status_code==200
                assert not pending.done()
                result=await pending
                assert result.status_code==200,result.text
            assert await asyncio.wrap_future(queued_pid)==pid
    asyncio.run(scenario())


def test_pillow_difference_counts_preserve_threshold_and_overlapping_masks():
    from PIL import Image
    from app.editor import _difference_counts
    before=Image.new('RGB',(5,4),(0,0,0));after=before.copy()
    after.putpixel((0,0),(17,17,17))
    after.putpixel((2,2),(30,30,30))
    after.putpixel((4,3),(40,40,40))
    after.putpixel((4,0),(16,16,16))  # unchanged according to the existing threshold
    masks=[(0,0,3,3),(2,2,4,4)]
    assert _difference_counts(before,after,masks)==(8,1,2)
