// Execute the shipped rendering code against actual paired-response shapes.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../../app/static/demo.js'), 'utf8');

async function render(payload) {
  const elements = new Map();
  const document = { querySelectorAll() { return []; }, getElementById(id) {
    if (!elements.has(id)) elements.set(id, { value: id === 'signal-values' ? '1, 1, 1, 1, 0.5, 0.5, 0.5, 0.5' : '1', style: {}, textContent: '', dataset: {projectId: 'project'},
      addEventListener(event, handler) { this.handler = handler; } });
    return elements.get(id);
  }};
  vm.runInNewContext(script, { document, window: {location: {}}, AbortController, setTimeout, clearTimeout, fetch: async (url) => ({ ok: true, json: async () => url === "/api/investigations" ? {id: "probe"} : payload }) });
  await document.getElementById('run-btn').handler();
  await new Promise(resolve => setImmediate(resolve));
  return elements;
}

test('actual API old_run/new_run renders measurements and contract conclusions', async () => {
  const fields = await render({ old_run: { outputs: { result: { conclusion: 'above_threshold', ratio: .50000001, intermediate_dtype: 'float64' } } },
    new_run: { outputs: { result: { conclusion: 'below_threshold', ratio: .5, intermediate_dtype: 'float32' } } },
    comparison: { old_conclusion: 'above_threshold', new_conclusion: 'inconclusive', conclusion_changed: true, verdict_hint: 'supported_conclusion_change' } });
  assert.match(fields.get('old-result').textContent, /float64/);
  assert.match(fields.get('new-result').textContent, /inconclusive.*float32/);
  assert.equal(fields.get('verdict-row').textContent, 'supported conclusion change');
  assert.equal(fields.get('result-section').style.display, 'block');
});

test('failed evidence never falls back to no change', async () => {
  const fields = await render({ old_run: { outputs: {} }, new_run: { outputs: {} }, comparison: { conclusion_changed: null, verdict_hint: 'setup_failure' } });
  assert.equal(fields.get('old-result').textContent, 'Result unavailable');
  assert.equal(fields.get('verdict-row').textContent, 'setup failure');
});

test('missing comparison is explicitly inconclusive', async () => {
  const fields = await render({});
  assert.equal(fields.get('verdict-row').textContent, 'inconclusive');
});
