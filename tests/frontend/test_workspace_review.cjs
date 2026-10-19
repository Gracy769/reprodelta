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
  const context = {document, fetch, URL, AbortController, location: {}, window: {setTimeout,clearTimeout, matchMedia: () => ({matches:true})}};
  vm.runInNewContext(shipped.replace('  start();', '  globalThis.reviewTest = {renderReview, configureReplay, state};'), context);
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

test('fresh review opens contract approval without a second configure click', async () => {
  const ctx=boot(async () => ({ok:true,json:async () => ({contract_hash:'c'.repeat(64)})}));
  ctx.reviewTest.renderReview({...result,freshReview:true},change);
  await new Promise(setImmediate);
  const nodes=all(ctx.elements.get('review-result-body'));
  assert(nodes.some(n => n.textContent === 'Review the paired-run contract'));
  assert(nodes.some(n => n.textContent?.includes('Approve contract and run investigation')));
});

test('paired replay carries approved contract, immutable revision and selected manifest', async () => {
  const requests=[];
  const ctx=boot(async (url,options) => {
    requests.push({url,options});
    return {ok:true,json:async () => url.endsWith('/contract') ? {contract_hash:'c'.repeat(64)} : {investigation_id:'job-id'}};
  });
  const parent=new Element(), trigger=new Element('button');
  await ctx.reviewTest.configureReplay(result,change,parent,trigger);
  const form=parent.children.find(n => n.tagName === 'form');
  const manifest=all(form).find(n => n.tagName === 'select'); manifest.value='requirements.txt';
  const checkbox=all(form).find(n => n.type === 'checkbox'); checkbox.checked=true;
  await form.listeners.submit({preventDefault(){}});
  const approval=requests.find(r => r.url.endsWith('/contract/approve'));
  const request=requests.find(r => r.url.endsWith('/paired-replay'));
  assert.equal(approval.options.method,'POST');
  assert(requests.indexOf(approval)<requests.indexOf(request));
  assert.equal(request.options.method,'POST');
  assert.deepEqual(JSON.parse(request.options.body),{repository:'lab/project',number:7,expected_head_sha:'b'.repeat(40),dependency_path:'requirements.txt',contract_hash:'c'.repeat(64)});
  assert.equal(ctx.location.href,'/investigations/job-id');
});

test('paired replay cannot start until the contract is approved', async () => {
  const requests=[];
  const ctx=boot(async (url,options) => {
    requests.push({url,options});
    return {ok:true,json:async () => ({contract_hash:'c'.repeat(64)})};
  });
  const parent=new Element();
  await ctx.reviewTest.configureReplay(result,change,parent);
  const form=parent.children.find(n => n.tagName === 'form');
  await form.listeners.submit({preventDefault(){}});
  assert(all(form).some(n => n.textContent?.includes('Review and approve')));
  assert(!requests.some(r => r.url.endsWith('/paired-replay')));
});

test('missing manifest explains setup gap and cannot start paired replay', async () => {
  const ctx=boot(async () => ({ok:true,json:async () => ({contract_hash:'c'.repeat(64)})}));
  const parent=new Element();
  await ctx.reviewTest.configureReplay({...result,dependency_files:[]},change,parent,new Element());
  assert(all(parent).some(n => n.textContent?.includes('No supported dependency manifest')));
  assert(!all(parent).some(n => n.type === 'submit'));
});
