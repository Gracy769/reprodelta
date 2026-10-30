const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const shipped = fs.readFileSync(path.join(__dirname, '../../app/static/workspace.js'), 'utf8');

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.classList = {toggle(){}, add(){}, remove(){}}; }
  append(...nodes) { nodes.forEach(node => {node.parent = this; this.children.push(node);}); }
  prepend(...nodes) { nodes.forEach(node => {node.parent = this;}); this.children.unshift(...nodes); }
  get firstChild() {return this.children[0];}
  get options() {return this.children;}
  remove() {this.parent.children.splice(this.parent.children.indexOf(this),1);}
  addEventListener(event, handler) {this.listeners[event] = handler;}
  setAttribute() {}
  scrollIntoView() {}
  focus() {}
}
function boot(fetch) {
  const elements = new Map();
  const document = {getElementById(id) {if(!elements.has(id)) elements.set(id,new Element()); return elements.get(id);},
    createElement(tag) {return new Element(tag);}, createTextNode(text) {const node = new Element('text'); node.textContent=text; return node;}, querySelectorAll() {return [];}};
  const context = {document, fetch, URL, AbortController, crypto: {randomUUID: () => 'fixed-idempotency-key'}, location: {}, window: {setTimeout,clearTimeout, matchMedia: () => ({matches:true})}};
  vm.runInNewContext(shipped.replace('  start();', '  globalThis.reviewTest = {renderReview, configureReplay, configureApprovedRun, renderApprovedFinding, openSavedProjectRun, loadRecentProjectRuns, loadProfiles, loadOperatorProfiles, configureShare, configureProjectHypothesis, state};'), context);
  return {...context, elements};
}
function all(node) {return [node,...node.children.flatMap(all)];}
const change = {provider:'github',repoKey:'lab/project',number:7,title:'Upgrade',url:'https://github.com/lab/project/pull/7'};
const result = {evidence_level:'Diff review',complete:false,head_sha:'b'.repeat(40),dependency_files:['requirements.txt'],
  dependency_changes:[{package:'numpy'}],changed_file_count_checked:101,scope:'101 files inspected',limitations:['Incomplete diff'],
  next_actions:['Resolve pins','Declare output','Run paired replay'], findings:[{title:'<script>attack</script>', evidence:'requirements.txt',url:'https://evil.test/read', action:'Review'}]};

test('review exposes incomplete evidence, revision, actions and inert source text', () => {
  const ctx=boot(); ctx.reviewTest.renderReview(result,change);
  const nodes=all(ctx.elements.get('review-result-body'));
  assert(nodes.some(n => n.textContent?.includes('Incomplete evidence')));
  assert(nodes.some(n => n.textContent?.includes('bbbbbbbbbbbb')));
  assert(nodes.some(n => n.textContent === '<script>attack</script>'));
  assert(!nodes.some(n => n.href === 'https://evil.test/read'));
  assert(nodes.some(n => n.textContent === 'Declare output'));
  assert(nodes.some(n => n.textContent?.includes('No repository code was executed')));
});

test('historical review cannot display an unchecked run contract', () => {
  const ctx=boot(async () => {throw Error('Historical review must not make a run request')});
  ctx.reviewTest.renderReview(result,change);
  const nodes=all(ctx.elements.get('review-result-body'));
  assert(nodes.some(n => n.textContent?.includes('saved review records an older commit')));
  assert(!nodes.some(n => n.textContent?.includes('paired-run contract')));
});

test('fresh eligible review opens the contract without a second configure click', async () => {
  const ctx=boot(async (url) => ({ok:true,json:async () => url.endsWith('/eligibility')
    ? {eligible:true,base_sha:'a'.repeat(40),head_sha:'b'.repeat(40),numpy_versions:{base:'1.26.4',head:'2.0.2'}}
    : {contract_hash:'c'.repeat(64)}}));
  ctx.reviewTest.state.selectedChange=change;
  ctx.reviewTest.renderReview({...result,freshReview:true},change);
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  const nodes=all(ctx.elements.get('review-result-body'));
  assert(nodes.some(n => n.textContent === 'Review this paired-run contract'));
  assert(nodes.some(n => n.textContent?.includes('Approve and start paired run')));
});

test('unsupported review never loads a contract or offers approval', async () => {
  const requests=[];
  const ctx=boot(async (url) => {requests.push(url); return {ok:true,json:async () => ({eligible:false,reason:'Unsupported numerical kernel'})};});
  const parent=new Element();
  await ctx.reviewTest.configureReplay(result,change,parent);
  assert(all(parent).some(n => n.textContent?.includes('Unsupported numerical kernel')));
  assert(!requests.some(url => url.endsWith('/contract')));
  assert(!all(parent).some(n => n.type === 'submit'));
});

test('paired replay carries approved contract, revision, manifest and strategy', async () => {
  const requests=[];
  const ctx=boot(async (url,options) => {
    requests.push({url,options});
    return {ok:true,json:async () => url.endsWith('/eligibility')
      ? {eligible:true,base_sha:'a'.repeat(40),head_sha:'b'.repeat(40),numpy_versions:{base:'1.26.4',head:'2.0.2'}}
      : url.endsWith('/contract') ? {contract_hash:'c'.repeat(64)} : {investigation_id:'job-id'}};
  });
  const parent=new Element();
  await ctx.reviewTest.configureReplay(result,change,parent);
  const form=all(parent).find(n => n.tagName === 'form');
  const strategy=all(form).find(n => n.tagName === 'select'); strategy.value='random';
  const checkbox=all(form).find(n => n.type === 'checkbox'); checkbox.checked=true;
  await form.listeners.submit({preventDefault(){}});
  const eligibility=requests.find(r => r.url.endsWith('/eligibility'));
  const approval=requests.find(r => r.url.endsWith('/contract/approve'));
  const request=requests.find(r => r.url.endsWith('/paired-replay'));
  assert.equal(approval.options.method,'POST');
  assert(requests.indexOf(eligibility)<requests.indexOf(approval));
  assert(requests.indexOf(approval)<requests.indexOf(request));
  assert.equal(request.options.method,'POST');
  assert.deepEqual(JSON.parse(request.options.body),{repository:'lab/project',number:7,expected_head_sha:'b'.repeat(40),dependency_path:'requirements.txt',contract_hash:'c'.repeat(64),search_strategy:'random'});
  assert.equal(ctx.location.href,'/investigations/job-id');
});

test('paired replay cannot start until the contract is approved', async () => {
  const requests=[];
  const ctx=boot(async (url,options) => {
    requests.push({url,options});
    return {ok:true,json:async () => url.endsWith('/eligibility')
      ? {eligible:true,base_sha:'a'.repeat(40),head_sha:'b'.repeat(40),numpy_versions:{base:'1.26.4',head:'2.0.2'}}
      : {contract_hash:'c'.repeat(64)}};
  });
  const parent=new Element();
  await ctx.reviewTest.configureReplay(result,change,parent);
  const form=all(parent).find(n => n.tagName === 'form');
  await form.listeners.submit({preventDefault(){}});
  assert(all(form).some(n => n.textContent?.includes('Review and approve')));
  assert(!requests.some(r => r.url.endsWith('/paired-replay')));
});

test('missing manifest explains setup gap and cannot start paired replay', async () => {
  const ctx=boot(async () => {throw Error('No network request expected')});
  const parent=new Element();
  await ctx.reviewTest.configureReplay({...result,dependency_files:[]},change,parent);
  assert(all(parent).some(n => n.textContent?.includes('No supported dependency manifest')));
  assert(!all(parent).some(n => n.type === 'submit'));
});

test('approved project run requires exact plan acknowledgement', async () => {
  const requests=[];
  const profileId='11111111-1111-4111-8111-111111111111';
  const plan={eligible:true, rate_configured:true, execution_enabled:true,
    repository:'lab/project',base_sha:'a'.repeat(40),head_sha:'b'.repeat(40),
    contract_hash:'c'.repeat(64), max_sandbox_seconds:1800,max_wall_seconds:900,
    candidate_budget:1,candidate_strategy:'sweep',candidate_seed:0,
    cost_ceiling_usd:0.2, source_scope:'same source', source_transfer_notice:'Source sent to E2B',
    old_environment:{numpy_version:'1.26.4'},new_environment:{numpy_version:'2.0.2'}};
  const ctx=boot(async (url,options) => {requests.push({url,options}); return {ok:true,json:async () =>
    url.includes('/plan') ? plan : url.endsWith('/project-runs') ? {id:'job-1',status:'queued'}
    : {status:'completed',result_json:{changed_conclusion:true,repeatable:true,inconclusive:false,old:{ratio:0.4,conclusion:'below_threshold'},
      new:{ratio:0.6,conclusion:'above_threshold'},sandbox_seconds:2,estimated_sandbox_cost_usd:0.0002,input_sha256:'d'.repeat(64)}}};});
  ctx.reviewTest.state.jwt='test-token';
  ctx.reviewTest.state.selectedChange=change;
  const parent=new Element();
  ctx.reviewTest.configureApprovedRun(change,[{id:profileId,version:1,repository:'lab/project',draft_json:{name:'Numeric decision'}}],parent);
  const selects=all(parent).filter(n => n.tagName==='select');
  selects[0].value=profileId; selects[1].value='1'; selects[2].value='sweep';
  const check=all(parent).find(n => n.textContent==='Check this run ↗');
  await check.listeners.click();
  const form=all(parent).find(n => n.tagName==='form');
  const consent=all(form).find(n => n.type==='checkbox');
  await form.listeners.submit({preventDefault(){}});
  assert(!requests.some(r => r.options?.method==='POST'));
  consent.checked=true;
  await form.listeners.submit({preventDefault(){}});
  const start=requests.find(r => r.url.endsWith('/project-runs') && r.options?.method==='POST');
  assert(start);
  const body=JSON.parse(start.options.body);
  assert.equal(body.expected_head_sha,plan.head_sha);
  assert.equal(body.approved_contract_hash,plan.contract_hash);
  assert.equal(body.approved_cost_ceiling_usd,plan.cost_ceiling_usd);
  assert.equal(body.candidate_budget,1);
  assert.equal(body.candidate_strategy,'sweep');
  assert.equal(body.source_transfer_approved,true);
  assert(all(parent).some(n => n.textContent?.includes('outcome changed for this sample')));
  assert(all(parent).some(n => n.textContent?.includes('Repeatability')));
  assert(all(parent).some(n => n.textContent?.includes('Each tested input ran twice')));
  assert(all(parent).some(n => n.textContent?.includes('No candidate repair was executed')));
});

test('saved project run warns when the PR revision has changed', async () => {
  const runId='11111111-1111-4111-8111-111111111111';
  const saved={status:'completed',spec_json:{repository:'lab/project',change_number:7,
    old_environment_lock_sha256:'a'.repeat(64),new_environment_lock_sha256:'b'.repeat(64)},
    result_json:{kind:'approved_project_search',attempted:2,candidate_budget:2,strategy:'sweep',seed:0,
      changed_conclusion:false,inconclusive:false,observations:[{input_sha256:'c'.repeat(64),repeatable:true,
        old:{ratio:0.4,conclusion:'below_threshold'},new:{ratio:0.4,conclusion:'below_threshold'}}],
      source_sha256:'d'.repeat(64),sandbox_seconds:3,estimated_sandbox_cost_usd:0.0003}};
  const ctx=boot(async (url) => ({ok:true,json:async () => url.endsWith('/revision')
    ? {changed:true,saved_head_sha:'e'.repeat(40)} : saved}));
  ctx.reviewTest.state.jwt='test-token';
  await ctx.reviewTest.openSavedProjectRun(runId);
  const nodes=all(ctx.elements.get('saved-project-run'));
  assert(nodes.some(n => n.textContent?.includes('The PR moved since this run')));
  assert(nodes.some(n => n.textContent?.includes('No violation found within tested scope')));
  assert(nodes.some(n => n.textContent?.includes('Repeatability')));
});

test('private replay download appears only for a witness and requires acknowledgement', () => {
  const ctx=boot();
  const card=new Element();
  const finding={kind:'approved_project_sample',changed_conclusion:true,repeatable:true,
    witness_input:{x:0.5},old:{ratio:0.4,conclusion:'below_threshold'},
    new:{ratio:0.6,conclusion:'above_threshold'},input_sha256:'a'.repeat(64)};
  ctx.reviewTest.renderApprovedFinding(card,finding,{id:'11111111-1111-4111-8111-111111111111'});
  const nodes=all(card);
  const download=nodes.find(n => n.textContent === 'Download private replay ZIP ↗');
  const checkbox=nodes.find(n => n.type === 'checkbox');
  assert(download && checkbox);
  assert.equal(download.disabled,true);
  checkbox.checked=true; checkbox.listeners.change();
  assert.equal(download.disabled,false);
  const noWitness=new Element();
  ctx.reviewTest.renderApprovedFinding(noWitness,{...finding,changed_conclusion:false,witness_input:null},
    {id:'11111111-1111-4111-8111-111111111111'});
  assert(!all(noWitness).some(n => n.textContent === 'Download private replay ZIP ↗'));
});

test('project activity shows bounded revision checks, scoped cost and approved profiles', async () => {
  const runs=Array.from({length:6},(_,index)=>({id:`run-${index}`,status:'completed',
    spec_json:{repository:'lab/project',change_number:index+1,head_sha:'b'.repeat(40)},
    estimated_sandbox_cost_usd:0.000001,usage_uncertain:index===0,
    result_json:{changed_conclusion:index===0}}));
  const calls=[];
  const ctx=boot(async (url) => {
    calls.push(url);
    if(url==='/api/project-runs') return {ok:true,json:async()=>({runs})};
    if(url==='/api/project-profiles') return {ok:true,json:async()=>({profiles:[
      {id:'p1',status:'approved',repository:'lab/project',version:1,draft_json:{name:'Lab'}},
      {id:'p2',status:'draft',repository:'lab/other',version:1,draft_json:{name:'Other'}}]})};
    if(url.endsWith('/revision')) return {ok:true,json:async()=>({changed:url.includes('run-0')})};
    throw Error(`Unexpected request ${url}`);
  });
  ctx.reviewTest.state.jwt='test-token';
  await ctx.reviewTest.loadProfiles();
  await ctx.reviewTest.loadRecentProjectRuns();
  await new Promise(setImmediate);
  const profile=all(ctx.elements.get('project-profile-summary'));
  assert(profile.some(n=>n.textContent?.includes('1 approved project version')));
  const summary=all(ctx.elements.get('project-run-summary'));
  assert(summary.some(n=>n.textContent?.includes('Latest 6 investigations')));
  assert(summary.some(n=>n.textContent==='up to $0.000006'));
  const rows=all(ctx.elements.get('recent-project-runs'));
  assert(rows.some(n=>n.textContent?.includes('PR changed since this run')));
  assert(rows.some(n=>n.textContent==='Open saved finding ↗'));
  assert.equal(calls.filter(url=>url.endsWith('/revision')).length,5);
});

test('operator validation controls are only rendered after the approver endpoint succeeds', async () => {
  const denied=boot(async () => ({ok:false,status:403}));
  denied.reviewTest.state.jwt='test-token';
  await denied.reviewTest.loadOperatorProfiles();
  assert.notEqual(denied.elements.get('operator-profiles')?.hidden, false);

  const profile={id:'11111111-1111-4111-8111-111111111111',repository:'lab/project',version:1,
    owner_id:'github:owner',profile_digest:'a'.repeat(64),contract_hash:'b'.repeat(64),
    draft_json:{name:'Numerical adapter',adapter_module:'package.adapter',adapter_function:'run',
      dependency_path:'requirements.txt',sample_input:{signal:0.5},
      contract:{output_selector:{field_name:'ratio'},conclusion:{operator:'gt',threshold:0.5}},
      source_transfer_consent:true}};
  const allowed=boot(async () => ({ok:true,status:200,json:async () => ({profiles:[profile]})}));
  allowed.reviewTest.state.jwt='test-token';
  await allowed.reviewTest.loadOperatorProfiles();
  assert.equal(allowed.elements.get('operator-profiles').hidden,false);
  const nodes=all(allowed.elements.get('operator-profile-list'));
  assert(nodes.some(n => n.textContent?.includes('Start bounded sample validation')));
  assert(nodes.some(n => n.textContent?.includes('Source transfer consent')));
});

test('operator approval shows both repeats and hides legacy single-run validation', async () => {
  const profile={id:'11111111-1111-4111-8111-111111111111',repository:'lab/project',version:1,
    owner_id:'github:owner',profile_digest:'a'.repeat(64),contract_hash:'b'.repeat(64),
    draft_json:{name:'Adapter',adapter_module:'package.adapter',adapter_function:'run',
      dependency_path:'requirements.txt',sample_input:{signal:0.5},
      contract:{output_selector:{field_name:'ratio'},conclusion:{operator:'gt',threshold:0.5}},
      source_transfer_consent:true}};
  let repeatable=false;
  const ctx=boot(async (url) => ({ok:true,status:200,json:async () => url.endsWith('/operator/pending')
    ? {profiles:[profile]} : {validations:[{id:'job',status:'completed',result_json:{
        sample_validated:true,repeatable,source_sha:'c'.repeat(40),source_sha256:'d'.repeat(64),
        old:{ratio:0.4},old_repeat:{ratio:0.4},new:{ratio:0.6},new_repeat:{ratio:0.6}},
        spec_json:{old:{},new:{}}}]}}));
  ctx.reviewTest.state.jwt='test-token';
  await ctx.reviewTest.loadOperatorProfiles();
  const card=all(ctx.elements.get('operator-profile-list')).find(n=>n.tagName==='details');
  card.open=true;
  card.listeners.toggle();
  await new Promise(setImmediate);
  assert(!all(card).some(n=>n.textContent==='Approve this project version ↗'));
  repeatable=true;
  card.listeners.toggle();
  await new Promise(setImmediate);
  assert(all(card).some(n=>n.textContent?.includes('Old repeat')));
  assert(all(card).some(n=>n.textContent?.includes('New repeat')));
  assert(all(card).some(n=>n.textContent==='Approve this project version ↗'));
});

test('public finding link requires preview and explicit acknowledgement', async () => {
  let posted=0;
  const ctx=boot(async (url,options={}) => ({ok:true,status:200,json:async () => {
    if (url.endsWith('/share-preview')) return {scope:'Bounded sample',tested_inputs:1,
      changed_conclusion:true,inconclusive:false,limitations:['Source omitted.']};
    if (options.method === 'POST') {posted += 1; return {url:'/shared/token'};}
    return {shares:[]};
  }}));
  ctx.reviewTest.state.jwt='test-token';
  const card=new Element();
  ctx.reviewTest.configureShare('11111111-1111-4111-8111-111111111111',card);
  const preview=all(card).find(n => n.textContent === 'Preview public copy ↗');
  assert(preview);
  assert.equal(posted,0);
  await preview.listeners.click();
  const consent=all(card).find(n => n.type === 'checkbox');
  const create=all(card).find(n => n.textContent === 'Create revocable link ↗');
  assert.equal(create.disabled,true);
  consent.checked=true; consent.listeners.change();
  assert.equal(create.disabled,false);
  await create.listeners.click();
  assert.equal(posted,1);
  assert(all(card).some(n => n.href === '/shared/token'));
});

test('model advice requires a numeric preview and consent and stays untested text', async () => {
  let posted=0;
  const ctx=boot(async (url, options={}) => {
    if(url.endsWith('/model-hypothesis') && options.method==='POST') {
      posted += 1;
      return {ok:true,json:async()=>({status:'completed',model_id:'nvidia/test',prompt_version:'project_hypothesis_v1',
        response_text:'<script>untrusted</script>',reserved_model_cost_usd:0.01,usage_json:{total_tokens:20}})};
    }
    if(url.endsWith('/model-hypothesis')) return {ok:false,status:404,json:async()=>({detail:'Not requested'})};
    return {ok:true,json:async()=>({enabled:true,notice:'Only numeric evidence is sent.',
      configured_per_call_ceiling_usd:0.01,summary:{tested_inputs:1,
        old:{measurement:0.4,declared_outcome:'below_threshold'},
        new:{measurement:0.6,declared_outcome:'above_threshold'}}})};
  });
  ctx.reviewTest.state.jwt='test-token';
  const card=new Element();
  await ctx.reviewTest.configureProjectHypothesis('job-id',card);
  assert.equal(posted,0);
  const preview=all(card).find(n=>n.textContent==='Review model transfer ↗');
  await preview.listeners.click();
  const consent=all(card).find(n=>n.type==='checkbox');
  const send=all(card).find(n=>n.textContent==='Request one suggestion ↗');
  assert.equal(send.disabled,true);
  consent.checked=true; consent.listeners.change();
  await send.listeners.click();
  assert.equal(posted,1);
  assert(all(card).some(n=>n.textContent==='<script>untrusted</script>'));
  assert(all(card).some(n=>n.textContent?.includes('UNTESTED MODEL SUGGESTION')));
});
