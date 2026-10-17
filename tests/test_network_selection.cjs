/* Run with: node --test tests/test_network_selection.cjs
 * Load the production network state transitions with a minimal DOM and local
 * response fixture. No browser, EDF, or external network is required. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/brain-network.js'), 'utf8');
const selectionHandler = source.match(/window\.addEventListener\('eeg:job-selected',event=>\{([\s\S]*?)\n  \}\);/);
assert.ok(selectionHandler, 'Production network selection handler must exist');

function fixture(kind) {
  const bands = ['delta', 'theta', 'alpha', 'beta', 'gamma'].map(id => ({ id, low: 1, high: 2 }));
  return {
    schema_version: 1, source: { kind, label: kind }, bands,
    nodes: Array.from({ length: 18 }, (_, index) => ({
      id: String(index), label: String(index), position: [0, 1, 0], electrodes: [],
    })),
    frames: [{
      start_seconds: 30, end_seconds: 40,
      matrices: Object.fromEntries(bands.map(b => [b.id, Array.from({ length: 18 }, () => Array(18).fill(1))])),
      waveform: { t: [0], samples: Array.from({ length: 18 }, () => [0]) },
    }],
  };
}

function harness() {
  const elements = new Map(), requests = [];
  let failNextArtifact = false;
  const context = {
    clearTimeout, setTimeout, AbortController, window: {},
    document: {
      getElementById(id) {
        if (id === 'network-workspace') return null;
        if (!elements.has(id)) elements.set(id, { classList: { toggle() {} }, setAttribute() {} });
        return elements.get(id);
      },
    },
    fetch: async url => {
      requests.push(url);
      const artifact = url.startsWith('/api/artifact');
      if (artifact && failNextArtifact) {
        failNextArtifact = false;
        return { ok: false, status: 503 };
      }
      return {
        ok: true, headers: { get() { return null; } },
        json: async () => fixture(artifact ? 'edf' : 'synthetic_demo'),
      };
    },
  };
  // Rendering itself is covered by browser acceptance; retain the production
  // validation, request handling, state changes, and selected-job callback here.
  vm.runInNewContext(source + '\ncreateNodes=()=>{}; updateAll=()=>{}; globalThis.audit={state,loadData,selectJob(event){' + selectionHandler[1] + '}};', context);
  return {
    ...context.audit, requests, elements,
    failArtifact() { failNextArtifact = true; },
    settle: () => new Promise(resolve => setImmediate(resolve)),
  };
}

test('explicitly reselecting an EDF task restores it after demo without poll events overriding demo', async () => {
  const h = harness(), job = { id: 'A', action: 'connectivity', status: 'completed' };
  h.selectJob({ detail: job });
  await h.settle();
  assert.equal(h.state.data.source.kind, 'edf');
  await h.loadData('/api/network-demo');
  assert.equal(h.state.data.source.kind, 'synthetic_demo');
  h.selectJob({ detail: job });
  await h.settle();
  assert.equal(h.state.data.source.kind, 'synthetic_demo', 'A polling refresh must preserve the chosen demo');
  assert.equal(h.requests.length, 2);
  h.selectJob({ detail: { ...job, explicitSelection: true } });
  await h.settle();
  assert.equal(h.state.data.source.kind, 'edf');
  assert.deepEqual(h.requests, ['/api/artifact?job=A&name=network.json', '/api/network-demo', '/api/artifact?job=A&name=network.json']);
});

test('explicitly reselecting the same completed task retries a failed network download', async () => {
  const h = harness(), job = { id: 'A', action: 'connectivity', status: 'completed' };
  h.failArtifact();
  h.selectJob({ detail: job });
  await h.settle();
  assert.equal(h.state.data, null);
  assert.match(h.elements.get('bn-status').textContent, /HTTP 503/);
  h.selectJob({ detail: { ...job, explicitSelection: true } });
  await h.settle();
  assert.equal(h.requests.length, 2);
  assert.equal(h.state.data.source.kind, 'edf');
  assert.equal(h.elements.get('bn-status').textContent, '');
});

test('a newly selected completed task loads even when it was reported by polling', async () => {
  const h = harness();
  for (const id of ['A', 'B']) {
    h.selectJob({ detail: { id, action: 'connectivity', status: 'completed' } });
    await h.settle();
  }
  assert.equal(h.state.lastJob, 'B');
  assert.equal(h.requests.at(-1), '/api/artifact?job=B&name=network.json');
});
