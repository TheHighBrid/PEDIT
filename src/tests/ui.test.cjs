const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');
function harness(){
  const elements=new Map();
  class Element{
    constructor(){this.listeners={};this.files=[];this.style={};this.value='';this.classList={add(){},remove(){}};}
    addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
    replaceChildren(){}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const ctx={document:{getElementById:get,createElement:()=>new Element()},FormData:class{append(){}},fetch:async()=>{throw Error('Unexpected fetch');}};
  vm.createContext(ctx);
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
  get('file').files=[{name:'A.pdf'}];get('prompt').value='Replace "2025" with "2026"';
  return {ctx,get,run:()=>get('submit').listeners.click[0]()};
}
test('non-JSON server errors show the HTTP status',async()=>{
  const h=harness();h.ctx.fetch=async()=>({ok:false,status:413,json:async()=>{throw SyntaxError('Unexpected token <');}});
  await h.run();assert.equal(h.get('error').textContent,'Edit failed (HTTP 413).');
  assert.equal(h.get('download').style.display,'none');assert.equal(h.get('submit').disabled,false);
});
test('server error detail survives JSON parsing',async()=>{
  const h=harness();h.ctx.fetch=async()=>({ok:false,status:422,json:async()=>({detail:'Edit refused'})});
  await h.run();assert.equal(h.get('error').textContent,'Edit refused');
});
test('file controls and drops cannot change the document during processing',async()=>{
  const h=harness();let resolve;h.ctx.fetch=()=>new Promise(r=>resolve=r);
  const pending=h.run();assert.equal(h.get('file').disabled,true);assert.equal(h.get('prompt').disabled,true);
  const event={preventDefault(){},dataTransfer:{files:[{name:'B.pdf'}]}};
  for(const handler of h.get('drop').listeners.drop)handler(event);
  assert.equal(h.get('file').files[0].name,'A.pdf');
  resolve({ok:false,status:500,json:async()=>null});await pending;
  assert.equal(h.get('file').disabled,false);assert.equal(h.get('prompt').disabled,false);
});
