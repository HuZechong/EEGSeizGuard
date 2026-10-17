/* 头皮脑电癫痫发作预测软件 local workspace. All dynamic content is inserted with safe DOM APIs. */
"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const viewNames = {
    overview: "工作概览", prepare: "数据准备", train: "模型训练",
    predict: "风险预测", evaluate: "效果分析", jobs: "任务记录", network: "三维脑网络",
  };
  const actionNames = {
    demo: "完整演示（合成数据）", prepare: "数据准备", train: "模型训练",
    predict: "风险预测", evaluate: "效果分析", history: "读取历史研究结果", connectivity: "三维脑网络分析",
  };
  const statusNames = {
    queued: "排队中", running: "运行中", completed: "已完成",
    failed: "失败", cancelled: "已取消",
  };
  const activeStatuses = new Set(["queued", "running"]);
  const fieldIds = {
    dataset: ["train-dataset-path", "predict-dataset-path"],
    model: ["model-path"], predictions: ["predictions-path"], manifest: ["manifest-path"],
  };
  const state = {
    info: null, jobs: [], selectedId: null, view: "overview", submitting: false,
    connected: false, failures: 0, pollTimer: null, polling: false,
    selectedSignature: "", jobsSignature: "", resultSignature: "", displayedId: null,
    createdIds: new Set(), appliedIds: new Set(), preview: [], toastTimer: null,
    cancelPending: false, disposed: false, previewThreshold: .5, metricPatients: [], chartPoints: [], chartGeometry: null,
  };

  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined && content !== null) element.textContent = String(content);
    return element;
  }

  function replace(element, children) {
    element.replaceChildren(...children);
  }

  function normalizeStatus(status) {
    const value = String(status || "queued").toLowerCase();
    if (["succeeded", "success", "done"].includes(value)) return "completed";
    if (value === "canceled") return "cancelled";
    return value;
  }

  function statusOf(job) { return normalizeStatus(job.status); }
  function isActive(job) { return activeStatuses.has(statusOf(job)); }
  function hasActiveJob() { return state.jobs.some(isActive); }
  function percent(job) {
    const value = Number(job.progress);
    return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
  }

  function formatNumber(value, digits = 4) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : "—";
  }

  function formatCount(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString("zh-CN") : "—";
  }

  function formatPercent(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : "—";
  }

  function messageText(value) {
    if (typeof value === "string") return value;
    if (value && typeof value === "object") {
      if (typeof value.message === "string") return value.message;
      return JSON.stringify(value, null, 2);
    }
    return String(value ?? "");
  }

  function resultOrigin(job) {
    const result = job.result || {};
    if (job.action === "history") return "history";
    if (job.action === "connectivity" && result.network_summary?.source_kind === "synthetic_demo") return "demo";
    if (job.action === "demo" || result.synthetic || result.is_demo ||
      String(result.metrics?.source_kind || "").startsWith("synthetic")) return "demo";
    return job.action === "evaluate" ? "csv" : "current";
  }

  function toast(message, error = false) {
    const element = byId("toast");
    element.textContent = message;
    element.className = error ? "toast error" : "toast";
    element.hidden = false;
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => { element.hidden = true; }, error ? 8500 : 5500);
  }

  function showView(view, updateHash = true) {
    if (!Object.hasOwn(viewNames, view)) view = "overview";
    state.view = view;
    document.querySelectorAll(".view").forEach((section) => {
      section.hidden = section.id !== `view-${view}`;
    });
    document.querySelectorAll("[data-view]").forEach((button) => {
      const selected = button.dataset.view === view;
      button.classList.toggle("active", selected);
      if (selected) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    byId("current-view-name").textContent = viewNames[view];
    document.body.dataset.view = view;
    document.title = `${viewNames[view]} · 头皮脑电癫痫发作预测软件`;
    if (updateHash && window.location.hash !== `#${view}`) {
      window.history.replaceState(null, "", `#${view}`);
    }
    window.dispatchEvent(new CustomEvent("eeg:view", { detail: { view } }));
  }

  function updateControls() {
    const blocked = state.submitting || hasActiveJob() || !state.connected;
    document.querySelectorAll(".run-button").forEach((button) => {
      button.disabled = blocked;
      button.title = !state.connected ? "请先连接本地服务" :
        (state.submitting || hasActiveJob() ? "请等待当前任务完成，或先取消任务" : "");
    });
    byId("refresh-button").disabled = state.submitting;
    byId("cancel-button").disabled = state.cancelPending || !state.connected;
  }

  function setConnection(connected, message = "") {
    state.connected = connected;
    byId("sidebar-status").textContent = connected ? "本地服务已连接" : "连接暂不可用";
    byId("sidebar-status-dot").className = `status-dot ${connected ? "connected" : "disconnected"}`;
    byId("topbar-status-dot").className = `status-dot ${connected ? "connected" : "disconnected"}`;
    byId("topbar-status").textContent = connected ? "本机服务已连接" : "连接暂不可用";
    const alert = byId("connection-alert");
    alert.hidden = !message;
    alert.textContent = message;
    updateControls();
  }

  async function fetchJSON(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    const headers = { Accept: "application/json", ...options.headers };
    if (options.method === "POST") {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = state.info?.csrf_token || "";
    }
    try {
      const response = await fetch(path, {
        credentials: "same-origin", cache: "no-store", ...options, headers,
        signal: controller.signal,
      });
      let payload;
      try { payload = await response.json(); }
      catch { throw new Error(`服务返回了无法读取的内容（HTTP ${response.status}）。`); }
      if (!response.ok) {
        const detail = payload?.error ?? payload?.message ?? `请求失败（HTTP ${response.status}）`;
        throw new Error(messageText(detail));
      }
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("本地服务响应超时，稍后会继续尝试刷新。请检查启动窗口。");
      if (error instanceof TypeError) throw new Error("无法连接本地服务。请确认软件启动窗口仍在运行，并从启动时给出的地址打开工作台。");
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function setDefaultPaths(paths, overwrite = false) {
    if (!paths || typeof paths !== "object") return;
    Object.entries(fieldIds).forEach(([key, ids]) => {
      if (typeof paths[key] !== "string" || !paths[key].trim()) return;
      ids.forEach((id) => {
        const input = byId(id);
        if (overwrite || !input.value.trim()) input.value = paths[key];
      });
    });
  }

  function applyCompletedOutputs(job) {
    if (statusOf(job) !== "completed" || !state.createdIds.has(job.id) || state.appliedIds.has(job.id)) return;
    state.appliedIds.add(job.id);
    const result = job.result || {};
    if (job.action !== "history") setDefaultPaths(result.output_paths || result, true);
    toast(job.action === "demo" ? "演示已完成。合成数据的输出已填入后续步骤，可继续训练或分析。" : `${actionNames[job.action] || "任务"}已完成，结果和输出文件已保存。`);
  }

  function displayDate(value) {
    const date = new Date(Number(value) * 1000);
    return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "时间未提供";
  }

  function renderOverview() {
    const completed = state.jobs.filter((job) => statusOf(job) === "completed");
    const active = state.jobs.find(isActive);
    const latest = completed.find((job) => job.result?.metrics?.macro);
    const failed = state.jobs.filter((job) => statusOf(job) === "failed").length;
    byId("overview-total").textContent = formatCount(state.jobs.length);
    byId("overview-completed").textContent = formatCount(completed.length);
    byId("overview-completed-note").textContent = failed ? `${failed} 项失败，可在任务记录查看原因` : "独立目录保存，可追溯输入与输出";
    byId("overview-active").textContent = active ? `${Math.round(percent(active))}%` : "空闲";
    byId("overview-active-note").textContent = active ? actionNames[active.action] || active.action : "可以开始一项分析";
    byId("overview-auroc").textContent = formatNumber(latest?.result?.metrics?.macro?.auroc);
    byId("overview-auroc-note").textContent = latest ? (resultOrigin(latest) === "demo" ? "合成数据演示 · 无真实效能含义" : resultOrigin(latest) === "history" ? "已有历史研究 · 非本次训练" : "最近完成任务 · 患者级宏平均") : "完成效果分析后显示";
    const context = byId("overview-context");
    if (!latest) return;
    const text = node("span", "", `最近评估：${actionNames[latest.action] || latest.action} · ${displayDate(latest.created)} · 任务 ${latest.id}`);
    const button = node("button", "button button-small button-ghost", "查看该任务 →");
    button.type = "button";
    button.addEventListener("click", () => { showView("jobs"); selectJob(latest.id, true); });
    replace(context, [text, button]);
  }

  function renderJobs() {
    const query = byId("job-search").value.trim().toLowerCase();
    const statusFilter = byId("job-status-filter").value;
    const actionFilter = byId("job-action-filter").value;
    const signature = JSON.stringify([query, statusFilter, actionFilter, state.jobs.map((job) => [job.id, job.action, job.status, percent(job), job.id === state.selectedId])]);
    if (signature === state.jobsSignature) return;
    state.jobsSignature = signature;
    byId("job-count").textContent = String(state.jobs.length);
    const filtered = state.jobs.filter((job) => {
      const matchesStatus = statusFilter === "all" || (statusFilter === "running" ? isActive(job) : statusOf(job) === statusFilter);
      return matchesStatus && (actionFilter === "all" || job.action === actionFilter) && `${actionNames[job.action] || job.action} ${job.id}`.toLowerCase().includes(query);
    });
    byId("jobs-summary").textContent = state.jobs.length ? `显示 ${filtered.length} / ${state.jobs.length} 个任务` : "尚无任务";
    const container = byId("jobs-list");
    if (!filtered.length) {
      const empty = node("div", "empty-state");
      const symbol = node("span", "empty-icon", "⌁");
      symbol.setAttribute("aria-hidden", "true");
      empty.append(symbol, node("h3", "", state.jobs.length ? "没有符合条件的任务" : "还没有运行记录"), node("p", "", state.jobs.length ? "调整检索词或筛选条件，任务记录不会被删除。" : "先探索三维脑网络，或导入自己的数据运行分析。"));
      const button = node("button", "button button-secondary", state.jobs.length ? "清除筛选" : "返回工作概览");
      button.type = "button";
      button.addEventListener("click", () => {
        if (!state.jobs.length) return showView("overview");
        byId("job-search").value = "";
        byId("job-status-filter").value = "all";
        byId("job-action-filter").value = "all";
        renderJobs();
      });
      empty.append(button);
      replace(container, [empty]);
      return;
    }
    const rows = filtered.map((job) => {
      const status = statusOf(job);
      const row = node("button", `job-row${job.id === state.selectedId ? " selected" : ""}`);
      row.type = "button";
      row.setAttribute("aria-label", `${actionNames[job.action] || job.action}，${statusNames[status] || status}，查看详情`);
      row.setAttribute("aria-pressed", String(job.id === state.selectedId));
      const main = node("div", "job-row-main");
      const dot = node("span", `status-dot ${status}`);
      dot.setAttribute("aria-hidden", "true");
      const text = node("div");
      text.append(node("strong", "", actionNames[job.action] || job.action || "任务"), node("small", "", `任务编号 ${job.id.slice(0, 8)}`));
      main.append(dot, text);
      const right = node("div", "job-row-right");
      const tagClass = status === "completed" ? "tag tag-green" : status === "failed" ? "tag tag-amber" : "tag";
      const source = resultOrigin(job);
      if (source === "demo" || source === "history") right.append(node("span", "job-source", source === "demo" ? "合成演示" : "历史数据"));
      right.append(node("span", "", `${Math.round(percent(job))}%`), node("span", tagClass, statusNames[status] || status));
      row.append(main, right);
      row.addEventListener("click", () => selectJob(job.id, true));
      return row;
    });
    replace(container, rows);
  }

  function renderPatientTable() {
    const query = byId("patient-search").value.trim().toLowerCase();
    const sort = byId("patient-sort").value;
    const patients = state.metricPatients.filter((patient) => String(patient.patient).toLowerCase().includes(query));
    patients.sort((a, b) => sort === "patient" ? String(a.patient).localeCompare(String(b.patient), "zh-CN", { numeric: true }) :
      (b[sort] === null || b[sort] === undefined ? -Infinity : Number(b[sort])) - (a[sort] === null || a[sort] === undefined ? -Infinity : Number(a[sort])));
    byId("patient-table-count").textContent = `显示 ${patients.length} / ${state.metricPatients.length} 位患者；排序与检索不改变上方宏平均。`;
    replace(byId("patient-table-body"), patients.map((patient) => {
      const row = node("tr");
      [patient.patient, formatCount(patient.n), formatCount(patient.positives), formatPercent(patient.prevalence), formatNumber(patient.auroc), formatNumber(patient.ap), formatNumber(patient.macro_f1 ?? patient.f1), formatNumber(patient.balanced_accuracy)].forEach((value) => row.append(node("td", "", value ?? "—")));
      return row;
    }));
  }

  function renderMetrics(metrics, job) {
    const panel = byId("metrics-panel");
    const macro = metrics?.macro;
    const patients = Array.isArray(metrics?.patients) ? metrics.patients : [];
    panel.hidden = !macro && !patients.length;
    if (panel.hidden) return;
    const metricItems = [
      ["AUROC", macro?.auroc, "风险分数的排序能力"], ["AP", macro?.ap, "平均查准率"],
      ["F1", macro?.macro_f1 ?? macro?.f1, "指定阈值下的 F1"],
      ["平衡准确率", macro?.balanced_accuracy, "两类召回率的平均"],
    ];
    replace(byId("metrics-grid"), metricItems.map(([label, value, help]) => {
      const card = node("article", "metric-card");
      card.append(node("span", "", label), node("strong", "", formatNumber(value)), node("small", "", help));
      return card;
    }));
    const origin = resultOrigin(job);
    byId("metrics-context").textContent = origin === "history" ? "原项目历史记录" : origin === "demo" ? "合成演示数据" : origin === "csv" ? "CSV 描述性分析" : "当前任务输出";
    const lines = [];
    const pooled = metrics.pooled;
    if (pooled) lines.push(`合并窗口：AUROC ${formatNumber(pooled.auroc)} · AP ${formatNumber(pooled.ap)} · F1 ${formatNumber(pooled.macro_f1 ?? pooled.f1)} · 平衡准确率 ${formatNumber(pooled.balanced_accuracy)}`);
    const excluded = metrics.excluded_unknown ?? job.result?.excluded_unknown;
    if (excluded !== undefined && excluded !== null) lines.push(`排除未知标签窗口 ${formatCount(excluded)} 个`);
    lines.push("— 表示未提供或无法计算；单类患者的 AUROC / AP 均不计算。");
    byId("metrics-subline").textContent = lines.join("　|　");
    const events = metrics.history?.historical_event_facts;
    const eventNote = byId("history-event-note");
    eventNote.hidden = job.action !== "history" || !events || typeof events !== "object";
    if (!eventNote.hidden) {
      const strategy = metrics.history?.historical_alarm_policy?.strategy ?? "未提供";
      eventNote.textContent = `原报警策略：${String(strategy)}；事件命中 ${formatCount(events.predicted_events)}/${formatCount(events.eligible_events)}。事件敏感度 ${formatPercent(events.event_sensitivity)} · 误报警 ${formatCount(events.false_alarms)} 次 · FAR/h ${formatNumber(events.far_per_hour)}。事件指标来自原报告，本次未重算。零误报警需结合命中数解读。`;
    }
    state.metricPatients = patients;
    renderPatientTable();
  }

  function svgNode(tag, attributes, text) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function renderChart() {
    const patient = byId("preview-patient").value;
    const record = byId("preview-record").value;
    const all = state.preview.filter((point) => String(point.patient ?? "未注明") === patient && String(point.record_id ?? point.record ?? "未注明记录") === record)
      .filter((point) => point.time_seconds !== null && point.time_seconds !== "" &&
        point.time_seconds !== undefined && Number.isFinite(Number(point.time_seconds)) && point.probability !== null &&
        point.probability !== "" && Number.isFinite(Number(point.probability)))
      .sort((a, b) => Number(a.time_seconds) - Number(b.time_seconds));
    const container = byId("probability-chart");
    state.chartPoints = all;
    byId("curve-point").max = String(Math.max(0, all.length - 1));
    byId("curve-point").value = "0";
    byId("curve-point").disabled = !all.length;
    if (!all.length) {
      replace(container, [node("p", "", "该患者没有可绘制的有效风险评分时间点。")]);
      byId("chart-caption").textContent = "";
      return;
    }
    const stride = Math.max(1, Math.ceil(all.length / 600));
    const points = all.filter((_, index) => index % stride === 0);
    if (points[points.length - 1] !== all[all.length - 1]) points.push(all[all.length - 1]);
    const width = 880, height = 230, left = 52, right = 21, top = 20, bottom = 41;
    const min = Number(all[0].time_seconds), max = Number(all[all.length - 1].time_seconds);
    const range = Math.max(1, max - min);
    const x = (seconds) => left + (Number(seconds) - min) / range * (width - left - right);
    const y = (probability) => top + (1 - Math.max(0, Math.min(1, Number(probability)))) * (height - top - bottom);
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${patient}风险评分时间曲线；共 ${all.length} 个有效预览时间点` });
    svg.append(svgNode("title", {}, `${patient}窗口风险评分，横轴为记录内时间，纵轴为未校准风险分数`));
    [0, .25, .5, .75, 1].forEach((value) => {
      svg.append(svgNode("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: "#e5eee8", "stroke-width": 1 }));
      svg.append(svgNode("text", { x: left - 13, y: y(value) + 4, fill: "#96aaa0", "text-anchor": "end", "font-size": 10 }, value.toFixed(2)));
    });
    for (let index = 0; index <= 4; index++) {
      const seconds = min + range * index / 4;
      svg.append(svgNode("text", { x: x(seconds), y: height - 19, fill: "#96aaa0", "text-anchor": "middle", "font-size": 10 }, `${(seconds / 60).toFixed(1)} 分`));
    }
    const threshold = state.previewThreshold;
    svg.append(svgNode("line", { x1: left, x2: width - right, y1: y(threshold), y2: y(threshold), stroke: "#d7b57b", "stroke-width": 1.3, "stroke-dasharray": "5 5" }));
    // Reset the line across record boundaries and gaps instead of implying continuous observation.
    const positiveGaps = all.slice(1).map((point, index) => Number(point.time_seconds) - Number(all[index].time_seconds)).filter((gap) => gap > 0);
    positiveGaps.sort((a, b) => a - b);
    const typicalGap = positiveGaps.length ? positiveGaps[Math.floor(positiveGaps.length / 2)] : 10;
    const gapLimit = Math.max(30, typicalGap * stride * 3);
    let path = "", previous = null, gapCount = 0;
    points.forEach((point) => {
      const record = point.record_id ?? point.record;
      const previousRecord = previous?.record_id ?? previous?.record;
      const changedRecord = previous && record !== undefined && previousRecord !== undefined && record !== previousRecord;
      const discontinuity = previous && (changedRecord || Number(point.time_seconds) - Number(previous.time_seconds) > gapLimit);
      if (discontinuity) gapCount++;
      path += `${!previous || discontinuity ? "M" : "L"}${x(point.time_seconds).toFixed(2)} ${y(point.probability).toFixed(2)} `;
      previous = point;
    });
    svg.append(svgNode("path", { d: path, fill: "none", stroke: "#169d7d", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    if (points.length === 1) svg.append(svgNode("circle", { cx: x(points[0].time_seconds), cy: y(points[0].probability), r: 3, fill: "#169d7d" }));
    state.chartGeometry = { x, y, top, bottom: height - bottom };
    svg.append(svgNode("line", { id: "curve-cursor", x1: left, x2: left, y1: top, y2: height - bottom, stroke: "#6886a3", "stroke-width": 1, "stroke-dasharray": "3 4" }));
    svg.append(svgNode("circle", { id: "curve-marker", cx: left, cy: y(points[0].probability), r: 4, fill: "#087c6f", stroke: "white", "stroke-width": 2 }));
    replace(container, [svg]);
    renderCurvePoint();
    byId("chart-caption").textContent = `实线为窗口风险评分，虚线为分类阈值 ${threshold.toFixed(2)}。返回 ${all.length.toLocaleString("zh-CN")} 个有效时间点${stride > 1 ? `，绘图抽样 ${points.length} 点` : ""}。${gapCount ? `检测到 ${gapCount} 处时间或记录空档，曲线分段绘制。` : ""}完整预测请下载 CSV。`;
  }

  function renderPreview(preview) {
    state.preview = Array.isArray(preview) ? preview.filter((point) => point && typeof point === "object") : [];
    const panel = byId("preview-panel");
    panel.hidden = !state.preview.length;
    if (panel.hidden) return;
    const select = byId("preview-patient");
    const previous = select.value;
    const patients = [...new Set(state.preview.map((point) => String(point.patient ?? "未注明")))];
    replace(select, patients.map((patient) => {
      const option = node("option", "", patient);
      option.value = patient;
      return option;
    }));
    if (patients.includes(previous)) select.value = previous;
    renderRecordOptions();
  }

  function renderRecordOptions() {
    const select = byId("preview-record"), previous = select.value;
    const patient = byId("preview-patient").value;
    const records = [...new Set(state.preview.filter((point) => String(point.patient ?? "未注明") === patient)
      .map((point) => String(point.record_id ?? point.record ?? "未注明记录")))];
    replace(select, records.map((record) => {
      const option = node("option", "", record);
      option.value = record;
      return option;
    }));
    if (records.includes(previous)) select.value = previous;
    renderChart();
  }

  function renderCurvePoint() {
    const point = state.chartPoints[Number(byId("curve-point").value)];
    if (!point) { byId("curve-point-value").textContent = "没有可核对的时间点"; return; }
    const index = Number(byId("curve-point").value);
    const labels = { "-1": "未知标签", "0": "发作间期", "1": "发作前期" };
    byId("curve-point-value").textContent = `${index + 1}/${state.chartPoints.length} · 记录内 ${Number(point.time_seconds).toFixed(1)} s · 分数 ${Number(point.probability).toFixed(5)} · ${labels[String(point.y)] || "标签未提供"}`;
    const geometry = state.chartGeometry, cursor = byId("curve-cursor"), marker = byId("curve-marker");
    if (!geometry || !cursor || !marker) return;
    const x = geometry.x(point.time_seconds), y = geometry.y(point.probability);
    cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
    marker.setAttribute("cx", x); marker.setAttribute("cy", y);
  }

  function saveDownload(content, filename, mime = "application/json;charset=utf-8") {
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const anchor = node("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportPatients() {
    const keys = ["patient", "n", "positives", "prevalence", "auroc", "ap", "macro_f1", "balanced_accuracy"];
    const quote = (value) => {
      let text = String(value ?? "");
      if (/^[=+@\-\t\r]/.test(text)) text = `'${text}`;
      return `"${text.replaceAll('"', '""')}"`;
    };
    const rows = [keys, ...state.metricPatients.map((patient) => keys.map((key) => key === "macro_f1" ? patient.macro_f1 ?? patient.f1 : patient[key]))];
    saveDownload("\ufeff" + rows.map((row) => row.map(quote).join(",")).join("\r\n"), `patient_metrics_${state.selectedId}.csv`, "text/csv;charset=utf-8");
    toast("已导出当前任务的全部患者指标，包含未参与宏平均的缺失值。");
  }

  function renderArtifacts(artifacts, job) {
    const files = Array.isArray(artifacts) ? artifacts.filter((file) => file && typeof file.name === "string" && file.name) : [];
    byId("artifacts-panel").hidden = !files.length;
    replace(byId("artifacts-list"), files.map((file) => {
      const link = node("a", "artifact-link");
      const query = new URLSearchParams({ job: String(job.id), name: file.name });
      link.href = `/api/download?${query}`;
      link.setAttribute("download", file.name);
      const icon = node("span", "artifact-icon", "↓");
      icon.setAttribute("aria-hidden", "true");
      const labels = node("span");
      labels.append(node("strong", "", file.label || "输出文件"), node("small", "", file.name));
      const arrow = node("span", "artifact-arrow", "↗");
      arrow.setAttribute("aria-hidden", "true");
      link.append(icon, labels, arrow);
      return link;
    }));
  }

  function renderCharts(result, job) {
    const files = Array.isArray(result.artifacts) ? result.artifacts.filter((file) => file && /\.png$/i.test(file.name)) : [];
    const panel = byId("charts-panel");
    panel.hidden = !files.length;
    if (panel.hidden) return;
    replace(byId("chart-grid"), files.map((file) => {
      const query = new URLSearchParams({ job: String(job.id), name: file.name });
      const href = `/api/download?${query}`;
      const link = node("a", "");
      link.href = href;
      link.setAttribute("download", file.name);
      link.setAttribute("aria-label", `${file.label || "结果图表"}，点击下载 ${file.name}`);
      const img = document.createElement("img");
      img.src = `/api/artifact?${query}`;
      img.alt = file.label || file.name;
      img.loading = "lazy";
      img.addEventListener("error", () => { img.hidden = true; link.append(node("p", "", `${file.label || file.name} 预览不可用，点击下载文件核对。`)); }, { once: true });
      link.append(img);
      return link;
    }));
  }

  function renderWaveform(preview) {
    const channels = Array.isArray(preview.channels) ? preview.channels : [];
    const samples = Array.isArray(preview.samples) ? preview.samples : [];
    if (!channels.length || samples.length !== channels.length) return null;
    const width = 900, rowH = 62, left = 78, right = 112, top = 24, bottom = 34;
    const height = channels.length * rowH + top + bottom;
    const seconds = Number(preview.seconds) || 10;
    const plotW = width - left - right;
    const t = Array.isArray(preview.t) ? preview.t.map(Number) : [];
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${preview.patient || "患者"} ${preview.record || ""} 输入信号波形，${seconds} 秒，${channels.length} 导联` });
    svg.append(svgNode("title", {}, `${preview.patient || ""} ${preview.record || ""} 输入信号波形，横轴为记录内相对时间（秒），纵轴为微伏`));
    svg.append(svgNode("rect", { x: 0, y: 0, width, height, fill: "#fbfdfc" }));
    channels.forEach((channel, index) => {
      const row = samples[index] || [];
      if (!row.length) return;
      const y0 = top + index * rowH + rowH / 2;
      const values = row.map(Number);
      const min = Math.min(...values), max = Math.max(...values);
      const center = (min + max) / 2, span = Math.max(1e-9, max - min) / 2;
      const scale = rowH * 0.38 / span;
      svg.append(svgNode("line", { x1: left, x2: width - right, y1: y0, y2: y0, stroke: "#e5eee8", "stroke-width": 1 }));
      if (center - span < 0 && center + span > 0) {
        svg.append(svgNode("line", { x1: left, x2: width - right, y1: y0 - (0 - center) * scale, y2: y0 - (0 - center) * scale, stroke: "#d7b57b", "stroke-width": .7, "stroke-dasharray": "3 4" }));
      }
      let path = "";
      const step = Math.max(1, Math.ceil(t.length / 700));
      for (let i = 0; i < t.length; i += step) {
        const x = left + t[i] / seconds * plotW;
        const y = y0 - (values[i] - center) * scale;
        path += `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)} `;
      }
      svg.append(svgNode("path", { d: path, fill: "none", stroke: "#169d7d", "stroke-width": 1.2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      svg.append(svgNode("text", { x: left - 9, y: y0 + 4, fill: "#385953", "text-anchor": "end", "font-size": 11, "font-weight": 600 }, channel));
      svg.append(svgNode("text", { x: width - right + 7, y: y0 + 4, fill: "#96aaa0", "font-size": 9 }, `${min.toFixed(0)}…${max.toFixed(0)} μV`));
    });
    const xTicks = 5;
    for (let i = 0; i <= xTicks; i++) {
      const at = seconds * i / xTicks;
      const x = left + at / seconds * plotW;
      svg.append(svgNode("text", { x, y: height - 12, fill: "#96aaa0", "text-anchor": "middle", "font-size": 10 }, `${at.toFixed(1)} s`));
    }
    svg.append(svgNode("text", { x: left, y: top - 5, fill: "#8d9f9b", "font-size": 10 }, `文件内 ${Number(preview.start_second ?? 0).toFixed(0)} s 起 · ${preview.fs || 128} Hz 因果滤波 · 微伏（μV）`));
    return svg;
  }

  function renderWaveforms(result) {
    const previews = Array.isArray(result.waveform_previews) ? result.waveform_previews : [];
    const panel = byId("waveforms-panel");
    panel.hidden = !previews.length;
    if (panel.hidden) return;
    replace(byId("waveforms-list"), previews.map((preview) => {
      const svg = renderWaveform(preview);
      const card = node("article", "waveform-card");
      const caption = node("div", "waveform-caption",
        `${preview.patient || "患者"} · ${preview.record || "记录"} · 显示用波形，非评估依据`);
      if (svg) card.append(svg);
      card.append(caption);
      return card;
    }));
  }

  function renderTask(explicitSelection = false) {
    const job = state.jobs.find((item) => item.id === state.selectedId);
    byId("task-panel").hidden = !job;
    if (!job) return;
    const signature = JSON.stringify(job);
    if (signature === state.selectedSignature) return;
    state.selectedSignature = signature;
    const changedSelection = state.displayedId !== job.id;
    state.displayedId = job.id;
    const status = statusOf(job), active = isActive(job);
    byId("task-title").textContent = actionNames[job.action] || job.action || "当前任务";
    byId("task-status").textContent = statusNames[status] || status;
    byId("task-status").className = status === "completed" ? "tag tag-green" : "tag";
    byId("task-status-dot").className = `status-dot ${status}`;
    byId("task-id").textContent = `任务编号 ${job.id.slice(0, 8)}`;
    const origin = byId("result-origin");
    const source = resultOrigin(job);
    origin.className = `result-origin ${source === "history" ? "historical" : source === "demo" ? "demo" : ""}`;
    origin.textContent = source === "history" ? "原项目历史结果 · 来源独立保留" :
      source === "demo" ? "合成演示数据 · 不代表真实实验效果" :
      source === "csv" ? "本次 CSV 描述性分析 · 来源独立性需另行核对" : "本次软件任务输出";
    byId("task-progress").value = percent(job);
    byId("progress-label").textContent = `${Math.round(percent(job))}%`;
    byId("progress-row").hidden = status === "completed";
    byId("cancel-button").hidden = !active;
    byId("export-result-button").hidden = !job.result;
    byId("reuse-output-button").hidden = status !== "completed" || !job.result?.output_paths || ["history", "connectivity"].includes(job.action);
    byId("task-error").hidden = !job.error;
    byId("task-error").textContent = messageText(job.error);
    const logs = Array.isArray(job.logs) ? job.logs : [];
    byId("log-count").textContent = `${logs.length} 条`;
    const output = byId("task-logs");
    const nearBottom = output.scrollTop + output.clientHeight >= output.scrollHeight - 30;
    output.textContent = logs.slice(-1000).map(messageText).join("\n") || "暂时没有日志。";
    if (nearBottom || changedSelection) output.scrollTop = output.scrollHeight;
    if (changedSelection) byId("log-details").open = active || status === "failed";
    const resultSignature = `${job.id}:${status}:${JSON.stringify(job.result || null)}`;
    if (state.resultSignature !== resultSignature) {
      state.resultSignature = resultSignature;
      const result = job.result || {};
      const summary = result.summary;
      const message = summary ? messageText(summary) : status === "cancelled" ? "任务已取消。未完成的输出不可作为完整结果使用。" : "";
      byId("result-message").hidden = !message;
      byId("result-message").textContent = message;
      renderMetrics(result.metrics, job);
      const threshold = Number(result.metrics?.threshold);
      state.previewThreshold = Number.isFinite(threshold) && threshold >= 0 && threshold <= 1 ? threshold : .5;
      renderPreview(result.preview);
      renderCharts(result, job);
      renderWaveforms(result);
      renderArtifacts(result.artifacts, job);
      byId("result-details").hidden = !job.result;
      byId("raw-result").textContent = job.result ? JSON.stringify(job.result, null, 2) : "";
      if (changedSelection) byId("result-details").open = false;
    }
    applyCompletedOutputs(job);
    const navigation = byId("result-navigation");
    let sections = 0;
    navigation.querySelectorAll("[data-result]").forEach((button) => { button.hidden = byId(button.dataset.result).hidden; if (!button.hidden) sections++; });
    navigation.hidden = sections < 2;
    window.dispatchEvent(new CustomEvent("eeg:job-selected", { detail: { ...job, explicitSelection } }));
  }

  function mergeJobs(jobs) {
    if (!Array.isArray(jobs)) throw new Error("任务列表格式不正确，请检查本地服务版本。");
    state.jobs = jobs.filter((job) => job && typeof job.id === "string");
    if (!state.selectedId && state.jobs.length) state.selectedId = state.jobs[0].id;
    if (state.selectedId && !state.jobs.some((job) => job.id === state.selectedId)) state.selectedId = state.jobs[0]?.id || null;
    renderJobs();
    renderTask();
    renderOverview();
    state.jobs.forEach(applyCompletedOutputs);
    updateControls();
  }

  function selectJob(id, scroll = false) {
    state.selectedId = id;
    state.selectedSignature = "";
    renderJobs();
    renderTask(true);
    if (scroll) byId("task-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function schedulePoll(delay) {
    window.clearTimeout(state.pollTimer);
    if (!state.disposed) state.pollTimer = window.setTimeout(poll, delay);
  }

  async function initializeInfo() {
    const info = await fetchJSON("/api/info");
    if (!info || typeof info.csrf_token !== "string" || !info.csrf_token) throw new Error("本地服务缺少会话校验信息，请重新启动软件。");
    state.info = info;
    window.dispatchEvent(new CustomEvent("eeg:ready", { detail: info }));
    setDefaultPaths(info.default_paths);
    if (typeof info.project_path === "string" && !byId("project-path").dataset.edited) byId("project-path").value = info.project_path;
  }

  async function poll() {
    if (state.polling || state.disposed) return;
    state.polling = true;
    try {
      if (!state.info) await initializeInfo();
      const response = await fetchJSON("/api/jobs");
      mergeJobs(response.jobs);
      state.failures = 0;
      setConnection(true);
    } catch (error) {
      state.failures += 1;
      // A restarted local process has a new CSRF token; refresh metadata on reconnect.
      state.info = null;
      setConnection(false, `${error.message} 任务记录仍保留，连接恢复后会自动更新。`);
    } finally {
      state.polling = false;
      const delay = state.failures ? Math.min(30000, 2500 * 2 ** Math.min(state.failures - 1, 4)) :
        document.hidden ? 12000 : hasActiveJob() ? 1200 : 5000;
      schedulePoll(delay);
    }
  }

  async function startJob(action, params, button) {
    if (state.submitting || hasActiveJob()) {
      toast("已有任务正在运行。请等待完成，或取消后再开始。", true);
      return;
    }
    if (!state.connected || !state.info?.csrf_token) {
      toast("尚未连接本地服务，请刷新或检查软件启动窗口。", true);
      return;
    }
    state.submitting = true;
    updateControls();
    const original = button?.textContent;
    if (button) button.textContent = "正在提交…";
    try {
      const response = await fetchJSON("/api/jobs", { method: "POST", body: JSON.stringify({ action, params }) });
      const job = response.job || response;
      if (!job || typeof job.id !== "string") throw new Error("服务未返回有效任务标识，请刷新任务记录后确认。");
      state.createdIds.add(job.id);
      state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)];
      state.selectedId = job.id;
      state.selectedSignature = "";
      renderJobs();
      renderTask();
      if (action !== "connectivity") byId("task-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      toast("任务已提交，运行进度和结果会自动更新。");
      schedulePoll(300);
      return job;
    } catch (error) {
      toast(error.message, true);
      // A network timeout may follow a successful server-side enqueue; refresh before retrying.
      schedulePoll(500);
    } finally {
      state.submitting = false;
      if (button && original) button.textContent = original;
      updateControls();
    }
  }

  function bindForm(id, action, fields) {
    byId(id).addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const data = new FormData(form), params = {};
      fields.forEach((field) => { params[field] = String(data.get(field) || "").trim(); });
      if (action === "evaluate") {
        params.threshold = Number(params.threshold);
        if (!Number.isFinite(params.threshold) || params.threshold < 0 || params.threshold > 1) {
          toast("分类阈值必须是 0 到 1 之间的数字。", true);
          return;
        }
      }
      if (fields.some((field) => field !== "threshold" && !params[field])) {
        toast("请填写完整的本机文件路径。", true);
        return;
      }
      startJob(action, params, form.querySelector("button[type=submit]"));
    });
  }

  async function cancelJob() {
    const job = state.jobs.find((item) => item.id === state.selectedId);
    if (!job || !isActive(job) || state.cancelPending) return;
    state.cancelPending = true;
    updateControls();
    try {
      await fetchJSON("/api/cancel", { method: "POST", body: JSON.stringify({ id: job.id }) });
      toast("已提交取消请求。软件会在安全的检查点停止任务。");
      schedulePoll(200);
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.cancelPending = false;
      updateControls();
    }
  }

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  document.querySelectorAll("[data-go]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.go));
  });
  document.querySelector(".brand").addEventListener("click", () => showView("overview"));
  byId("demo-button").addEventListener("click", (event) => startJob("demo", {}, event.currentTarget));
  byId("cancel-button").addEventListener("click", cancelJob);
  byId("preview-patient").addEventListener("change", renderRecordOptions);
  byId("preview-record").addEventListener("change", renderChart);
  byId("curve-point").addEventListener("input", renderCurvePoint);
  byId("job-search").addEventListener("input", renderJobs);
  byId("job-status-filter").addEventListener("change", renderJobs);
  byId("job-action-filter").addEventListener("change", renderJobs);
  byId("patient-search").addEventListener("input", renderPatientTable);
  byId("patient-sort").addEventListener("change", renderPatientTable);
  byId("export-patients-button").addEventListener("click", exportPatients);
  byId("export-result-button").addEventListener("click", () => {
    const job = state.jobs.find((item) => item.id === state.selectedId);
    if (job?.result) saveDownload(JSON.stringify(job, null, 2), `task_${job.id}.json`);
  });
  byId("export-curve-button").addEventListener("click", () => {
    const svg = byId("probability-chart").querySelector("svg");
    if (svg) saveDownload(new XMLSerializer().serializeToString(svg), `risk_curve_${state.selectedId}.svg`, "image/svg+xml;charset=utf-8");
  });
  byId("reuse-output-button").addEventListener("click", () => {
    const job = state.jobs.find((item) => item.id === state.selectedId);
    if (!job?.result?.output_paths) return;
    setDefaultPaths(job.result.output_paths, true);
    const destination = job.action === "prepare" ? "train" : ["train", "demo"].includes(job.action) ? "predict" : "evaluate";
    showView(destination);
    byId(`view-${destination}`).scrollIntoView({ behavior: "smooth", block: "start" });
    toast(resultOrigin(job) === "demo" ? "合成演示数据的路径已填入。后续结果仍属于演示。" : "该任务的输出路径已填入后续步骤，请核对后运行。");
  });
  byId("result-navigation").querySelectorAll("[data-result]").forEach((button) => button.addEventListener("click", () => {
    byId(button.dataset.result).scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  byId("project-path").addEventListener("input", (event) => { event.currentTarget.dataset.edited = "true"; });
  byId("refresh-button").addEventListener("click", async () => {
    state.failures = 0;
    if (state.polling) { toast("正在刷新，请稍候。 "); return; }
    state.info = null;
    window.clearTimeout(state.pollTimer);
    await poll();
    if (state.connected) toast("已连接本地服务，任务记录已更新。");
  });
  bindForm("prepare-form", "prepare", ["manifest"]);
  bindForm("train-form", "train", ["dataset"]);
  bindForm("predict-form", "predict", ["dataset", "model"]);
  bindForm("evaluate-form", "evaluate", ["predictions", "threshold"]);
  bindForm("history-form", "history", ["project"]);
  window.addEventListener("hashchange", () => showView(window.location.hash.slice(1), false));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) schedulePoll(100); });
  window.addEventListener("pagehide", () => { state.disposed = true; window.clearTimeout(state.pollTimer); });
  window.addEventListener("pageshow", () => { if (state.disposed) { state.disposed = false; schedulePoll(100); } });
  window.EEGApp = Object.freeze({
    submit: (action, params, button) => startJob(action, params, button),
    getInfo: () => state.info,
    getJobs: () => state.jobs.slice(),
    getSelectedJob: () => state.jobs.find(job => job.id === state.selectedId) || null,
    showView,
  });
  showView(window.location.hash.slice(1) || "overview", false);
  updateControls();
  poll();
})();
