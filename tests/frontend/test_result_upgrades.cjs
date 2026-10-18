const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../../app/static/result.js'), 'utf8');

class Element {
  constructor() { this.children = []; this.hidden = true; this.dataset = {}; this.textContent = ''; this.classList = { add() {} }; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
  set innerHTML(value) { throw new Error('Untrusted HTML insertion'); }
}
async function render(data) {
  const elements = new Map();
  const document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  }, createElement() { return new Element(); }, querySelectorAll() { return []; } };
  document.getElementById('result-page').dataset = { investigationId: 'owned-id', view: 'evidence' };
  vm.runInNewContext(script, { document, window: { addEventListener() {} }, fetch: async () => ({ ok: true, json: async () => data }) });
  await new Promise(resolve => setImmediate(resolve));
  const text = element => element.textContent + element.children.map(text).join(' ');
  return { elements, text: text(document.getElementById('result-disclosures')) };
}
const base = { investigation_id: 'owned-id', run_mode: 'pinned_workers', verdict: 'no_violation_found', controls: [], limitations: [] };

test('historical report renders without optional upgrade fields or export link', async () => {
  const result = await render(base);
  assert.equal(result.elements.get('result-content').hidden, false);
  assert.equal(result.elements.get('result-regression').hidden, true);
});

test('checkpoint and repair scopes remain explicit and source text stays inert', async () => {
  const result = await render({ ...base,
    checkpoint_diagnostics: { status: 'observed', first_observed_difference: 'normalized', checkpoints: [], limitations: ['<img src=x onerror=alert(1)>'] },
    search_provenance: { strategy: 'adaptive', seed: 9, attempted: 4, valid: 4, invalid: 0, stop_reason: 'exhausted', candidates: [] },
    model_patch_result: { verdict: 'repair_fails_stated_checks', scientific_oracle: { status: 'not_supplied' },
      measurement_checks: [{ case: 'holdout_0', preservation: 'fail', reference: 'not_supplied', baseline_ratios: [.2, .2], patched_ratios: [.3, .3] }] }
  });
  assert.equal(result.elements.get('result-content').hidden, false);
  assert.match(result.text, /does not establish a cause/);
  assert.match(result.text, /not scientific correctness/);
  assert.match(result.text, /seed 9/);
  assert.match(result.text, /<img src=x/);
  assert.match(result.elements.get('result-repairs').children[0].className, /is-fail/);
});

test('regression link is restricted to eligible pinned reports', async () => {
  const witness = { input_json: { signal: [1] }, old_run: {}, new_run: {} };
  const eligible = await render({ ...base, witness, replay_material: { sources: {} } });
  assert.equal(eligible.elements.get('result-regression').hidden, false);
  assert.equal(eligible.elements.get('result-regression').href, '/api/investigations/owned-id/regression.zip');
  const simulated = await render({ ...base, run_mode: 'local_simulation', witness, replay_material: {} });
  assert.equal(simulated.elements.get('result-regression').hidden, true);
});
