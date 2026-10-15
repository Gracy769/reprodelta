const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const template = fs.readFileSync(path.join(__dirname, '../../app/templates/investigation_running.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1].replace('{{ investigation_id | tojson }}','"run"');
async function progress(data,status=200) {
  const elements=new Map();let stopped=false,interval;
  const document={getElementById(id){if(!elements.has(id))elements.set(id,{style:{},hidden:true,textContent:''});return elements.get(id);}};
  vm.runInNewContext(script,{document,window:{location:{}},AbortController,setTimeout,clearTimeout,
    setInterval(callback){interval=callback;return 1;},clearInterval(){stopped=true;},
    fetch:async()=>({ok:status<400,status,json:async()=>data})});
  await new Promise(resolve=>setImmediate(resolve));
  return {elements,stopped,interval};
}
test('progress stops for expired authentication and offers sign-in',async()=>{
  const ctx=await progress({detail:'Session expired'},401);
  assert.equal(ctx.stopped,true);
  assert.match(ctx.elements.get('status-msg').textContent,/session expired/i);
  assert.equal(ctx.elements.get('progress-signin').hidden,false);
});
test('missing investigation is a visible terminal error instead of unknown forever',async()=>{
  const ctx=await progress({detail:'Investigation not found'},404);
  assert.equal(ctx.stopped,true);assert.match(ctx.elements.get('status-msg').textContent,/not found/);
});
test('cancelled and draft investigations do not poll forever',async()=>{
  for(const status of ['cancelled','draft']) {
    const ctx=await progress({status,events:[]});
    assert.equal(ctx.stopped,true);assert.equal(ctx.elements.get('progress-restart').hidden,false);
  }
});
test('transient service failure preserves retry behavior',async()=>{
  const ctx=await progress({detail:'temporarily unavailable'},503);
  assert.equal(ctx.stopped,false);assert.match(ctx.elements.get('status-msg').textContent,/Retrying/);
});
test('benchmark stage keeps the user informed while comparisons run',async()=>{
  const ctx=await progress({status:'running',events:[{event_type:'demonstration_started',payload:{total_pairs:96}},
    {event_type:'demonstration_progress',payload:{finished_pairs:37,total_pairs:96,case:'sensor_offset',strategy:'random_valid'}}]});
  assert.match(ctx.elements.get('status-msg').textContent,/37\/96/);
  assert.match(ctx.elements.get('detail-comparison').textContent,/sensor_offset/);
  assert.equal(ctx.elements.get('icon-comparison').className,'stage-icon running');
});
