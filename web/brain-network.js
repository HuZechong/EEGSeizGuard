/* 头皮脑电癫痫发作预测软件 V1.0. All network weights come from the supplied signal analysis.
 * Three-dimensional anatomy below is a procedural schematic, never patient MRI. */
const mount = document.getElementById('network-workspace');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const finite = v => typeof v === 'number' && Number.isFinite(v);
const fmt = (v, digits = 3) => finite(v) ? v.toFixed(digits) : '—';
const seconds = value => { const s = Math.max(0, Math.round(value)); return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`; };
const state = {data:null,frame:0,band:'theta',threshold:0.45,density:35,hemisphere:'all',shared:false,selected:-1,pair:null,focus:false,labels:true,opacity:0.2,playing:false,speed:1200,chart:'psd',edges:[],eligible:0,lastJob:null,request:0,webgl:false,flat:false};
const processText = [
  '01 · 连续读取 18 路双极头皮脑电，沿文件时间轴进行因果滤波和 128 Hz 重采样。下方波形显示当前 10 秒窗内的预处理信号。',
  '02 · 在同一个 10 秒窗中，以 2 秒 Hann 窗、50% 重叠估计频谱。选择 δ / θ / α / β / γ，所有视图随频段同步。',
  '03 · 以交叉功率谱计算幅值平方相干性 |Pxy|² / (Pxx·Pyy)，再平均所选频段内的频率点。数值 0–1；它不代表方向或因果。',
  '04 · 把双极导联映射到示意头皮位置，连接强度决定线的颜色。阈值、连接比例及共享电极过滤仅改变展示，不改变原始矩阵。',
  '05 · 按 10 秒步长逐窗回放。波形、频谱、矩阵、网络和连接表始终对应同一时间窗；帧间没有插值或伪造连接。'
];
let THREE, OrbitControls, scene, camera, renderer, orbit, nodeGroup, edgeGroup, labelGroup, cortexGroup, haloGroup;
let sceneReady = false, loadController, playTimer, pointerStart, resizeObserver, contextLost = false, cameraFitFactor = 1;
const $ = id => document.getElementById(`bn-${id}`);

if (mount) {
  mount.innerHTML = `
  <section class="bn-panel bn-source" aria-label="脑网络数据来源">
    <div class="bn-source-top"><div class="bn-source-title"><span class="bn-badge demo" id="bn-source-badge">合成信号演示</span><strong id="bn-source-label">正在读取离线演示…</strong></div><div class="bn-toolbar"><button class="bn-button" id="bn-demo" type="button">加载演示</button><button class="bn-button bn-primary" id="bn-import-toggle" type="button" aria-expanded="false" aria-controls="bn-import">分析我的 EDF <span aria-hidden="true">↗</span></button></div></div>
    <p class="bn-source-description" id="bn-source-description">每一条连接均由脑电信号的相干性计算得到。演示信号仅用于体验交互。</p>
    <form class="bn-import" id="bn-import" hidden><div class="bn-import-grid"><div><label for="bn-edf-path">EDF 文件路径</label><input id="bn-edf-path" required autocomplete="off" spellcheck="false" placeholder="data/chb03_35.edf"></div><div><label for="bn-start">开始秒数</label><input id="bn-start" type="number" min="30" step="10" value="30" required></div><div><label for="bn-duration">分析时长 / 秒</label><input id="bn-duration" type="number" min="10" max="600" step="10" value="120" required></div><button class="bn-button bn-primary" id="bn-analyze" type="submit">计算脑网络</button></div><p>开始位置 ≥ 30 秒、时长 10–600 秒，均为 10 的倍数。连续从记录起点滤波以保持因果状态；长记录可能需要更多处理时间。必须具备完整的 18 路导联。</p></form>
  </section>
  <div class="bn-status" id="bn-status" role="status" aria-live="polite"></div>
  <div class="bn-process" role="group" aria-label="网络构建过程">${['预处理信号','选择频段','计算相干矩阵','构建空间网络','逐窗回放'].map((name,i)=>`<button type="button" data-stage="${i}" class="${i===3?'active':''}" aria-pressed="${i===3}"><b>0${i+1}</b><span><strong>${name}</strong><small>${['18 导联 · 128 Hz','Welch 频谱估计','0–1 统计联系','双极导联示意位置','10 秒窗 · 同步联动'][i]}</small></span></button>`).join('')}</div>
  <p class="bn-process-detail" id="bn-process-detail">${processText[3]}</p>
  <div class="bn-workbench">
    <section class="bn-stage" aria-label="三维头皮导联网络">
      <div class="bn-stage-top"><div class="bn-stage-heading">三维头皮导联网络<small>SCALP CONNECTIVITY · SCHEMATIC</small></div><span class="bn-live" id="bn-scene-meta">θ · 4–8 Hz</span></div>
      <div class="bn-viewport" id="bn-viewport"><div id="bn-fallback" class="bn-fallback" hidden></div><div class="bn-fallback-note" id="bn-fallback-note" hidden>当前浏览器未提供 WebGL，已切换可交互的俯视网络。矩阵、波形和导出仍可使用。</div></div>
      <div class="bn-empty-scene" id="bn-empty-scene" hidden>当前筛选下没有连接。可以降低阈值或提高连接保留比例。</div>
      <div class="bn-canvas-tip" id="bn-canvas-tip">拖动旋转 · 滚轮缩放 · 点击节点查看连接</div>
      <div class="bn-scene-toolbar"><div class="bn-view-buttons" role="group" aria-label="三维视角"><button type="button" class="bn-button" data-camera="oblique">斜视</button><button type="button" class="bn-button" data-camera="top">俯视</button><button type="button" class="bn-button" data-camera="front">前视</button><button type="button" class="bn-button" data-camera="side">侧视</button><button type="button" class="bn-button" id="bn-rotate" aria-pressed="false">自动旋转</button><button type="button" class="bn-button" id="bn-flat" aria-pressed="false">二维模式</button></div><button type="button" class="bn-button" id="bn-snapshot">保存视图 PNG</button></div>
      <div class="bn-legend"><span class="bn-legend-item"><i class="bn-dot"></i>左侧导联</span><span class="bn-legend-item"><i class="bn-dot right"></i>右侧导联</span><span class="bn-legend-item"><i class="bn-dot midline"></i>中线</span><span class="bn-legend-scale">弱 <i class="bn-gradient"></i> 强</span></div>
    </section>
    <aside class="bn-panel bn-inspector" aria-label="连接筛选与节点信息">
      <div class="bn-heading"><h2>网络控制</h2><small>同步更新全部视图</small></div>
      <div class="bn-band-section"><div class="bn-control-label">分析频段 <span id="bn-band-range">4–8 Hz</span></div><div class="bn-band-tabs" id="bn-bands" role="group" aria-label="分析频段">${[['delta','δ','0.5–4'],['theta','θ','4–8'],['alpha','α','8–13'],['beta','β','13–30'],['gamma','γ','30–45']].map(([id,label,range])=>`<button type="button" class="bn-band" data-band="${id}" aria-pressed="${id==='theta'}" aria-label="${label}频段 ${range} Hz"><strong>${label}</strong><small>${range}</small></button>`).join('')}</div></div>
      <div class="bn-control"><label for="bn-threshold">最低相干性 <output id="bn-threshold-output" for="bn-threshold">0.45</output></label><input type="range" id="bn-threshold" min="0" max="1" step="0.01" value="0.45"><p class="bn-control-hint">仅展示达到此值的连接；不是显著性检验。</p></div>
      <div class="bn-control"><label for="bn-density">最多保留候选连接 <output id="bn-density-output" for="bn-density">35%</output></label><input type="range" id="bn-density" min="1" max="100" step="1" value="35"><p class="bn-control-hint">按强度排序，先满足阈值，再保留最强连接。</p></div>
      <div class="bn-control"><label for="bn-hemisphere">空间范围</label><select id="bn-hemisphere"><option value="all">全部导联</option><option value="cross">仅左右跨侧连接</option><option value="left">仅左侧内部</option><option value="right">仅右侧内部</option></select><label class="bn-check"><input type="checkbox" id="bn-shared">包含共享电极连接</label><p class="bn-control-hint">共享电极可能人为增高相干性，默认隐藏。</p></div>
      <div class="bn-control"><label for="bn-opacity">示意脑透明度 <output id="bn-opacity-output">20%</output></label><input type="range" id="bn-opacity" min="0" max="0.7" step="0.05" value="0.2"><label class="bn-check"><input type="checkbox" id="bn-labels" checked>显示导联名称</label></div>
      <div class="bn-divider"></div>
      <div><div class="bn-stats"><div class="bn-stat"><strong id="bn-edge-count">—</strong><small>显示连接</small></div><div class="bn-stat"><strong id="bn-active-count">—</strong><small>连接节点</small></div><div class="bn-stat"><strong id="bn-mean">—</strong><small>平均强度</small></div></div><p class="bn-control-hint" id="bn-filter-summary">统计口径：当前时窗的筛选后网络</p></div>
      <div class="bn-selection-section bn-control"><label for="bn-node">选择导联 / 查看邻居</label><select id="bn-node"><option value="-1">全部导联</option></select><label class="bn-check"><input type="checkbox" id="bn-focus">仅显示所选导联的连接</label><div class="bn-selection-note" id="bn-node-note">可在三维场景、矩阵或列表中选中导联。</div><div class="bn-neighbors" id="bn-neighbors"></div></div>
      <div class="bn-quality" id="bn-quality" hidden></div>
    </aside>
  </div>
  <section class="bn-panel bn-timeline" aria-label="网络时间回放"><div class="bn-timeline-head"><div class="bn-timeline-title">时间窗口 <span id="bn-frame-label">— / —</span></div><div class="bn-timeline-controls"><button type="button" class="bn-button bn-icon" id="bn-prev" aria-label="上一时间窗">‹</button><button type="button" class="bn-button" id="bn-play" aria-pressed="false">播放</button><button type="button" class="bn-button bn-icon" id="bn-next" aria-label="下一时间窗">›</button><label class="bn-sr-only" for="bn-speed">回放速度</label><select id="bn-speed"><option value="2000">慢速</option><option value="1200" selected>常速</option><option value="600">快速</option></select></div></div><div class="bn-timeline-track"><time id="bn-time-start">00:00</time><input type="range" id="bn-frame" min="0" max="0" value="0" step="1" aria-label="当前分析时间窗"><time id="bn-time-end">00:00</time></div><div class="bn-frame-bars" id="bn-frame-bars" aria-label="各时间窗的全体有效连接平均相干性"></div><p class="bn-timeline-caption">下方柱高表示各窗全部可估计非对角连接的平均相干性（未应用展示筛选）。回放速度仅影响浏览，时间仍按原始记录计算。</p></section>
  <div class="bn-analysis-grid">
    <section class="bn-panel bn-analysis-panel" id="bn-matrix-panel" aria-labelledby="bn-matrix-title"><div class="bn-heading"><h2 id="bn-matrix-title">相干性矩阵</h2><span class="bn-badge" id="bn-matrix-band">THETA</span></div><p class="bn-panel-description">完整 18 × 18 原始矩阵，不受展示阈值影响。点击一个单元格，联动查看对应导联。</p><div class="bn-matrix-wrap" id="bn-matrix-wrap"><canvas id="bn-matrix" role="img" aria-label="18路导联相干性矩阵，导联详情可通过选择框和连接列表访问"></canvas><div class="bn-matrix-tooltip" id="bn-matrix-tooltip" hidden></div></div><div class="bn-matrix-bottom"><span>0</span><i class="bn-gradient"></i><span>1</span><span>灰色：自身 / 不可估计</span></div><p class="bn-panel-description" id="bn-pair-info" aria-live="polite">选择导联后，对应行与列将高亮。</p></section>
    <section class="bn-panel bn-analysis-panel" id="bn-signal-panel" aria-labelledby="bn-signal-title"><div class="bn-heading"><h2 id="bn-signal-title">信号与频谱</h2><small id="bn-signal-time">同一时间窗</small></div><p class="bn-panel-description" id="bn-signal-description">显示前 4 路预处理信号；选择导联后显示该导联和最强邻居。</p><div class="bn-wave-wrap" id="bn-wave"></div><div class="bn-panel-subtitle"><span id="bn-spectrum-title">Welch 功率谱 · dB(µV²/Hz)</span><div class="bn-data-tabs" role="group" aria-label="频谱显示方式"><button type="button" class="bn-button" data-chart="psd" aria-pressed="true">功率谱</button><button type="button" class="bn-button" data-chart="bands" aria-pressed="false">频段功率</button></div></div><div class="bn-spectrum-wrap" id="bn-spectrum"></div><p class="bn-panel-description" id="bn-spectrum-description">频谱显示所选导联；未选时显示第一路导联。着色区域为当前分析频段。</p></section>
  </div>
  <section class="bn-panel bn-edge-panel"><div class="bn-heading"><h2>当前连接清单 <small id="bn-edge-table-count"></small></h2><div class="bn-toolbar"><button type="button" class="bn-button" id="bn-export-csv">导出当前连接 CSV</button><button type="button" class="bn-button" id="bn-export-json">导出完整网络 JSON</button><button type="button" class="bn-button" id="bn-reset">重置筛选</button></div></div><div class="bn-edge-table-wrap"><table class="bn-edge-table"><thead><tr><th scope="col">序号</th><th scope="col">导联 A</th><th scope="col">导联 B</th><th scope="col">相干性</th><th scope="col">空间关系</th><th scope="col">共享电极</th></tr></thead><tbody id="bn-edge-body"></tbody></table><div class="bn-edge-empty" id="bn-edge-empty">读取信号后显示连接。</div></div></section>
  <details class="bn-method"><summary>计算方法、数据口径与解读边界</summary><p>此视图分析的是双极头皮导联之间的统计联系，独立于风险预测模型。它没有增加新的癫痫预测算法，也不把相干性解释为发作概率。</p><ul><li>每个 10 秒信号窗使用 Welch 频谱估计。幅值平方相干性在所选频带内平均；不进行显著性检验。矩阵对称、无方向，范围为 0–1。</li><li>节点位置来自标准电极的示意坐标，将双极电极中点方向投影到头皮球面。三维脑表面由程序生成，仅辅助辨别方位，不代表患者 MRI、精确电极位置或脑源重建。</li><li>共享电极、体积传导、伪迹和短时间窗都会影响相干性。默认隐藏共享电极连接仍不能排除这些影响；连接强并不等于真实神经连接或因果关系。</li><li>展示阈值、连接比例及空间筛选仅作用于网络和连接清单；完整矩阵始终保留原始数值。平坦或不可估计的导联显示为未知，不填成零连接。</li><li>合成演示由已知频率成分和噪声构成。它不包含患者数据，不可用于评价预测性能或临床决策。</li></ul><div class="bn-method-footer"><span>头皮脑电癫痫发作预测软件 V1.0 · 本地计算 · 无外部数据上传</span><span id="bn-source-id" class="bn-source-id"></span></div></details>`;
  wireEvents();
  initScene().finally(() => {
    if (state.data) { createNodes(); updateAll(); }
    const selected=window.EEGApp?.getSelectedJob?.();
    if (selected?.action==='connectivity'&&selected.status==='completed'&&selected.id!==state.lastJob) {
      state.lastJob=selected.id;
      loadData(`/api/artifact?job=${encodeURIComponent(selected.id)}&name=network.json`,selected.id);
    } else if (!state.request) loadData('/api/network-demo');
  });
}

function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
function validateData(data) {
  if (!data || data.schema_version !== 1 || !Array.isArray(data.nodes) || data.nodes.length !== 18 || !Array.isArray(data.frames) || !data.frames.length || data.frames.length > 60) throw new Error('网络文件结构不兼容，应包含 18 路导联和 1–60 个时间窗。');
  if (!data.source || !['edf','synthetic_demo'].includes(data.source.kind)) throw new Error('网络数据缺少可识别的数据来源。');
  const ids = new Set();
  data.nodes.forEach(n => {
    if (typeof n.id !== 'string' || typeof n.label !== 'string' || ids.has(n.id) || !Array.isArray(n.position) || n.position.length !== 3 || !n.position.every(v=>finite(v)&&Math.abs(v)<=2) || !Array.isArray(n.electrodes)) throw new Error('导联标识或空间坐标无效。');
    ids.add(n.id);
  });
  ['delta','theta','alpha','beta','gamma'].forEach(id=>{ if (!data.bands?.some(b=>b.id===id && finite(b.low)&&finite(b.high))) throw new Error('频段定义不完整。'); });
  data.frames.forEach((f,index)=>{
    if (!finite(f.start_seconds)||!finite(f.end_seconds)||f.end_seconds<=f.start_seconds || (index && f.start_seconds<data.frames[index-1].end_seconds)) throw new Error('时间窗边界无效或重叠。');
    data.bands.forEach(b=>{
      const matrix=f.matrices?.[b.id];
      if (!Array.isArray(matrix)||matrix.length!==18||matrix.some(row=>!Array.isArray(row)||row.length!==18||row.some(v=>v!==null&&(!finite(v)||v<0||v>1.00001)))) throw new Error('相干性矩阵包含无效数值。');
    });
    const waveform = f.waveform;
    if (!waveform || !Array.isArray(waveform.t) || waveform.t.length>2000 || !waveform.t.length || !waveform.t.every(finite) || !Array.isArray(waveform.samples) || waveform.samples.length!==18 || waveform.samples.some(row=>!Array.isArray(row)||row.length!==waveform.t.length||!row.every(finite))) throw new Error('波形数据长度或数值无效。');
  });
  return data;
}
async function loadData(url, jobId = null) {
  const request = ++state.request; loadController?.abort(); loadController = new AbortController(); setPlaying(false);
  status('正在读取网络数据…');
  try {
    const response = await fetch(url, {signal:loadController.signal,cache:'no-store'});
    if (!response.ok) throw new Error(`读取网络失败（HTTP ${response.status}）。`);
    if (Number(response.headers.get('Content-Length'))>64*1024*1024) throw new Error('网络文件超过64MB展示预算，请缩短分析区间。');
    const data = validateData(await response.json());
    if (request !== state.request) return;
    state.data=data; state.frame=0; state.selected=-1; state.pair=null; if(jobId)state.lastJob=jobId;
    $('node').innerHTML='<option value="-1">全部导联</option>'+data.nodes.map((n,i)=>`<option value="${i}">${esc(n.label)}</option>`).join('');
    const demo=data.source.kind==='synthetic_demo';
    $('source-badge').className=`bn-badge ${demo?'demo':'edf'}`;
    $('source-badge').textContent=demo?'合成信号演示':'真实 EDF 信号';
    $('source-label').textContent=data.source.label || (demo?'离线多导联合成信号':'EDF 导联网络');
    $('source-description').textContent=demo?'当前所有波形与连接均来自合成信号，仅用于展示分析流程。切换至「分析我的 EDF」可计算真实记录。':`来源：${data.source.path || data.source.label || '本机 EDF'}。连接由此记录计算，三维表面与电极位置为示意布局。`;
    const hash=data.source.sha256 || data.source.file_sha256;
    $('source-id').textContent=hash?`SHA-256 ${hash}`:`${data.frames.length} 个时间窗 · 18 路双极导联`;
    $('frame').max=String(data.frames.length-1); $('time-start').textContent=seconds(data.frames[0].start_seconds); $('time-end').textContent=seconds(data.frames.at(-1).end_seconds);
    createNodes(); updateAll();
    status('');
  } catch (error) {
    if (error.name !== 'AbortError' && request===state.request) status(`${error.message} ${state.data?'原有视图保持可用。':'可点击「加载演示」重试。'}`,true);
  }
}
function frame() { return state.data.frames[state.frame]; }
function band() { return state.data.bands.find(b=>b.id===state.band); }
function shares(i,j) { return state.data.nodes[i].electrodes.some(e=>state.data.nodes[j].electrodes.includes(e)); }
function spatial(i,j) {
  const a=state.data.nodes[i].hemisphere,b=state.data.nodes[j].hemisphere;
  return state.hemisphere==='all' || (state.hemisphere==='cross'&&((a==='left'&&b==='right')||(a==='right'&&b==='left'))) || (state.hemisphere==='left'&&a==='left'&&b==='left') || (state.hemisphere==='right'&&a==='right'&&b==='right');
}
function computeEdges() {
  const matrix=frame().matrices[state.band], eligible=[];
  for(let i=0;i<18;i++) for(let j=i+1;j<18;j++) {
    const value=matrix[i][j];
    if(!finite(value)||(!state.shared&&shares(i,j))||!spatial(i,j)) continue;
    eligible.push({i,j,value,shared:shares(i,j)});
  }
  eligible.sort((a,b)=>b.value-a.value||a.i-b.i||a.j-b.j); state.eligible=eligible.length;
  state.edges=eligible.filter(e=>e.value>=state.threshold).slice(0,Math.ceil(eligible.length*state.density/100));
  if (state.focus&&state.selected>=0) state.edges=state.edges.filter(e=>e.i===state.selected||e.j===state.selected);
}
function updateAll() {
  if(!state.data) return;
  computeEdges();
  const f=frame(),b=band(),count=state.data.frames.length;
  $('scene-meta').textContent=`${state.data.source.kind==='synthetic_demo'?'合成演示':'真实 EDF'} · ${b.label} · ${seconds(f.start_seconds)}`;
  $('band-range').textContent=`${b.low}–${b.high} Hz`; $('matrix-band').textContent=state.band.toUpperCase();
  $('frame-label').textContent=`${state.frame+1} / ${count} · ${seconds(f.start_seconds)}–${seconds(f.end_seconds)}`;
  $('signal-time').textContent=`${seconds(f.start_seconds)}–${seconds(f.end_seconds)}`;
  $('frame').value=String(state.frame); $('prev').disabled=state.frame===0; $('next').disabled=state.frame===count-1; $('play').disabled=count<2;
  $('edge-count').textContent=String(state.edges.length);
  $('active-count').textContent=String(new Set(state.edges.flatMap(e=>[e.i,e.j])).size);
  $('mean').textContent=state.edges.length?fmt(state.edges.reduce((a,e)=>a+e.value,0)/state.edges.length,2):'—';
  $('filter-summary').textContent=`有效候选 ${state.eligible} 对 · 显示密度 ${state.eligible?fmt(state.edges.length/state.eligible*100,1):'0.0'}% · 筛选后统计`;
  $('empty-scene').hidden=state.edges.length>0;
  const warnings = [...(f.quality?.warnings || [])];
  if(f.quality?.flat_channels?.length) warnings.push(`不可估计导联：${f.quality.flat_channels.join('、')}`);
  $('quality').hidden=!warnings.length; $('quality').textContent=warnings.join('；');
  updateScene(); drawMatrix(); drawSignals(); renderEdges(); renderSelection(); renderTimeline(); renderPair();
}
function selectNode(index) {
  state.selected=Number.isInteger(index)&&index>=0&&index<18?index:-1;
  state.pair=null;
  $('node').value=String(state.selected); updateAll();
}
function renderPair() {
  const pair=state.pair;
  if(!pair){$('pair-info').textContent=state.selected>=0?`${state.data.nodes[state.selected].label} 对应行与列已高亮。`:'选择导联后，对应行与列将高亮。';return;}
  const value=frame().matrices[state.band][pair.i][pair.j];
  $('pair-info').textContent=`${state.data.nodes[pair.i].label} ↔ ${state.data.nodes[pair.j].label} · ${pair.i===pair.j?'自身':`相干性 ${fmt(value)}`}${pair.i!==pair.j&&shares(pair.i,pair.j)?' · 两导联共享电极，解释时需谨慎':''}`;
}
function renderSelection() {
  const idx=state.selected;
  if(idx<0) { $('node-note').textContent='可在三维场景、矩阵或列表中选中导联。'; $('neighbors').replaceChildren(); return; }
  const node=state.data.nodes[idx],links=state.edges.filter(e=>e.i===idx||e.j===idx);
  $('node-note').innerHTML=`<b>${esc(node.label)}</b> · ${esc(node.region || '')}<br>${links.length} 条显示连接 · ${esc({left:'左侧',right:'右侧',midline:'中线'}[node.hemisphere]||'示意位置')}`;
  $('neighbors').innerHTML=links.length?links.slice(0,8).map(e=>{const target=e.i===idx?e.j:e.i;return `<button type="button" class="bn-neighbor" data-node="${target}" aria-label="选择邻居 ${esc(state.data.nodes[target].label)}"><span>${esc(state.data.nodes[target].label)}</span><strong>${fmt(e.value)}</strong></button>`;}).join(''):'<span class="bn-control-hint">该导联在当前筛选下没有连接。</span>';
}
function renderEdges() {
  $('edge-table-count').textContent=`${state.edges.length} 对 · 按强度降序`;
  $('edge-empty').hidden=!!state.edges.length;
  $('edge-empty').textContent='当前筛选下没有可显示的连接。降低阈值或增加连接比例后重试。';
  $('edge-body').innerHTML=state.edges.map((e,k)=>{
    const a=state.data.nodes[e.i],b=state.data.nodes[e.j];
    const relationship=a.hemisphere===b.hemisphere?({left:'左侧内部',right:'右侧内部',midline:'中线'}[a.hemisphere]||'同侧'):(a.hemisphere==='midline'||b.hemisphere==='midline'?'涉及中线':'左右跨侧');
    return `<tr data-node="${e.i}"><td>${String(k+1).padStart(2,'0')}</td><td><button type="button" class="bn-neighbor" data-node="${e.i}" aria-label="选择 ${esc(a.label)}">${esc(a.label)}</button></td><td><button type="button" class="bn-neighbor" data-node="${e.j}" aria-label="选择 ${esc(b.label)}">${esc(b.label)}</button></td><td>${fmt(e.value)}<i class="bn-edge-bar"><span data-bar-value="${(e.value*100).toFixed(2)}"></span></i></td><td>${relationship}</td><td>${e.shared?'是 · 需谨慎解读':'无'}</td></tr>`;
  }).join('');
  $('edge-body').querySelectorAll('[data-bar-value]').forEach(bar=>{bar.style.width=`${bar.dataset.barValue}%`;});
}
function renderTimeline() {
  $('frame-bars').innerHTML=state.data.frames.map((f,i)=>{
    const m=f.matrices[state.band],values=[];for(let a=0;a<18;a++)for(let b=a+1;b<18;b++)if(finite(m[a][b]))values.push(m[a][b]);
    const mean=values.length?values.reduce((a,b)=>a+b,0)/values.length:0;
    return `<button type="button" class="bn-frame-bar ${i===state.frame?'active':''}" data-frame="${i}" data-bar-height="${Math.max(8,mean*100)}" aria-label="第 ${i+1} 窗，${seconds(f.start_seconds)}，全部连接平均相干性 ${fmt(mean)}" aria-pressed="${i===state.frame}"></button>`;
  }).join('');
  $('frame-bars').querySelectorAll('[data-bar-height]').forEach(bar=>{bar.style.height=`${bar.dataset.barHeight}%`;});
}
function setPlaying(playing) {
  clearTimeout(playTimer); state.playing=playing; if(!$('play'))return;
  $('play').textContent=playing?'暂停':'播放'; $('play').setAttribute('aria-pressed',String(playing));
  if(playing&&state.data) playTimer=setTimeout(()=>{state.frame=(state.frame+1)%state.data.frames.length;updateAll();setPlaying(true);},state.speed);
}
function colorRGB(value) {
  const t=clamp(value,0,1),stops=[[31,59,78],[48,128,143],[80,199,169],[194,244,168]],q=t*3,i=Math.min(2,Math.floor(q)),f=q-i;
  return stops[i].map((v,k)=>Math.round(v+(stops[i+1][k]-v)*f));
}
function drawMatrix() {
  const canvas=$('matrix'),wrap=$('matrix-wrap');if(!state.data||!wrap.clientWidth)return;
  const width=wrap.clientWidth,height=wrap.clientHeight,ratio=Math.min(window.devicePixelRatio||1,2);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
  const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.clearRect(0,0,width,height);
  const left=53,top=48,size=Math.min(width-left-8,height-top-4),cell=size/18,matrix=frame().matrices[state.band];canvas.bnGrid={left,top,cell,size,width,height};
  ctx.font=`${clamp(cell*.43,6,9)}px ui-monospace,monospace`;
  state.data.nodes.forEach((node,i)=>{
    ctx.fillStyle=i===state.selected?'#147e69':'#728e99';ctx.textAlign='right';ctx.textBaseline='middle';ctx.fillText(node.label,left-5,top+(i+.5)*cell);
    ctx.save();ctx.translate(left+(i+.5)*cell,top-5);ctx.rotate(-Math.PI/3);ctx.textAlign='left';ctx.fillText(node.label,0,0);ctx.restore();
  });
  for(let i=0;i<18;i++)for(let j=0;j<18;j++){
    const v=matrix[i][j];ctx.fillStyle=i===j?'#e1e8ea':finite(v)?`rgb(${colorRGB(v).join(',')})`:'#c2cbd0';ctx.fillRect(left+j*cell,top+i*cell,Math.ceil(cell)-.8,Math.ceil(cell)-.8);
    if(!finite(v)&&i!==j){ctx.strokeStyle='#8799a2';ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(left+j*cell+2,top+i*cell+2);ctx.lineTo(left+(j+1)*cell-2,top+(i+1)*cell-2);ctx.stroke();}
  }
  if(state.selected>=0){ctx.strokeStyle='#e9c65f';ctx.lineWidth=2;ctx.strokeRect(left,top+state.selected*cell,size,cell);ctx.strokeRect(left+state.selected*cell,top,cell,size);}
}
function matrixPair(event) {
  const canvas=$('matrix'),g=canvas.bnGrid;if(!g)return null;const rect=canvas.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
  const col=Math.floor((x-g.left)/g.cell),row=Math.floor((y-g.top)/g.cell);return col>=0&&col<18&&row>=0&&row<18?{i:row,j:col,x,y}:null;
}
function drawSignals() {
  const f=frame(),idx=state.selected>=0?state.selected:0,adjacent=state.edges.filter(e=>e.i===idx||e.j===idx).map(e=>e.i===idx?e.j:e.i);
  const rows=state.selected>=0?[idx,...adjacent.slice(0,3)]:[0,1,2,3];
  const width=510,height=181,left=68,right=16,top=9,rowHeight=33,t=f.waveform.t,span=f.end_seconds-f.start_seconds;
  let svg=`<svg class="bn-svg-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="当前时间窗内的预处理脑电波形"><rect width="${width}" height="${height}" fill="#fafcfc" rx="7"/>`;
  for(let k=0;k<=5;k++){const x=left+(width-left-right)*k/5;svg+=`<path d="M${x} 8V151" stroke="#e4edef" stroke-dasharray="3 4"/><text x="${x}" y="170" font-size="9" fill="#8da1a8" text-anchor="middle">${fmt(span*k/5,0)} s</text>`;}
  rows.forEach((channel,r)=>{
    const samples=f.waveform.samples[channel],base=top+rowHeight*(r+.5),mean=samples.reduce((a,b)=>a+b,0)/samples.length,amplitude=Math.max(1,...samples.map(v=>Math.abs(v-mean)));
    const path=samples.map((v,i)=>`${i?'L':'M'}${(left+t[i]/span*(width-left-right)).toFixed(2)},${(base-(v-mean)/amplitude*12).toFixed(2)}`).join('');
    const color=channel===idx?'#1c907c':'#5d9ab0';
    svg+=`<text x="${left-6}" y="${base-1}" text-anchor="end" font-size="8" fill="#60818d">${esc(state.data.nodes[channel].label)}</text><text x="${left-6}" y="${base+10}" text-anchor="end" font-size="7" fill="#9aaeb5">±${fmt(amplitude,0)} µV</text><path d="M${left} ${base}H${width-right}" stroke="#e3ecee"/><path d="${path}" stroke="${color}" fill="none" stroke-width="1"/>`;
  });
  svg+='</svg>';$('wave').innerHTML=svg;
  $('signal-description').textContent=state.selected>=0?'所选导联及筛选后最强的最多 3 个邻居。每路独立缩放；左侧标示振幅，波形只作预览。':'当前显示前 4 路预处理信号。每路独立缩放；选择导联可查看它与最强邻居。';
  state.chart==='bands'?drawBandpower(idx):drawPSD(idx);
}
function drawPSD(idx) {
  const f=frame(),freq=f.psd?.frequency_hz,values=f.psd?.values?.[idx],b=band();
  $('spectrum-title').textContent=`${state.data.nodes[idx].label} · 功率谱`;
  if(!Array.isArray(freq)||!Array.isArray(values)||freq.length!==values.length){$('spectrum').innerHTML='<p class="bn-muted">此网络文件未提供功率谱；可切换频段功率。</p>';return;}
  const points=freq.map((hz,i)=>({hz,v:values[i]})).filter(p=>finite(p.hz)&&p.hz>=.5&&p.hz<=45&&finite(p.v)&&p.v>=0).map(p=>({hz:p.hz,db:10*Math.log10(Math.max(p.v,1e-15))}));
  if(!points.length){$('spectrum').innerHTML='<p class="bn-muted">当前导联没有可显示的频谱。</p>';return;}
  const W=510,H=145,L=42,R=15,T=15,B=27,min=Math.floor(Math.min(...points.map(p=>p.db))/10)*10,max=Math.max(min+10,Math.ceil(Math.max(...points.map(p=>p.db))/10)*10),x=hz=>L+(hz/45)*(W-L-R),y=db=>T+(max-db)/(max-min)*(H-T-B);
  let svg=`<svg viewBox="0 0 ${W} ${H}" class="bn-svg-chart" role="img" aria-label="所选导联Welch功率谱，着色为当前频段"><rect x="${x(b.low)}" y="${T}" width="${x(b.high)-x(b.low)}" height="${H-T-B}" fill="#d9f0e7"/>`;
  for(let i=0;i<3;i++){const v=min+(max-min)*i/2;svg+=`<path d="M${L} ${y(v)}H${W-R}" stroke="#e0e9ec"/><text x="${L-5}" y="${y(v)+3}" text-anchor="end" font-size="9" fill="#839ca5">${fmt(v,0)}</text>`;}
  for(const hz of [0,10,20,30,40])svg+=`<text x="${x(hz)}" y="${H-9}" text-anchor="middle" font-size="9" fill="#839ca5">${hz}</text>`;
  svg+=`<path d="${points.map((p,i)=>`${i?'L':'M'}${x(p.hz).toFixed(2)},${y(p.db).toFixed(2)}`).join('')}" fill="none" stroke="#268d7a" stroke-width="1.5"/><text x="${W-12}" y="${H-9}" font-size="9" fill="#839ca5" text-anchor="end">Hz</text><text x="${L}" y="10" font-size="8" fill="#839ca5">dB(µV²/Hz)</text></svg>`;
  $('spectrum').innerHTML=svg;$('spectrum-description').textContent='功率谱以对数刻度显示。着色区域为当前频段；相干性来自两导联交叉谱，不能由单路功率直接推断。';
}
function drawBandpower(idx) {
  const values=state.data.bands.map(b=>({b,v:frame().bandpower?.[b.id]?.[idx]})),valid=values.filter(p=>finite(p.v)&&p.v>=0),max=Math.max(1e-12,...valid.map(p=>p.v)),W=510,H=145,L=28,T=15,barWidth=49,slot=(W-L*2)/5;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="五频段线性功率，单位微伏平方">`;
  values.forEach(({b,v},i)=>{const x=L+slot*(i+.5),height=finite(v)&&v>=0?v/max*79:0;svg+=`<rect x="${x-barWidth/2}" y="${T+85-height}" width="${barWidth}" height="${Math.max(height,.5)}" rx="4" fill="${b.id===state.band?'#259681':'#cee3df'}"/><text x="${x}" y="${Math.max(11,T+78-height)}" text-anchor="middle" font-size="9" fill="#6c8b95">${finite(v)?(v<.01?v.toExponential(1):fmt(v,2)):'未知'}</text><text x="${x}" y="120" text-anchor="middle" font-size="12" fill="#4d7582">${esc(b.label)}</text><text x="${x}" y="136" text-anchor="middle" font-size="8" fill="#8ba0a8">${b.low}–${b.high} Hz</text>`;});
  svg+='</svg>';$('spectrum').innerHTML=svg;$('spectrum-title').textContent=`${state.data.nodes[idx].label} · 频段功率 / µV²`;$('spectrum-description').textContent='五个频段的线性功率，单位 µV²。柱高随当前导联自动缩放；不同时间窗应结合数值比较。';
}

function disposeGroup(group) {
  if(!group)return;
  while(group.children.length){const item=group.children[0];group.remove(item);item.traverse(object=>{object.geometry?.dispose();const materials=Array.isArray(object.material)?object.material:[object.material];materials.forEach(m=>{m?.map?.dispose();m?.dispose();});});}
}
async function initScene() {
  try {
    [THREE,{OrbitControls}]=await Promise.all([import('/vendor/three.module.js'),import('/vendor/OrbitControls.js')]);
    renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));renderer.setClearColor(0x0a1c2b,0);renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.domElement.setAttribute('aria-label','可旋转三维头皮导联网络。也可使用视角按钮和导联选择框操作。');renderer.domElement.setAttribute('role','img');renderer.domElement.tabIndex=0;
    $('viewport').prepend(renderer.domElement);
    scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(39,1,.05,100);camera.position.set(2.65,2.15,3.2);
    orbit=new OrbitControls(camera,renderer.domElement);orbit.target.set(0,.22,0);orbit.enableDamping=true;orbit.dampingFactor=.08;orbit.minDistance=2.3;orbit.maxDistance=7;orbit.enablePan=false;orbit.autoRotateSpeed=.7;
    scene.add(new THREE.AmbientLight(0xa1d0ef,1.6));const key=new THREE.DirectionalLight(0xbfe9ff,3);key.position.set(2,4,3);scene.add(key);const rim=new THREE.DirectionalLight(0x51c1c9,1.5);rim.position.set(-3,1,-2);scene.add(rim);
    cortexGroup=new THREE.Group();nodeGroup=new THREE.Group();edgeGroup=new THREE.Group();labelGroup=new THREE.Group();haloGroup=new THREE.Group();scene.add(cortexGroup,edgeGroup,nodeGroup,labelGroup,haloGroup);
    createCortex();
    const grid=new THREE.GridHelper(3.6,18,0x244855,0x19313f);grid.position.y=-.64;grid.material.transparent=true;grid.material.opacity=.28;scene.add(grid);
    const ring=new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(Array.from({length:100},(_,i)=>new THREE.Vector3(Math.cos(i*Math.PI/50)*1.25,-.62,Math.sin(i*Math.PI/50)*1.25))),new THREE.LineBasicMaterial({color:0x3b6373,transparent:true,opacity:.45}));scene.add(ring);
    addDirectionLabel('前 / A',[0,-.6,1.53]);addDirectionLabel('后 / P',[0,-.6,-1.53]);addDirectionLabel('左 / L',[-1.53,-.6,0]);addDirectionLabel('右 / R',[1.53,-.6,0]);
    renderer.domElement.addEventListener('pointerdown',event=>{pointerStart={x:event.clientX,y:event.clientY};});
    renderer.domElement.addEventListener('pointerup',event=>{
      if(!state.data||!pointerStart||Math.hypot(event.clientX-pointerStart.x,event.clientY-pointerStart.y)>6)return;
      const rect=renderer.domElement.getBoundingClientRect(),pointer=new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),ray=new THREE.Raycaster();ray.setFromCamera(pointer,camera);const hit=ray.intersectObjects(nodeGroup.children,false)[0];if(hit)selectNode(hit.object.userData.index);
    });
    renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();contextLost=true;state.webgl=false;showFallback();status('三维绘制上下文暂时丢失，已切换俯视网络。刷新页面可重新初始化三维场景。',true);});
    state.webgl=true;sceneReady=true;resizeObserver=new ResizeObserver(()=>{resizeScene();drawMatrix();});resizeObserver.observe($('viewport'));resizeObserver.observe($('matrix-wrap'));resizeScene();animate();
  } catch(error) { state.webgl=false;renderer?.dispose();showFallback();window.addEventListener('resize',drawMatrix); }
}
function createCortex() {
  [-1,1].forEach(side=>{
    const geometry=new THREE.SphereGeometry(1,72,48),pos=geometry.attributes.position;
    for(let i=0;i<pos.count;i++){
      let x=pos.getX(i),y=pos.getY(i),z=pos.getZ(i);const theta=Math.atan2(z,x),phi=Math.acos(clamp(y,-1,1));
      const fold=1+.031*Math.sin(theta*14+phi*7)*Math.cos(phi*17)+.018*Math.sin(theta*23-phi*11);
      x=x*.37*fold+side*.35;y=y*.68*fold-.03;z=z*.84*fold;
      pos.setXYZ(i,x,y,z);
    }
    geometry.computeVertexNormals();
    const material=new THREE.MeshPhongMaterial({color:side<0?0x72adc0:0x81afcf,transparent:true,opacity:state.opacity,shininess:48,side:THREE.DoubleSide,depthWrite:false});
    cortexGroup.add(new THREE.Mesh(geometry,material));
    const wire=new THREE.Mesh(geometry.clone(),new THREE.MeshBasicMaterial({color:0x537e93,wireframe:true,transparent:true,opacity:.04,depthWrite:false}));cortexGroup.add(wire);
  });
  // A thin scalp shell communicates sensor placement without claiming anatomy.
  const shell=new THREE.Mesh(new THREE.SphereGeometry(1.02,48,24,0,Math.PI*2,0,Math.PI*.6),new THREE.MeshBasicMaterial({color:0x65b0b8,wireframe:true,transparent:true,opacity:.035,depthWrite:false}));cortexGroup.add(shell);
}
function makeLabel(text,color='#bbd9e2',scale=.24) {
  const canvas=document.createElement('canvas');canvas.width=384;canvas.height=96;const ctx=canvas.getContext('2d');ctx.font='500 31px Arial, Microsoft YaHei, sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.shadowColor='#071724';ctx.shadowBlur=9;ctx.fillStyle=color;ctx.fillText(text,192,48);
  const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;const material=new THREE.SpriteMaterial({map:texture,transparent:true,depthTest:false,depthWrite:false});const sprite=new THREE.Sprite(material);sprite.scale.set(scale*4,scale,1);return sprite;
}
function addDirectionLabel(text,position) { const label=makeLabel(text,'#628e9e',.22);label.position.set(...position);scene.add(label); }
function createNodes() {
  if(!state.data||!sceneReady)return;disposeGroup(nodeGroup);disposeGroup(labelGroup);
  state.data.nodes.forEach((n,i)=>{
    const color=n.hemisphere==='left'?0x60dab6:n.hemisphere==='right'?0x8dbfff:0xf2ce84;
    const node=new THREE.Mesh(new THREE.SphereGeometry(.033,18,14),new THREE.MeshPhongMaterial({color,emissive:color,emissiveIntensity:.18,shininess:90}));node.position.set(...n.position).multiplyScalar(1.08);node.userData.index=i;nodeGroup.add(node);
    const label=makeLabel(n.label,n.hemisphere==='left'?'#a5e5d3':n.hemisphere==='right'?'#b2d2f5':'#f0d49c',.2);
    label.position.copy(node.position).multiplyScalar(1.14);label.position.y+=.04;
    // Separate the lateral and medial chains radially; their frontal midpoints
    // project close together in the top view despite distinct sensor positions.
    if(n.hemisphere!=='midline'){
      const direction=n.hemisphere==='left'?-1:1,lateral=i<4||i>=12;
      label.position.x+=direction*(lateral?.13:-.09);
      if(i%4===0)label.position.z+=lateral?.09:-.06;
    }
    labelGroup.add(label);
  });
}
function updateScene() {
  if(!state.webgl||!sceneReady||state.flat){drawFallback();return;}disposeGroup(edgeGroup);disposeGroup(haloGroup);
  const focus=state.selected;
  nodeGroup.children.forEach((node,i)=>{
    node.scale.setScalar(i===focus?1.7:1);node.material.emissiveIntensity=i===focus?.85:.2;
    const active=!state.focus||focus<0||i===focus||state.edges.some(e=>e.i===i||e.j===i);node.material.transparent=true;node.material.opacity=active?1:.25;
  });
  labelGroup.visible=state.labels;
  labelGroup.children.forEach((label,i)=>{const size=i===focus?.25:.2;label.scale.set(size*4,size,1);label.material.opacity=focus<0||i===focus||state.edges.some(e=>(e.i===focus&&e.j===i)||(e.j===focus&&e.i===i))?1:.5;});
  cortexGroup.children.forEach((mesh,i)=>{mesh.material.opacity=i===4?state.opacity*.175:i%2===0?state.opacity:state.opacity*.2;});
  state.edges.forEach(e=>{
    const a=nodeGroup.children[e.i].position,b=nodeGroup.children[e.j].position,middle=a.clone().add(b).multiplyScalar(.5),distance=a.distanceTo(b);
    if(middle.length()<.2)middle.set(0,1,0);middle.normalize().multiplyScalar(1.11+distance*.12);const curve=new THREE.QuadraticBezierCurve3(a,middle,b),color=new THREE.Color(...colorRGB(e.value).map(v=>v/255));
    const highlighted=focus<0||e.i===focus||e.j===focus;
    const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(22)),new THREE.LineBasicMaterial({color,transparent:true,opacity:highlighted?.25+e.value*.68:.08,depthWrite:false}));edgeGroup.add(line);
  });
  if(focus>=0){const halo=new THREE.Mesh(new THREE.SphereGeometry(.065,20,14),new THREE.MeshBasicMaterial({color:0xf5dc94,transparent:true,opacity:.2,depthWrite:false}));halo.position.copy(nodeGroup.children[focus].position);haloGroup.add(halo);}
}
function resizeScene() {
  const viewport=$('viewport');if(!renderer||!viewport.clientWidth||!viewport.clientHeight)return;
  renderer.setSize(viewport.clientWidth,viewport.clientHeight,false);
  camera.aspect=viewport.clientWidth/viewport.clientHeight;
  // Preserve the orbit direction and user zoom while compensating for the
  // narrow horizontal field of view. The ratio restores desktop zoom exactly.
  const nextFit=Math.max(1,1.15/camera.aspect),ratio=nextFit/cameraFitFactor;
  camera.position.sub(orbit.target).multiplyScalar(ratio).add(orbit.target);
  cameraFitFactor=nextFit;orbit.minDistance=2.3*nextFit;orbit.maxDistance=7*nextFit;
  camera.updateProjectionMatrix();orbit.update();
}
function animate() {if(contextLost)return;requestAnimationFrame(animate);if(document.hidden||state.flat||!mount.getClientRects().length)return;orbit.update();renderer.render(scene,camera);}
function setCamera(view) {
  if(!state.webgl)return;const positions={oblique:[2.65,2.15,3.2],top:[0,4.7,.001],front:[0,1.05,4.8],side:[4.8,1.05,0]};camera.up.set(0,1,0);orbit.target.set(0,.22,0);camera.position.set(...positions[view]).sub(orbit.target).multiplyScalar(cameraFitFactor).add(orbit.target);orbit.update();
  mount.querySelectorAll('[data-camera]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.camera===view)));
}
function showFallback() {
  $('fallback').hidden=false;$('fallback-note').hidden=false;$('canvas-tip').textContent='俯视布局 · 点击节点查看连接';$('rotate').disabled=true;
  mount.querySelectorAll('[data-camera]').forEach(button=>button.disabled=true);$('snapshot').textContent='保存俯视 PNG';if(renderer?.domElement)renderer.domElement.hidden=true;
  $('flat').setAttribute('aria-pressed','true');$('flat').disabled=!state.webgl;$('flat').textContent=state.webgl?'返回三维':'二维模式';
  $('fallback-note').textContent=state.webgl?'二维俯视使用同一组真实计算的连接。三维渲染已暂停，可随时切回。':'当前浏览器未提供 WebGL，已切换可交互的俯视网络。矩阵、波形和导出仍可使用。';
  if(state.data)drawFallback();
}
function toggleFlat() {
  if(!state.webgl)return;state.flat=!state.flat;
  if(state.flat){showFallback();return;}
  $('fallback').hidden=true;$('fallback-note').hidden=true;renderer.domElement.hidden=false;$('rotate').disabled=false;
  mount.querySelectorAll('[data-camera]').forEach(button=>button.disabled=false);
  $('flat').textContent='二维模式';$('flat').setAttribute('aria-pressed','false');$('snapshot').textContent='保存视图 PNG';$('canvas-tip').textContent='拖动旋转 · 滚轮缩放 · 点击节点查看连接';resizeScene();if(state.data)updateScene();
}
function fallbackSVG() {
  const nodes=state.data.nodes,x=i=>250+nodes[i].position[0]*150,y=i=>244-nodes[i].position[2]*150;
  let svg='<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500" viewBox="0 0 500 500" role="img" aria-label="头皮导联网络俯视图"><ellipse cx="250" cy="242" rx="158" ry="180" fill="#1b3b4d" fill-opacity=".4" stroke="#416577"/><path d="M234 61L250 40 266 61M250 65V413" fill="none" stroke="#416577" stroke-dasharray="4 5"/>';
  state.edges.forEach(e=>{svg+=`<path d="M${x(e.i)} ${y(e.i)}Q250 190 ${x(e.j)} ${y(e.j)}" stroke="rgb(${colorRGB(e.value)})" stroke-width="${1+e.value}" stroke-opacity="${state.selected<0||e.i===state.selected||e.j===state.selected?.8:.12}" fill="none"/>`;});
  nodes.forEach((n,i)=>{svg+=`<g data-node="${i}" role="button" tabindex="0" aria-label="选择导联 ${esc(n.label)}"><circle cx="${x(i)}" cy="${y(i)}" r="${i===state.selected?9:6}" fill="${n.hemisphere==='left'?'#68dabc':n.hemisphere==='right'?'#8dbfff':'#ecc777'}" stroke="${i===state.selected?'#f9e6a6':'#16323c'}" stroke-width="2"/>${state.labels?`<text x="${x(i)}" y="${y(i)-12}" fill="#c6dce3" font-family="Arial,sans-serif" font-size="9" text-anchor="middle">${esc(n.label)}</text>`:''}</g>`;});
  return svg+'<text x="250" y="27" fill="#7296a5" text-anchor="middle" font-size="10">前 / A</text><text x="67" y="247" fill="#7296a5" font-size="10">L</text><text x="426" y="247" fill="#7296a5" font-size="10">R</text></svg>';
}
function drawFallback(){if(state.data)$('fallback').innerHTML=fallbackSVG();}
function download(name,blob) {const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
function exportCSV() {
  if(!state.data)return;const f=frame(),header=['source_kind','source_label','start_seconds','end_seconds','band','channel_a','channel_b','coherence','shared_electrode','threshold','maximum_candidate_percent','spatial_filter','focus_channel'];
  const quote=v=>{let value=String(v??'');if(typeof v==='string'&&/^\s*[=+\-@]/.test(value))value="'"+value;return '"'+value.replace(/"/g,'""')+'"';};
  const rows=state.edges.map(e=>[state.data.source.kind,state.data.source.label,f.start_seconds,f.end_seconds,state.band,state.data.nodes[e.i].label,state.data.nodes[e.j].label,e.value,e.shared,state.threshold,state.density,state.hemisphere,state.selected>=0?state.data.nodes[state.selected].label:'']);
  download(`头皮脑电癫痫发作预测软件_V1.0_${state.band}_${f.start_seconds}s_connections.csv`,new Blob(['\uFEFF'+[header,...rows].map(row=>row.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
}
async function snapshot() {
  if(!state.data)return;
  try {
    setPlaying(false);
    // Freeze the complete export before Image.onload or Canvas.toBlob yields.
    // Users can keep changing the workspace while the captured image is encoded.
    const f=frame(),b=band(),captured=Object.freeze({
      sourceKind:state.data.source.kind,band:state.band,bandLabel:b.label,low:b.low,high:b.high,
      sourceLabel:String(state.data.source.label||state.data.source.path||'未提供'),
      sourceHash:String(state.data.source.sha256||state.data.source.file_sha256||''),
      hemisphere:state.hemisphere,focusLabel:state.focus&&state.selected>=0?String(state.data.nodes[state.selected].label):'全部导联',
      start:f.start_seconds,end:f.end_seconds,threshold:state.threshold,density:state.density,
      count:state.edges.length,shared:state.shared,exportedAt:new Date().toISOString()
    });
    let original,sw,sh;
    if(state.webgl&&!state.flat){
      renderer.render(scene,camera);
      sw=renderer.domElement.width;sh=renderer.domElement.height;
      original=document.createElement('canvas');original.width=sw;original.height=sh;
      original.getContext('2d').drawImage(renderer.domElement,0,0);
    } else {
      const svg=fallbackSVG();sw=500;sh=500;
      original=await new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('二维网络图像未完成加载'));image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);});
    }
    const out=document.createElement('canvas');out.width=1400;out.height=1130;const ctx=out.getContext('2d');ctx.fillStyle='#0a1c2b';ctx.fillRect(0,0,out.width,out.height);ctx.fillStyle='#e2f1f2';ctx.font='600 27px Arial, Microsoft YaHei, sans-serif';ctx.fillText('头皮脑电癫痫发作预测软件 V1.0 · 头皮导联相干网络',45,48);
    const writeLine=(text,y)=>{
      let fitted=String(text);const limit=out.width-90;
      if(ctx.measureText(fitted).width>limit){
        const chars=Array.from(fitted);let lo=0,hi=chars.length;
        while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(ctx.measureText(chars.slice(0,mid).join('')+'…').width<=limit)lo=mid;else hi=mid-1;}
        fitted=chars.slice(0,lo).join('')+'…';
      }
      ctx.fillText(fitted,45,y);
    };
    ctx.fillStyle='#9fbcc6';ctx.font='18px Arial, Microsoft YaHei, sans-serif';ctx.fillText(`${captured.sourceKind==='synthetic_demo'?'合成演示':'真实 EDF 信号'} · ${captured.bandLabel} ${captured.low}–${captured.high} Hz · ${seconds(captured.start)}–${seconds(captured.end)}`,45,83);
    const scale=Math.min(1320/sw,855/sh),w=sw*scale,h=sh*scale;ctx.drawImage(original,(1400-w)/2,112+(855-h)/2,w,h);
    writeLine(`阈值 ≥ ${fmt(captured.threshold,2)} · 候选连接最多 ${captured.density}% · 显示 ${captured.count} 对 · ${captured.shared?'包含':'隐藏'}共享电极`,1010);
    ctx.font='16px Arial, Microsoft YaHei, sans-serif';
    const spatialLabel={all:'全部导联',cross:'仅左右跨侧',left:'仅左侧内部',right:'仅右侧内部'}[captured.hemisphere]||'全部导联';
    writeLine(`空间：${spatialLabel} · 聚焦：${captured.focusLabel} · 来源：${captured.sourceLabel}`,1040);
    ctx.fillStyle='#829eab';writeLine('节点为双极导联，三维脑表面和位置仅作示意；统计联系不代表脑区连接、方向或因果。',1070);
    const shortHash=captured.sourceHash?captured.sourceHash.slice(0,12)+(captured.sourceHash.length>12?'…':''):'未提供';
    writeLine(`视图导出时间 ${captured.exportedAt} · SHA-256 ${shortHash}`,1100);
    const blob=await new Promise(resolve=>out.toBlob(resolve,'image/png'));if(!blob)throw new Error('图像生成失败');download(`头皮脑电癫痫发作预测软件_V1.0_${captured.band}_${captured.start}s_network.png`,blob);status('当前视图已导出 PNG，包含数据来源、时间窗与筛选口径。');
  } catch(error) { status(`无法导出图像：${error.message||'浏览器未完成绘制'}。仍可导出连接 CSV 和完整 JSON。`,true); }
}
function wireEvents() {
  const applyDefaults=info=>{if(!$('edf-path').value&&info?.default_paths?.edf_path)$('edf-path').value=info.default_paths.edf_path;};
  applyDefaults(window.EEGApp?.getInfo?.());
  window.addEventListener('eeg:ready',event=>applyDefaults(event.detail));
  $('demo').addEventListener('click',()=>loadData('/api/network-demo'));
  $('import-toggle').addEventListener('click',()=>{$('import').hidden=!$('import').hidden;$('import-toggle').setAttribute('aria-expanded',String(!$('import').hidden));if(!$('import').hidden)$('edf-path').focus();});
  $('import').addEventListener('submit',async event=>{
    event.preventDefault();const start=Number($('start').value),duration=Number($('duration').value),path=$('edf-path').value.trim();
    if(!path||!Number.isInteger(start)||start<30||start%10||!Number.isInteger(duration)||duration<10||duration>600||duration%10){status('请填写有效 EDF 路径；起点需 ≥ 30 秒，时长 10–600 秒，且均为 10 的倍数。',true);return;}
    if(!window.EEGApp?.submit){status('本地工作台尚未连接完成，请稍后重试。',true);return;}
    try{status('正在提交网络分析任务。任务完成后将自动显示结果。');const job=await window.EEGApp.submit('connectivity',{source:'edf',edf_path:path,start_seconds:start,duration_seconds:duration},$('analyze'));if(!job)status('网络任务未能提交，请查看工作台提示或任务记录后重试。',true);}catch(error){status(error.message||'网络分析任务提交失败。',true);}
  });
  $('threshold').addEventListener('input',event=>{state.threshold=Number(event.target.value);$('threshold-output').value=fmt(state.threshold,2);updateAll();});
  $('density').addEventListener('input',event=>{state.density=Number(event.target.value);$('density-output').value=`${state.density}%`;updateAll();});
  $('hemisphere').addEventListener('change',event=>{state.hemisphere=event.target.value;updateAll();});
  $('shared').addEventListener('change',event=>{state.shared=event.target.checked;updateAll();});
  $('focus').addEventListener('change',event=>{state.focus=event.target.checked;updateAll();});
  $('node').addEventListener('change',event=>selectNode(Number(event.target.value)));
  $('opacity').addEventListener('input',event=>{state.opacity=Number(event.target.value);$('opacity-output').value=`${Math.round(state.opacity*100)}%`;if(state.data)updateScene();});
  $('labels').addEventListener('change',event=>{state.labels=event.target.checked;if(state.data)updateScene();});
  $('frame').addEventListener('input',event=>{setPlaying(false);state.frame=Number(event.target.value);updateAll();});
  $('prev').addEventListener('click',()=>{if(state.data){setPlaying(false);state.frame=Math.max(0,state.frame-1);updateAll();}});
  $('next').addEventListener('click',()=>{if(state.data){setPlaying(false);state.frame=Math.min(state.data.frames.length-1,state.frame+1);updateAll();}});
  $('play').addEventListener('click',()=>{if(state.data)setPlaying(!state.playing);});
  $('speed').addEventListener('change',event=>{state.speed=Number(event.target.value);if(state.playing)setPlaying(true);});
  $('rotate').addEventListener('click',()=>{if(orbit){orbit.autoRotate=!orbit.autoRotate;$('rotate').setAttribute('aria-pressed',String(orbit.autoRotate));}});
  $('flat').addEventListener('click',toggleFlat);
  $('snapshot').addEventListener('click',snapshot);$('export-csv').addEventListener('click',exportCSV);
  $('export-json').addEventListener('click',()=>{if(state.data)download('头皮脑电癫痫发作预测软件_V1.0_network.json',new Blob([JSON.stringify(state.data,null,2)],{type:'application/json'}));});
  $('reset').addEventListener('click',()=>{state.threshold=.45;state.density=35;state.hemisphere='all';state.shared=false;state.focus=false;state.selected=-1;state.pair=null;$('threshold').value='.45';$('density').value='35';$('threshold-output').value='0.45';$('density-output').value='35%';$('hemisphere').value='all';$('shared').checked=false;$('focus').checked=false;$('node').value='-1';$('pair-info').textContent='选择导联后，对应行与列将高亮。';updateAll();});
  mount.addEventListener('click',event=>{
    const stage=event.target.closest('[data-stage]');if(stage){
      mount.querySelectorAll('[data-stage]').forEach(el=>{el.classList.toggle('active',el===stage);el.setAttribute('aria-pressed',String(el===stage));});
      const index=Number(stage.dataset.stage);$('process-detail').textContent=processText[index];
      const destinations=[$('signal-panel'),$('spectrum-title'),$('matrix-panel'),mount.querySelector('.bn-workbench'),mount.querySelector('.bn-timeline')];
      destinations[index]?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
      return;
    }
    const bandButton=event.target.closest('[data-band]');if(bandButton){state.band=bandButton.dataset.band;mount.querySelectorAll('[data-band]').forEach(el=>el.setAttribute('aria-pressed',String(el===bandButton)));$('pair-info').textContent='选择导联后，对应行与列将高亮。';updateAll();return;}
    const chartButton=event.target.closest('[data-chart]');if(chartButton){state.chart=chartButton.dataset.chart;mount.querySelectorAll('[data-chart]').forEach(el=>el.setAttribute('aria-pressed',String(el===chartButton)));if(state.data)drawSignals();return;}
    const cameraButton=event.target.closest('[data-camera]');if(cameraButton){setCamera(cameraButton.dataset.camera);return;}
    const node=event.target.closest('[data-node]');if(node&&state.data){selectNode(Number(node.dataset.node));return;}
    const frameButton=event.target.closest('[data-frame]');if(frameButton&&state.data){setPlaying(false);state.frame=Number(frameButton.dataset.frame);updateAll();}
  });
  $('fallback').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){const node=event.target.closest('[data-node]');if(node){event.preventDefault();selectNode(Number(node.dataset.node));}}});
  $('matrix').addEventListener('mousemove',event=>{
    if(!state.data)return;const pair=matrixPair(event);if(!pair){$('matrix-tooltip').hidden=true;return;}
    const value=frame().matrices[state.band][pair.i][pair.j];$('matrix-tooltip').hidden=false;$('matrix-tooltip').textContent=`${state.data.nodes[pair.i].label} ↔ ${state.data.nodes[pair.j].label}：${pair.i===pair.j?'自身':finite(value)?fmt(value):'不可估计'}${pair.i!==pair.j&&shares(pair.i,pair.j)?' · 共享电极':''}`;
  });
  $('matrix').addEventListener('mouseleave',()=>{$('matrix-tooltip').hidden=true;});
  $('matrix').addEventListener('click',event=>{
    if(!state.data)return;const pair=matrixPair(event);if(!pair)return;selectNode(pair.i);state.pair={i:pair.i,j:pair.j};renderPair();
  });
  window.addEventListener('eeg:job-selected',event=>{
    const job=event.detail;if(job?.action!=='connectivity')return;
    // Explicit selection must restore this task after a demo or a failed read;
    // passive polling must preserve the source the user chose to explore.
    if(job.status==='completed'&&(job.id!==state.lastJob||job.explicitSelection===true)){state.lastJob=job.id;loadData(`/api/artifact?job=${encodeURIComponent(job.id)}&name=network.json`,job.id);}
    else if(job.status==='failed')status(`网络分析失败：${job.error||'请查看任务日志。'}`,true);
    else if(job.status==='cancelled')status('网络分析已取消，当前已有视图保持可用。');
    else if(['queued','running'].includes(job.status))status(`网络分析进行中 · ${Math.round(job.progress||0)}%。当前展示仍为上一次加载的数据。`);
  });
  window.addEventListener('eeg:view',event=>{const view=typeof event.detail==='string'?event.detail:event.detail?.view;if(view!=='network')setPlaying(false);else{resizeScene();drawMatrix();}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)setPlaying(false);});
  window.addEventListener('pagehide',()=>{setPlaying(false);loadController?.abort();resizeObserver?.disconnect();});
}
