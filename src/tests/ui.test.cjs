const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function harness(){
  const elements=new Map(),rows=[],downloads=[],created=[];
  class Element {
    constructor(){this.listeners={};this.children=new Map();this.value='';this.disabled=false;this.valid=true;this.files=[];this.hidden=true;this.classList={add(){},remove(){},toggle(){}};this.style={};}
    addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
    querySelector(selector){if(!this.children.has(selector))this.children.set(selector,new Element());const e=this.children.get(selector);if(selector==='.alignment'&&!e.value)e.value='auto';return e;}
    appendChild(e){rows.push(e);}
    focus(){this.focused=true;}
    checkValidity(){return this.valid;}
    removeAttribute(name){delete this[name];}
    replaceChildren(...items){this.options=items;}
    remove(){const i=rows.indexOf(this);if(i>=0)rows.splice(i,1);}
    click(){downloads.push({href:this.href,name:this.download});}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const context={document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>rows,body:{appendChild(){}}},
    URL:{createObjectURL(){created.push('blob:output');return 'blob:output';},revokeObjectURL(){}},
    FormData:class{constructor(){this.values={};}append(key,value){this.values[key]=value;}},Blob,Uint8Array,atob,console,fetch:async()=>{throw Error('Unexpected fetch');}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8'),context);
  get('comparison').value='50';
  rows[0].querySelector('.old-text').value='2024';rows[0].querySelector('.new-text').value='2025';
  context.chooseFile({name:'A.pdf',size:123});
  return {context,get,rows,created,downloads,run:()=>get('run').listeners.click[0]()};
}
function success(){return {ok:true,status:200,json:async()=>({download_url:'/api/download/token',report_url:'/api/report/token',previews:[{page:1,before_image:'before',after_image:'after'}],preview_page:1,edits:[],integrity:{signature_status:'Unsigned'},limitation:'Review output.'})};}
test('changing the selected PDF discards an in-flight response',async()=>{
  const h=harness();let resolve;h.context.fetch=()=>new Promise(r=>resolve=r);
  const request=h.run();h.context.chooseFile({name:'B.pdf',size:124});resolve(success());await request;
  assert.equal(h.get('results').hidden,true);
  assert.equal(h.get('uploadTitle').textContent,'B.pdf');assert.notEqual(h.get('previewState').textContent,'VALIDATION PASSED');
  h.get('download').listeners.click[0]();assert.equal(h.downloads.length,0);
});
test('changing instructions discards an in-flight response',async()=>{
  const h=harness();let resolve;h.context.fetch=()=>new Promise(r=>resolve=r);
  const request=h.run();h.get('instructions').listeners.input[0]();resolve(success());await request;
  assert.equal(h.get('results').hidden,true);
});
test('HTML proxy errors produce an HTTP status message',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:false,status:413,json:async()=>{throw SyntaxError('Unexpected token <');}});
  await h.run();assert.equal(h.get('message').textContent,'Edit failed (HTTP 413).');assert.equal(h.get('run').disabled,false);
});
test('JSON errors retain the actionable server detail',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:false,status:422,json:async()=>({detail:'Missing glyph'})});
  await h.run();assert.equal(h.get('message').textContent,'Missing glyph');
});
test('invalid numeric match fields prevent the request',async()=>{
  const h=harness();h.rows[0].querySelector('.occurrence').value='1.5';h.rows[0].querySelector('.occurrence').valid=false;
  let calls=0;h.context.fetch=async()=>{calls++;return success();};await h.run();
  assert.equal(calls,0);assert.match(h.get('message').textContent,/positive whole numbers/);
});
test('valid output downloads with the submitted document name',async()=>{
  const h=harness();h.context.fetch=async()=>success();await h.run();h.get('download').listeners.click[0]();
  assert.equal(h.get('results').hidden,false);assert.equal(h.downloads[0].name,'A_pedit.pdf');
  h.context.chooseFile({name:'B.pdf',size:100});h.get('download').listeners.click[0]();assert.equal(h.downloads.length,1);
});

function inspection(spans) { return {ok:true,status:200,json:async()=>({page:1,pages:2,image:'source',spans,signature_present:false})}; }
const candidates=[{text:'Year 2025',font:'Helvetica',size:11,color:'#000000'}, {text:'2025',font:'Helvetica',size:11,color:'#000000'}];
test('source selection scopes duplicate text to its exact page occurrence',async()=>{
  const h=harness();h.get('sourcePage').value='1';h.context.fetch=async()=>inspection(candidates);
  await h.get('inspectSource').listeners.click[0]();
  h.context.selectSourceSpan(1);
  const row=h.rows[1];
  assert.equal(row.querySelector('.old-text').value,'2025');
  assert.equal(row.querySelector('.page-number').value,'1');
  assert.equal(row.querySelector('.occurrence').value,'2');
  assert.equal(row.querySelector('.new-text').focused,true);
});
test('inspection responses are discarded after document or page changes',async()=>{
  for (const change of ['file','page']) {
    const h=harness();h.get('sourcePage').value='1';let resolve;
    h.context.fetch=()=>new Promise(r=>resolve=r);
    const pending=h.get('inspectSource').listeners.click[0]();
    if(change==='file')h.context.chooseFile({name:'B.pdf',size:1});
    else h.get('sourcePage').listeners.input[0]();
    resolve(inspection(candidates));await pending;
    assert.equal(h.get('sourceImage').hidden,true);
    assert.equal(h.get('sourceSpans').options.length,0);
  }
});
test('source filter keeps the original occurrence index',async()=>{
  const h=harness();h.get('sourcePage').value='1';h.context.fetch=async()=>inspection(candidates);
  await h.get('inspectSource').listeners.click[0]();
  h.get('sourceFilter').value='year';h.context.renderSourceSpans();
  assert.equal(h.get('sourceSpans').options.length,1);
  assert.match(h.get('sourceSpans').options[0].textContent,/Year 2025/);
});
test('match number and all matches cannot be submitted together',async()=>{
  const h=harness();h.rows[0].querySelector('.all-occurrences').checked=true;
  h.rows[0].querySelector('.occurrence').value='1';let calls=0;
  h.context.fetch=async()=>{calls++;return success();};await h.run();
  assert.equal(calls,0);assert.match(h.get('message').textContent,/not both/);
});
test('failed inspection shows an actionable status without source candidates',async()=>{
  const h=harness();h.get('sourcePage').value='1';
  h.context.fetch=async()=>({ok:false,status:413,json:async()=>{throw Error('HTML');}});
  await h.get('inspectSource').listeners.click[0]();
  assert.equal(h.get('sourceStatus').textContent,'Inspection failed (HTTP 413).');
  assert.equal(h.get('sourceSpans').options.length,0);
});

test('source selection keeps its position anchor when switching editing modes',async()=>{
  const h=harness();h.rows[0].querySelector('.old-text').value='';h.rows[0].querySelector('.new-text').value='';
  h.get('sourcePage').value='1';let form;
  h.context.fetch=async(url,options)=>{
    if(url==='/api/inspect')return inspection([{...candidates[0],origin:[70,100]}]);
    form=options.body.values;return success();
  };
  await h.get('inspectSource').listeners.click[0]();h.context.selectSourceSpan(0);
  h.rows[0].querySelector('.new-text').value='Year 2026';
  h.get('editMode').value='stream';h.get('editMode').listeners.change[0]();
  await h.run();
  assert.equal(form.mode,'stream');const edit=JSON.parse(form.edits_json)[0];
  assert.deepEqual(edit.origin,[70,100]);assert.equal(edit.occurrence,null);
  h.context.chooseFile({name:'B.pdf',size:3});
  assert.equal(h.rows[0].sourceAnchor,null);
});
test('changing editing mode discards an in-flight edit',async()=>{
  const h=harness();let resolve;h.context.fetch=()=>new Promise(r=>resolve=r);
  const pending=h.run();h.get('editMode').listeners.change[0]();resolve(success());await pending;
  assert.equal(h.get('results').hidden,true);
});
