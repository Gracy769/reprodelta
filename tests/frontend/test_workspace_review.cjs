const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const shipped = fs.readFileSync(path.join(__dirname, '../../app/static/workspace.js'), 'utf8');

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.classList = {toggle(){}, add(){}, remove(){}}; }
  append(...nodes) { nodes.forEach(node => {node.parent = this; this.children.push(node);}); }
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
  vm.runInNewContext(shipped.replace('  start();', '  globalThis.reviewTest = {renderReview, configureReplay, configureApprovedRun, state};'), context);
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
    : {status:'completed',result_json:{changed_conclusion:true,old:{ratio:0.4,conclusion:'below_threshold'},
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
  assert(all(parent).some(n => n.textContent?.includes('One paired execution cannot establish')));
  assert(all(parent).some(n => n.textContent?.includes('No candidate repair was executed')));
});
