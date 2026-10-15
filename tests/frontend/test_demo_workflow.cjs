const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../../app/static/demo.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../../app/templates/investigation_new.html'), 'utf8');
const reply = (data, status=200) => ({ok:status<400,status,json:async () => data});
function boot(fetch) {
  const elements=new Map(), presets=[];
  for (const match of html.matchAll(/data-signal-preset="([^"]+)"/g)) presets.push({dataset:{signalPreset:match[1]},addEventListener(event,callback){this.click=callback;}});
  const document={getElementById(id){if(!elements.has(id)) elements.set(id,{dataset:{projectId:'project'},style:{},hidden:true,value:'1,1,1,1,1,1,1,1',addEventListener(event,callback){this.click=callback;}});return elements.get(id);},querySelectorAll(){return presets;}};
  const window={location:{}};
  vm.runInNewContext(script,{document,window,fetch,AbortController,setTimeout,clearTimeout});
  return {elements,document,window,presets};
}
test('failed starts reuse their draft and show the real backend error',async () => {
  let creates=0,starts=0;
  const ctx=boot(async (url,options) => {
    if(url.endsWith('/approve')) return reply({});
    if(url==='/api/investigations') {creates++;assert.equal(JSON.parse(options.body).model_mode,'mock');return reply({id:'pending'});}
    if(url.endsWith('/start')) return ++starts===1 ? reply({detail:'Investigation capacity is busy'},429) : reply({});
    return reply({status:'draft'});
  });
  await ctx.document.getElementById('start-btn').click();
  assert.match(ctx.elements.get('start-error').textContent,/capacity is busy/);
  await ctx.document.getElementById('start-btn').click();
  assert.equal(creates,1);assert.equal(starts,2);assert.equal(ctx.window.location.href,'/investigations/pending');
});
test('lost start response resumes a running investigation instead of starting again',async () => {
  let starts=0,creates=0;
  const ctx=boot(async url=>{
    if(url.endsWith('/approve'))return reply({});
    if(url==='/api/investigations'){creates++;return reply({id:'running'});}
    if(url.endsWith('/start')){starts++;throw new Error('connection lost');}
    return reply({status:'running'});
  });
  await ctx.document.getElementById('start-btn').click();await ctx.document.getElementById('start-btn').click();
  assert.equal(creates,1);assert.equal(starts,1);assert.equal(ctx.window.location.href,'/investigations/running');
});
test('expired login is actionable instead of a generic approve failure',async()=>{
  const ctx=boot(async()=>reply({detail:'Session expired; sign in again'},401));
  await ctx.document.getElementById('start-btn').click();
  assert.match(ctx.elements.get('start-error').textContent,/session expired/i);
  assert.equal(ctx.elements.get('demo-signin').hidden,false);
});
test('exhausted signal exploration has an explicit reset without bypassing budgets',async()=>{
  let creates=0,pairs=0;
  const ctx=boot(async url=>{
    if(url==='/api/investigations'){creates++;return reply({id:'probe-'+creates});}
    pairs++;return reply({detail:'Investigation runs budget exhausted'},429);
  });
  await ctx.document.getElementById('run-btn').click();
  assert.equal(ctx.elements.get('reset-exploration').hidden,false);
  assert.equal(ctx.elements.get('result-section').style.display,'none');
  ctx.document.getElementById('reset-exploration').click();
  await ctx.document.getElementById('run-btn').click();
  assert.equal(creates,2);assert.equal(pairs,2);
});
test('boundary preset is the known witness and clears stale displayed results',()=>{
  const ctx=boot();ctx.presets[0].click();
  const signal=ctx.elements.get('signal-values').value.split(',').map(Number);
  assert.deepEqual(signal,[1,1,1,1,1,1,1,1.0000001192092896]);
  assert.equal(ctx.elements.get('result-section').style.display,'none');
});
test('unchanged control stays decisively away from the tie boundary',()=>{
  const ctx=boot();ctx.presets[1].click();
  const signal=ctx.elements.get('signal-values').value.split(',').map(Number);
  assert.deepEqual(signal,[.5,.5,.5,.5,1.5,1.5,1.5,1.5]);
  const powers=signal.map(x=>(x/2)**2);
  const ratio=powers.slice(4).reduce((a,b)=>a+b,0)/powers.reduce((a,b)=>a+b,0);
  assert(ratio>.8);
});
