"use strict";
const el = id => document.getElementById(id);
const steps = {generate:"生成首图", iterate:"继续迭代", edit:"局部编辑", mask:"选区精度", reference:"参考约束", history:"版本恢复", export:"交付导出"};
const node = (tag, text, css) => {
  const item = document.createElement(tag);
  item.textContent = text;
  if (css) item.className = css;
  return item;
};
async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw Error(data.detail || "请求失败");
  return data;
}
function handle(fn) {
  return async () => {
    try { await fn(); } catch (err) { el("notice").textContent = err.message; }
  };
}
async function loadProducts() {
  const data = await api("/api/products");
  for (const id of ["left","right"]) {
    const previous = el(id).value;
    el(id).replaceChildren(...data.products.map(p => new Option(p.name,p.product)));
    if (previous) el(id).value = previous;
    else if (data.products.some(p => p.product === (id === "left" ? "INK" : "RILL"))) el(id).value = id === "left" ? "INK" : "RILL";
    else if (id === "right") el(id).selectedIndex = 1;
  }
}
async function compare() {
  const q = new URLSearchParams({left:el("left").value, right:el("right").value});
  el("matrix").replaceChildren();
  const pair = await api("/api/compare?" + q);
  for (const tool of pair.products) {
    const rows = pair.observations.filter(r => r.product === tool.sku);
    const last = rows.at(-1);
    if (!last) {
      const card = node("div", "", "card");
      card.append(node("h3",tool.name), node("p","尚无实际任务走查，请先导入走查记录。官方文档可在研究决策台查看。"));
      el("matrix").append(card); continue;
    }
    const card = node("div","","card");
    card.append(node("h3",tool.name), node("small",last.version+" · "+last.date));
    for (const [key,label] of Object.entries(steps)) {
      const row = rows.find(r => r.date === last.date && r.step === key);
      if (!row && !["generate","iterate","edit"].includes(key)) continue;
      card.append(node("p", label + "　" + (row?.observation || "缺少走查记录")));
    }
    el("matrix").append(card);
  }
}
function showReport(report) {
  el("report").hidden = false;
  el("title").textContent = report.title;
  const c = report.counts;
  el("counts").textContent = (report.mode === "model" ? "模型工具调用" : "本地规则分析") + " · 读取 "+c.materials+" 份材料 / "+c.observations+" 条观测 · 引用 "+c.citations+" 条";
  el("reads").replaceChildren(...report.reads.map(r => node("li", r.material + " · " + r.reference)));
  el("body").replaceChildren();
  el("evidence").replaceChildren();
  const targets = new Map();
  for (const row of report.evidence) {
    const item = node("div",row.reference+" · "+row.material+" · "+row.name+" "+row.version+"　"+row.observation,"proof");
    item.tabIndex = -1;
    targets.set(row.reference,item);
    el("evidence").append(item);
  }
  for (const line of report.markdown.split("\n").filter(Boolean)) {
    const p = node(line.startsWith("## ") ? "h4" : "p","");
    for (const token of line.replace(/^## /,"").split(/(\[[^\]]+\])/g)) {
      const target = targets.get(token.slice(1,-1));
      if (target && token.startsWith("[")) {
        const button = node("button",token,"ref");
        button.onclick = () => { el("proof").open = true; target.focus(); };
        p.append(button);
      } else p.append(document.createTextNode(token));
    }
    el("body").append(p);
  }
  el("export").href = "/api/reports/"+report.id+"/export";
}
async function history() {
  const {reports} = await api("/api/reports");
  el("history").replaceChildren();
  for (const report of reports) {
    const b = node("button", "#"+report.id+"　"+report.title+"　"+new Date(report.created_at).toLocaleString());
    b.onclick = handle(async () => showReport(await api("/api/reports/"+report.id)));
    el("history").append(b);
  }
  if (!reports.length) el("history").textContent = "尚无已保存的报告。";
}
el("generate").onclick = handle(async () => {
  el("generate").disabled = true;
  try {
    el("notice").textContent = "正在分析…";
    const report = await api("/api/reports",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({left:el("left").value,right:el("right").value,question:el("question").value})});
    showReport(report); await history(); el("notice").textContent = "报告已保存。";
  } finally { el("generate").disabled = false; }
});
el("file").onchange = handle(async () => {
  const file = el("file").files[0];
  if (!file) return;
  el("file").value = "";
  const data = await api("/api/import",{method:"POST", headers:{"Content-Type":"text/csv"},body:file});
  await loadProducts(); await compare();
  el("notice").textContent = "已导入 "+data.rows+" 条记录，可重新生成报告。";
});
for (const id of ["left","right"]) el(id).onchange = handle(compare);
handle(async () => {
  const status = await api("/api/status");
  el("mode").textContent = {local:"本地规则分析 · 固定比较局部编辑",model:"模型工具调用",incomplete:"模型配置待补全"}[status.mode];
  el("question").readOnly = status.mode === "local";
  await loadProducts(); await compare(); await history();
})();

handle(async () => { const id = new URLSearchParams(location.search).get("report"); if (id && /^\d+$/.test(id)) { showReport(await api("/api/reports/"+id)); el("report").scrollIntoView({block:"start"}); } })();
