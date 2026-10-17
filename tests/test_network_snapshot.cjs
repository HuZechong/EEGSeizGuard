/* Run with: node --test tests/test_network_snapshot.cjs
 * Exercise the production export function across controlled browser async
 * boundaries; no WebGL, patient files, or network service is required. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/brain-network.js'), 'utf8');
const start = source.indexOf('async function snapshot()');
const end = source.indexOf('function wireEvents()', start);
assert.ok(start >= 0 && end > start, 'Production snapshot function must exist');

function harness(webgl = false) {
  const state = {
    data: {
      source: { kind: 'edf', label: 'chb03_35.edf', sha256: 'abcdef012345' + 'a'.repeat(52) },
      nodes: [{ label: 'FP1-F7' }, { label: 'FP2-F4' }],
    }, frame: 0, band: 'theta', webgl,
    flat: !webgl, playing: true, threshold: .45, density: 35,
    shared: false, edges: [{}, {}], hemisphere: 'left', focus: true, selected: 0,
  };
  const frames = [{ start_seconds: 30, end_seconds: 40 }, { start_seconds: 40, end_seconds: 50 }];
  const bands = { theta: { label: 'θ', low: 4, high: 8 }, alpha: { label: 'α', low: 8, high: 13 } };
  const images = [], canvases = [], downloads = [], statuses = [];
  const renderer = {
    domElement: { width: 960, height: 540, pixels: '' },
    render() { this.domElement.pixels = `frame=${state.frame};band=${state.band}`; },
  };
  function canvas() {
    const element = { width: 0, height: 0, pixels: '', labels: [], labelDetails: [], draws: [], blobCallback: null };
    const context = {
      fillRect() {},
      fillText(text, x, y) { element.labels.push(text); element.labelDetails.push({ text, x, y, width: this.measureText(text).width }); },
      measureText(text) { return { width: Array.from(text).reduce((sum, c) => sum + (c.codePointAt(0) > 255 ? 16 : 8), 0) }; },
      drawImage(image, ...dimensions) {
        element.pixels = image.pixels;
        element.draws.push({ pixels: image.pixels, dimensions });
      },
    };
    element.getContext = () => context;
    element.toBlob = callback => { element.blobCallback = callback; };
    canvases.push(element);
    return element;
  }
  const context = {
    state, renderer, scene: {}, camera: {}, Date, Promise, encodeURIComponent,
    frame: () => frames[state.frame], band: () => bands[state.band],
    fmt: (value, digits) => value.toFixed(digits), seconds: value => String(value),
    setPlaying(value) { state.playing = value; },
    fallbackSVG: () => `<svg>frame=${state.frame};band=${state.band}</svg>`,
    Image: class {
      constructor() { this.width = 500; this.height = 500; images.push(this); }
      set src(value) { this.pixels = decodeURIComponent(value); }
    },
    document: { createElement: canvas },
    download(name, blob) { downloads.push({ name, blob }); },
    status(message, error) { statuses.push({ message, error }); },
  };
  vm.runInNewContext(source.slice(start, end) + '\nglobalThis.exportSnapshot = snapshot;', context);
  return { state, renderer, images, canvases, downloads, statuses, snapshot: context.exportSnapshot };
}

function changeWorkspace(h) {
  h.state.frame = 1;
  h.state.band = 'alpha';
  h.state.data.source.kind = 'synthetic_demo';
  h.state.data.source.label = 'other.edf';
  h.state.data.source.sha256 = '0123456789abcdef'.repeat(4);
  h.state.data.nodes[0].label = 'CHANGED';
  h.state.hemisphere = 'right';
  h.state.focus = false;
  h.state.selected = 1;
  h.state.threshold = .99;
  h.state.density = 1;
  h.state.edges = [];
  h.state.shared = true;
}

test('2D export keeps image, source, frame, band and filters fixed across image load and PNG encoding', async () => {
  const h = harness();
  const pending = h.snapshot();
  assert.equal(h.state.playing, false, 'Export must synchronously stop playback');
  assert.match(h.images[0].pixels, /frame=0;band=theta/);
  changeWorkspace(h);
  h.images[0].onload();
  await new Promise(resolve => setImmediate(resolve));
  const out = h.canvases[0];
  assert.match(out.pixels, /frame=0;band=theta/);
  assert.equal(out.labels[1], '真实 EDF 信号 · θ 4–8 Hz · 30–40');
  assert.equal(out.labels[2], '阈值 ≥ 0.45 · 候选连接最多 35% · 显示 2 对 · 隐藏共享电极');
  assert.equal(out.labels[3], '空间：仅左侧内部 · 聚焦：FP1-F7 · 来源：chb03_35.edf');
  assert.match(out.labels[5], /SHA-256 abcdef012345…$/);
  assert.deepEqual(out.labelDetails.slice(2).map(line => line.y), [1010, 1040, 1070, 1100]);
  // Loading a different record while the browser encodes the PNG must not
  // change the already-captured filename or require any live data access.
  h.state.data = null;
  h.state.frame = 99;
  h.state.band = 'gamma';
  out.blobCallback({ png: true });
  await pending;
  assert.equal(h.downloads[0].name, '头皮脑电癫痫发作预测软件_V1.0_theta_30s_network.png');
  assert.equal(h.statuses.at(-1).error, undefined);
});

test('3D export copies rendered pixels and dimensions before the live renderer changes', async () => {
  const h = harness(true);
  const pending = h.snapshot();
  assert.equal(h.state.playing, false);
  assert.equal(h.canvases.length, 2, 'A detached canvas must freeze the live WebGL frame');
  const [frozen, out] = h.canvases;
  assert.deepEqual([frozen.width, frozen.height], [960, 540]);
  assert.equal(frozen.pixels, 'frame=0;band=theta');
  assert.equal(out.pixels, frozen.pixels);
  assert.deepEqual(out.draws[0].dimensions.slice(-2), [1320, 742.5]);
  changeWorkspace(h);
  h.renderer.render();
  h.renderer.domElement.width = 320;
  h.renderer.domElement.height = 900;
  out.blobCallback({ png: true });
  await pending;
  assert.equal(out.pixels, 'frame=0;band=theta');
  assert.equal(out.labels[1], '真实 EDF 信号 · θ 4–8 Hz · 30–40');
  assert.equal(out.labels[3], '空间：仅左侧内部 · 聚焦：FP1-F7 · 来源：chb03_35.edf');
  assert.match(out.labels[5], /SHA-256 abcdef012345…$/);
  assert.equal(h.downloads[0].name, '头皮脑电癫痫发作预测软件_V1.0_theta_30s_network.png');
});

test('failed SVG decoding reports an error without exporting an incomplete image', async () => {
  const h = harness();
  const pending = h.snapshot();
  h.images[0].onerror();
  await pending;
  assert.equal(h.downloads.length, 0);
  assert.equal(h.statuses.at(-1).error, true);
  assert.match(h.statuses.at(-1).message, /二维网络图像未完成加载/);
});

test('long source labels are measured and clipped while filters and hash remain readable', async () => {
  const h = harness(true);
  h.state.data.source.label = '很长的脑电记录文件😀'.repeat(100) + '.edf';
  h.state.focus = false;
  h.state.hemisphere = 'cross';
  const pending = h.snapshot();
  const out = h.canvases[1], sourceLine = out.labelDetails.find(line => line.y === 1040);
  assert.match(sourceLine.text, /^空间：仅左右跨侧 · 聚焦：全部导联 · 来源：/);
  assert.ok(sourceLine.text.endsWith('…'));
  assert.ok(sourceLine.width <= out.width - 90);
  assert.ok(out.labelDetails.filter(line => line.y >= 1010).every(line => line.width <= out.width - 90));
  assert.match(out.labels[5], /SHA-256 abcdef012345…$/);
  out.blobCallback({ png: true });
  await pending;
  assert.equal(h.downloads.length, 1);
});
