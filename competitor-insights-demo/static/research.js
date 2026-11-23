"use strict";

const el = (id) => document.getElementById(id);
let feedbackWindow = 7;
const questionByIntent = {
  opportunity: "哪项创作流程改动最值得优先验证？现有证据支持什么结论？",
  benchmark: "两款工具在哪些能力上存在实测差距？哪些项目仍缺证据？",
  change: "从前一版到当前版本，哪些断点已经消除？哪些仍应保留为回归检查？",
};
const intentLabel = {opportunity: "机会优先级", benchmark: "能力对标", change: "版本复验"};
const transitionLabel = {resolved: "阻碍已解除", regressed: "出现新阻碍", same_version_conflict:"同版结果不一致，待复测", stable: "表现相同", unknown: "待复验"};
const state = {snapshot: null, revision: 0, dialogRequest: 0, busy: false, ready: false, mode: "local"};
const node = (tag, text, className) => {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
};
function button(label, handler, css = "text-button") {
  const b = node("button", label, css);
  b.type = "button";
  b.addEventListener("click", handler);
  return b;
}
function inform(text = "", error = false) {
  el("notice").textContent = text;
  el("notice").classList.toggle("error", error);
}
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === "string" ? data.detail : "请求未完成，请重新检查条件。");
  return data;
}
function handle(action) {
  return async (event) => {
    try { await action(event); }
    catch (error) { inform(error.message, true); }
  };
}
function selection() {
  const request = {
    left: el("left").value,
    right: el("right").value,
    intent: document.querySelector("[name=intent]:checked").value,
    window_days: feedbackWindow,
  };
  if (el("as-of").value) request.as_of = el("as-of").value;
  return request;
}
function configureActions() {
  const locked = state.busy;
  el("generate").disabled = locked || !state.ready || state.mode === "incomplete";
  el("generate").textContent = locked ? "正在生成报告…" : "生成研究报告 →";
  for (const control of el("setup").querySelectorAll("input,select,textarea")) control.disabled = locked;
  for (const id of ["import-open", "history-open", "live"]) el(id).disabled = locked;
}
async function products() {
  const {products} = await api("/api/products");
  for (const id of ["left", "right"]) {
    const select = el(id);
    const before = select.value;
    select.replaceChildren(...products.map((p) => new Option(p.name, p.product)));
    if (products.some((p) => p.product === before)) select.value = before;
    else if (id === "right" && products.length > 1) select.selectedIndex = 1;
  }
}
async function preview(successMessage = "") {
  const revision = ++state.revision;
  state.ready = false;
  configureActions();
  el("results").hidden = true;
  el("loading").hidden = false;
  inform();
  try {
    const form = selection();
    if (!form.left || !form.right || form.left === form.right) throw Error("请选择两款不同的产品。");
    const data = await api("/api/research?" + new URLSearchParams(form));
    if (revision !== state.revision) return;
    display(data);
    state.ready = true;
    inform(successMessage);
  } catch (error) {
    if (revision === state.revision) inform(error.message, true);
  } finally {
    if (revision === state.revision) {
      el("loading").hidden = true;
      configureActions();
    }
  }
}
function openDialog(title, kicker) {
  state.dialogRequest++;
  el("drawer-title").textContent = title;
  el("drawer-kicker").textContent = kicker || "";
  const body = el("drawer-body");
  body.replaceChildren();
  if (!el("drawer").open) el("drawer").showModal();
  el("drawer").scrollTop = 0;
  return body;
}
function resolveSource(row) { return row.source_label || row.source_type; }
function nameFor(sku) {
  return state.snapshot.products.find((product) => product.sku === sku)?.name || sku;
}
function capabilityFor(key) {
  return state.snapshot.capabilities.find((capability) => capability.key === key)?.label || key;
}
function evidenceRows(refs) {
  return [...new Set(refs)].map((id) => state.evidenceById.get(id)).filter(Boolean);
}
function openEvidence(rows, title = "证据库") {
  const area = openDialog(title, "截至 " + state.snapshot.as_of + " · " + rows.length + " 条证据");
  const list = node("div");
  function draw(query = "") {
    const q = query.toLocaleLowerCase();
    const matches = rows.filter((r) =>
      [r.id, r.source, resolveSource(r), nameFor(r.product), r.date, r.excerpt].join(" ").toLocaleLowerCase().includes(q)
    );
    list.replaceChildren();
    for (const r of matches) {
      const item = node("section", undefined, "evidence-row");
      item.append(node("small", resolveSource(r) + " · " + r.date + " · " + (r.version || "版本待确认")),
        node("h3", nameFor(r.product) + " · " + capabilityFor(r.capability)), node("blockquote", r.excerpt),
        node("small", "来源 " + r.source + "　·　" + r.id));
      if (/^https:\/\//.test(r.source)) {
        const link = node("a", "打开原始网页 ↗");
        link.href = r.source; link.target = "_blank"; link.rel = "noopener";
        item.append(link);
      }
      list.append(item);
    }
    if (!matches.length) list.append(node("p", "当前范围内没有匹配的证据。"));
  }
  if (rows.length > 3) {
    const search = node("input");
    search.type = "search";
    search.placeholder = "搜索产品、日期、出处或原文";
    search.setAttribute("aria-label", "搜索证据");
    search.addEventListener("input", () => draw(search.value));
    area.append(search);
  }
  area.append(list);
  draw();
}
function refLink(id) {
  const row = evidenceRows([id])[0];
  if (!row) return document.createTextNode("[" + id + "]");
  return button(id, () => openEvidence([row], "引用原文"), "ref");
}
function groupLink(refs, label) {
  const records = evidenceRows(refs || []);
  if (!records.length) return null;
  return button(label || "核对 " + records.length + " 条证据", () => openEvidence(records, "引用原文"), "text-button cell-link");
}
function renderFindings(data) {
  el("finding-count").textContent = data.findings.length + " 项";
  const boxes = data.findings.map((f, index) => {
    const box = node("div", undefined, "finding");
    const head = node("div", undefined, "find-heading");
    head.append(node("span", f.priority, "priority"), node("h3", f.title));
    box.append(head, node("p", f.reason));
    const extra = node("details");
    extra.open = index === 0;
    extra.append(node("summary", "行动与验证口径"));
    const definition = node("dl");
    for (const [term, value] of [["下一步", f.action], ["验证实验", f.experiment], ["观测口径", f.metric]]) {
      definition.append(node("dt", term), node("dd", value));
    }
    extra.append(definition);
    const link = groupLink(f.refs);
    box.append(extra);
    if (link) box.append(link);
    return box;
  });
  el("findings").replaceChildren(...boxes);
}
function renderCoverage(data) {
  const groups = new Map();
  for (const row of data.evidence) {
    const key = resolveSource(row);
    groups.set(key, (groups.get(key) || 0) + 1);
  }
  const total = node("div", undefined, "cover-n");
  total.append(node("strong", String(data.evidence.length)), node("span", "条可核对原文"));
  el("coverage-summary").replaceChildren(total);
  const bars = [...groups].map(([source, count]) => {
    const row = node("div", undefined, "source-row");
    const bar = node("meter");
    bar.setAttribute("aria-label", source + "证据条数"); bar.max = data.evidence.length; bar.value = count;
    row.append(node("span", source), bar, node("span", String(count)));
    return row;
  });
  el("source-bars").replaceChildren(...bars);
}
function renderMatrix(data) {
  el("matrix-count").textContent = data.capabilities.length + " 项能力";
  const table = node("table");
  table.setAttribute("aria-label", "两款产品的能力对标与证据");
  const head = table.createTHead().insertRow();
  for (const value of ["能力", ...data.products.map((p) => p.name)]) {
    const th = node("th", value); th.scope = "col"; head.append(th);
  }
  const body = table.createTBody();
  for (const item of data.capabilities) {
    const row = body.insertRow();
    const title = node("th", item.label); title.scope = "row"; row.append(title);
    for (const cell of item.cells) {
      const td = row.insertCell();
      td.append(node("span", cell.label, "status " + cell.status), node("p", cell.note, "cell-note"));
      const link = groupLink(cell.refs);
      if (link) td.append(link);
    }
  }
  el("matrix").replaceChildren(table);
}
function renderChanges(data) {
  const changes = data.changes || [];
  el("change-panel").hidden = !changes.length && data.intent !== "change";
  const rows = changes.map((c) => {
    const row = node("div", undefined, "change-row");
    row.append(node("strong", nameFor(c.product) + " · " + capabilityFor(c.capability) + "　" + transitionLabel[c.transition]),
      node("p", c.from_version + "（" + c.from_date + "） → " + c.to_version + "（" + c.to_date + "）", "sub"));
    const link = groupLink(c.refs, "核对两次观测");
    if (link) row.append(link);
    return row;
  });
  el("changes").replaceChildren(...(rows.length ? rows : [node("p", "尚无可比较的两批实测。导入新版走查后可以复验。")]));
}
function renderTrace(data) {
  const calls = data.model_trace || [];
  const modelSteps = new Set(calls.map((t) => t.step));
  const knownOrigin = Array.isArray(data.model_trace) || data.mode !== "model";
  el("trace-count").textContent = knownOrigin
    ? "程序处理 " + (data.trace.length - calls.length) + " 步 · 模型调用 " + calls.length + " 次"
    : data.trace.length + " 步";
  const labels = {compare_capabilities:"能力对标", list_changes:"查阅版本变化", find_evidence:"检索材料", open_evidence:"核对原文", read_feedback_trends:"读取反馈趋势"};
  const items = data.trace.map((t) => {
    const li = node("li");
    if (knownOrigin) li.append(node("span", modelSteps.has(t.step) ? "模型调用" : "程序处理", "trace-origin"));
    const label = labels[t.step];
    li.append(node("strong", label ? label + " · " + t.step : t.step), node("p", t.detail));
    const link = groupLink(t.refs, "本步关联的证据");
    if (link) li.append(link);
    return li;
  });
  el("trace").replaceChildren(...items);
}
function appendInline(parent, content) {
  for (const part of content.split(/(\[[^\]\n]+\]|\*\*[^\n*]+\*\*)/g)) {
    if (part.startsWith("[") && part.endsWith("]")) parent.append(refLink(part.slice(1, -1)));
    else if (part.startsWith("**") && part.endsWith("**")) parent.append(node("strong", part.slice(2, -2)));
    else parent.append(document.createTextNode(part));
  }
}
function tableColumns(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}
function renderMarkdown(text) {
  const root = el("report-body");
  root.replaceChildren();
  const lines = (text || "").split("\n");
  let list = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) { list = null; continue; }
    const next = (lines[i + 1] || "").trim();
    if (line.includes("|") && next.includes("|") && tableColumns(next).every((cell) => /^:?-{3,}:?$/.test(cell))) {
      const table = node("table");
      const heading = table.createTHead().insertRow();
      for (const value of tableColumns(line)) {
        const th = node("th"); th.scope = "col"; appendInline(th, value); heading.append(th);
      }
      const tbody = table.createTBody();
      i += 1;
      while (i + 1 < lines.length && lines[i + 1].includes("|")) {
        const tr = tbody.insertRow();
        for (const value of tableColumns(lines[++i])) appendInline(tr.insertCell(), value);
      }
      root.append(table); list = null; continue;
    }
    const bullet = line.match(/^(?:([-*])|\d+[.)])\s+(.*)$/);
    if (bullet) {
      const tag = bullet[1] ? "ul" : "ol";
      if (!list || list.localName !== tag) { list = node(tag); root.append(list); }
      const item = node("li"); appendInline(item, bullet[2]); list.append(item);
      continue;
    }
    list = null;
    const row = node(/^#{1,6}\s/.test(line) ? "h3" : "p");
    appendInline(row, line.replace(/^#{1,6}\s+/, ""));
    root.append(row);
  }
}
function display(data) {
  state.snapshot = data;
  state.evidenceById = new Map(data.evidence.map((record) => [record.id, record]));
  const saved = data.id != null;
  el("scope").textContent = saved ? "报告快照 #" + data.id : "当前研究预览";
  el("scope-date").textContent = intentLabel[data.intent] + " · 截至 " + data.as_of;
  el("results").hidden = false;
  el("loading").hidden = true;
  el("live").hidden = !saved;
  el("report-panel").hidden = !saved;
  if (saved) {
    el("report-title").textContent = data.title;
    const mode = data.mode === "model" ? "模型工具调用" + (data.model ? " · " + data.model : "") : "本地规则分析";
    const c = data.counts;
    el("report-meta").textContent = mode + (c ? " · 读取 " + c.materials + " 份材料 / " + c.observations + " 条原文 · 引用 " + c.citations + " 条" : "");
    el("report-question").textContent = data.question;
    el("export").href = "/api/research/reports/" + encodeURIComponent(data.id) + "/export";
    renderMarkdown(data.markdown);
  }
  renderFindings(data);
  renderCoverage(data);
  renderMatrix(data);
  renderChanges(data);
  renderTrace(data);
}
async function submit(event) {
  event.preventDefault();
  if (state.busy || !state.ready) return;
  const question = el("question").value.trim();
  if (!question) { inform("请填写研究问题。", true); return; }
  state.busy = true;
  configureActions();
  inform("正在生成报告…");
  try {
    const data = await api("/api/research/reports", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...selection(), question}),
    });
    display(data);
    inform("报告已保存，可从报告库重新打开。");
    el("report-panel").scrollIntoView({block:"start"});
  } finally {
    state.busy = false;
    configureActions();
  }
}
async function openHistory() {
  const area = openDialog("报告库", "保留生成时的材料和结论");
  const request = state.dialogRequest;
  const list = node("div");
  const error = node("p", "");
  error.setAttribute("role", "status");
  const more = button("加载更早的报告", () => load());
  const old = node("a", "查看原始走查报告 →");
  old.href = "/static/walkthrough.html#history";
  area.replaceChildren(list, more, error, old);
  let before = null;
  function reportButton(r) {
    const entry = button("#" + r.id + "　" + r.title, async () => {
      try {
        const report = await api("/api/research/reports/" + encodeURIComponent(r.id));
        if (request !== state.dialogRequest) return;
        ++state.revision;
        state.ready = false;
        configureActions();
        display(report);
        el("drawer").close();
        inform("已打开历史快照。点击“返回当前研究”可继续分析。");
        el("report-panel").scrollIntoView({block:"start"});
      } catch (e) { error.textContent = e.message; }
    }, "past");
    if (r.created_at) entry.append(node("small", new Date(r.created_at).toLocaleString("zh-CN")));
    return entry;
  }
  async function load() {
    more.disabled = true;
    error.textContent = "正在读取报告…";
    try {
      const query = before === null ? "" : "?before=" + encodeURIComponent(before);
      const page = await api("/api/research/reports" + query);
      if (request !== state.dialogRequest) return;
      list.append(...page.reports.map(reportButton));
      if (!list.hasChildNodes()) list.append(node("p", "还没有研究报告。选择一个研究方向，生成第一份报告。"));
      before = page.next_before;
      more.hidden = before === null;
      error.textContent = "";
    } catch (e) {
      error.textContent = e.message;
      more.textContent = "重试读取报告";
    } finally {
      more.disabled = false;
    }
  }
  await load();
}
function openImport() {
  const area = openDialog("导入研究材料", "UTF-8 CSV");
  for (const [title, note, path, file, label] of [
    ["新版任务走查", "导入新版本的实际操作记录，按步骤复查产品断点。", "/sample.csv", "csv-file", "选择走查 CSV"],
    ["版本公告与用户反馈", "导入产品说明或体验原文。新材料可补充能力对标，也可能与已有材料形成分歧。", "/api/research/materials/sample", "source-file", "选择来源 CSV"],
  ]) {
    const row = node("section", undefined, "evidence-row");
    const controls = node("div", undefined, "import-tools");
    const download = node("a", "下载样例"); download.href = path; download.download = "";
    controls.append(download);
    if (file === "csv-file") {
      const example = node("a", "下载导出复测样例");
      example.href = "/api/research/materials/verification-sample";
      example.download = "";
      controls.append(example);
    }
    controls.append(button(label, () => el(file).click(), "secondary"));
    row.append(node("h3", title), node("p", note), controls);
    area.append(row);
  }
}
async function upload(event, source) {
  const input = event.target;
  const file = input.files?.[0];
  if (!file) return;
  input.value = "";
  el("drawer").close();
  if (file.size > 1048576) { inform("CSV 文件不能超过 1 MiB。", true); return; }
  state.busy = true;
  configureActions();
  inform("正在核验并导入文件…");
  try {
    const result = await api(source ? "/api/research/materials/import" : "/api/import", {
      method: "POST", headers: {"Content-Type": "text/csv"}, body: file,
    });
    await products();
    const message = source ? "新增 " + result.inserted + " 条来源记录，" + result.unchanged + " 条已存在。" : "已导入 " + result.rows + " 条走查记录。";
    await preview(message + (el("as-of").value ? "当前预览仍按所选截止日筛选。" : "当前研究预览已更新。"));
  } finally {
    state.busy = false;
    configureActions();
  }
}
el("drawer").addEventListener("close", () => { state.dialogRequest++; });
el("close").addEventListener("click", () => el("drawer").close());
el("history-open").addEventListener("click", openHistory);
el("import-open").addEventListener("click", openImport);
el("csv-file").addEventListener("change", handle((e) => upload(e, false)));
el("source-file").addEventListener("change", handle((e) => upload(e, true)));
el("setup").addEventListener("submit", handle(submit));
el("live").addEventListener("click", () => preview());
for (const id of ["sources", "library-open"]) {
  el(id).addEventListener("click", () => openEvidence(state.snapshot.evidence));
}
for (const id of ["left", "right", "as-of"]) el(id).addEventListener("change", () => preview());
for (const radio of document.querySelectorAll("[name=intent]")) {
  radio.addEventListener("change", () => {
    el("question").value = questionByIntent[radio.value];
    preview();
  });
}
handle(async () => {
  const [status] = await Promise.all([api("/api/status"), products()]);
  // A topic link carries the original comparison, cutoff and feedback window.
  const params = new URLSearchParams(location.search);
  for (const id of ["left", "right"]) {
    const value = params.get(id);
    if ([...el(id).options].some(option => option.value === value)) el(id).value = value;
  }
  const cutoff = params.get("as_of");
  if (cutoff && /^\d{4}-\d{2}-\d{2}$/.test(cutoff)) el("as-of").value = cutoff;
  const intent = params.get("intent");
  if (Object.hasOwn(questionByIntent, intent)) {
    document.querySelector('input[name="intent"][value="' + intent + '"]').checked = true;
    el("question").value = questionByIntent[intent];
  }
  const question = params.get("question");
  if (question) el("question").value = question.slice(0, 2000);
  const days = Number(params.get("window_days"));
  if ([7, 14, 30].includes(days)) feedbackWindow = days;
  state.mode = status.mode;
  el("mode").textContent = {model:"模型工具调用",local:"本地规则分析",incomplete:"模型配置待补全"}[state.mode];
  el("question-hint").textContent = state.mode === "local" ? "本地模式可按能力追问，如“选区精度还缺什么证据？”" : state.mode === "model" ? "模型将按问题检索并核对原文。" : "在 .env 填全模型配置并重启后即可生成报告。";
  await preview();
})();
