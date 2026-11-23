"use strict";

const $ = (id) => document.getElementById(id);
const state = {data:null, scope:null, followups:[], before:null, generation:0, importBusy:false};
const statusNames = {open:"跟进中", reviewed:"已复核", archived:"已归档"};
const positionNames = {support:"正向体验", friction:"问题反馈", unknown:"态度未明"};
const capabilityNames = {generate:"首图生成", iterate:"多轮迭代", edit:"编辑衔接", mask:"选区精度", reference:"参考约束", history:"版本恢复", export:"交付导出"};

function node(tag, text, css) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (css) item.className = css;
  return item;
}
function button(label, action, css="text-button") {
  const b=node("button",label,css);
  b.type="button";
  b.addEventListener("click",action);
  return b;
}
function inform(text="", error=false) {
  $("notice").textContent=text;
  $("notice").classList.toggle("error",error);
}
async function api(path, options) {
  const response=await fetch(path,options);
  let data;
  try { data=await response.json(); } catch { throw new Error("服务返回了无法读取的结果，请重试。"); }
  if (!response.ok) throw new Error(typeof data.detail==="string" ? data.detail : "操作未完成，请核对输入后重试。");
  return data;
}
const post = (data, method="POST") => ({method,headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
function selected() {
  const params={left:$("left").value,right:$("right").value,window_days:$("window").value};
  if ($("as-of").value) params.as_of=$("as-of").value;
  return params;
}
function nameFor(product) {
  return state.data?.products?.find((p)=>(p.sku || p.product)===product)?.name || product;
}
function dateRange(period) { return period.start+" — "+period.end; }
function excerptCount(n) { return n+" 条摘录"; }
function sources(n,denominator) {
  return n+" / "+denominator;
}
function splitWindow(rows,period) {
  return rows.filter((r)=>r.date>=period.start && r.date<=period.end);
}
function empty(text) {return node("p",text,"empty");}
function showDialog(title,sub) {
  $("dialog-title").textContent=title;
  $("dialog-sub").textContent=sub;
  $("dialog-body").replaceChildren();
  if (!$("radar-dialog").open) $("radar-dialog").showModal();
  $("radar-dialog").scrollTop=0;
  return $("dialog-body");
}
function renderQuotes(area, rows) {
  if (!rows.length) { area.append(empty("当前范围内没有可用原文。"));return; }
  for (const r of rows) {
    const item=node("article",undefined,"quote");
    item.append(node("small",(r.date || "")+" · "+(positionNames[r.position] || "反馈摘录")+(r.product?" · "+nameFor(r.product):"")),
      node("blockquote",r.excerpt),node("small",[r.source,r.origin,r.id].filter(Boolean).join(" · ")));
    area.append(item);
  }
}
function evidenceByRef(refs,pool=state.data?.evidence || []) {
  const index=new Map(pool.map((r)=>[r.id,r]));
  return [...new Set(refs || [])].map((id)=>index.get(id)).filter(Boolean);
}
function addEvidence(container, refs, title) {
  const records=evidenceByRef(refs);
  container.append(button(title+"（"+records.length+"）",()=>{
    const area=showDialog(title,"资料截止 "+state.data.as_of);
    renderQuotes(area,records);
  }));
}
function researchLink(topic) {
  const {left,right}=state.scope;
  const p=new URLSearchParams({left,right,as_of:state.data.as_of,intent:"opportunity",window_days:state.data.window_days,
    question:"请结合"+nameFor(topic.product)+"的「"+topic.label+"」反馈与同期"+(capabilityNames[topic.capability] || "能力")+"走查，判断是否应优先处理，并说明还需怎样复测。"});
  const a=node("a","带着问题去研究台 →","research-link");
  a.href="/?"+p;return a;
}
function renderPeriods() {
  const {periods,evidence}=state.data;
  for (const [key,label] of [["current","本期"],["previous","上期"]]) {
    const period=periods[key], rows=splitWindow(evidence,period);
    const countFacts=node("div",undefined,"period-count");
    countFacts.append(node("strong",String(new Set(rows.map((r)=>r.origin)).size),"period-number"),node("span","个独立反馈来源"));
    $(key+"-summary").replaceChildren(node("div",label+" · "+dateRange(period),"period-label"),countFacts,node("p",excerptCount(rows.length)+" · 所选两款产品合计"));
  }
}
function openTopic(t) {
  const area=showDialog(t.label,nameFor(t.product)+" · "+(capabilityNames[t.capability] || t.capability)+" · "+t.trend);
  area.append(node("p",t.reason,"period-facts"));
  const current=sources(t.current_sources,t.current_total), previous=sources(t.previous_sources,t.previous_total);
  area.append(node("p","本期 "+current+" 个来源；上期 "+previous+" 个来源。","period-facts"),
    node("p","本期提到问题的来源 "+t.current_negative+" 个，有正向体验的来源 "+t.current_positive+" 个。上期分别为 "+t.previous_negative+" 个和 "+t.previous_positive+" 个。","period-facts"),
    node("p","窗口内最早记录 "+t.first_seen+"，最近记录 "+t.last_seen+"。","sub"),researchLink(t));
  if (t.channel_mix && t.channel_mix.length) {
    const mix=node("details",undefined,"method");
    mix.append(node("summary","这个主题来自哪些渠道？"),channelTable(t.channel_mix));
    area.append(mix);
  }
  const records=evidenceByRef(t.refs);
  const repIds=new Set((t.representatives || []).map((r)=>r.id));
  const featured=records.filter((r)=>repIds.has(r.id));
  if (featured.length) {
    area.append(node("h3","代表原文（"+featured.length+"）"));
    renderQuotes(area,featured);
    const other=records.filter((r)=>!repIds.has(r.id));
    if(other.length) {
      const details=node("details",undefined,"method");
      details.append(node("summary","展开其余 "+other.length+" 条摘录"));
      renderQuotes(details,other);
      area.append(details);
    }
  } else {
    area.append(node("h3","主题原文（"+records.length+"）"));
    renderQuotes(area,records);
  }
}
function channelTable(rows) {
  if (!rows || !rows.length) return empty("当前窗口没有可比较的渠道样本。");
  const table=node("table",undefined,"radar-rows channels");
  const head=table.createTHead().insertRow();
  for (const label of ["产品 / 渠道","本期来源","上期来源"]) {
    const th=node("th",label);th.scope="col";head.append(th);
  }
  const body=table.createTBody();
  for (const r of rows) {
    const row=body.insertRow(), th=node("th",nameFor(r.product));th.scope="row";
    th.append(node("span",r.label,"secondary-line"));row.append(th);
    for (const [count,negative] of [[r.current_sources,r.current_negative],[r.previous_sources,r.previous_negative]]) {
      const cell=row.insertCell();cell.append(node("span",String(count),"num"));
      if (negative!==undefined) cell.append(node("span","其中问题反馈 "+negative,"secondary-line"));
    }
  }
  return table;
}
function renderTopics() {
  const data=state.data;
  $("topic-count").textContent=data.topics.length+" 个产品主题";
  const wrap=$("topics");wrap.replaceChildren();
  if (!data.topics.length) {wrap.append(empty("当前两期尚无可归类的主题，可调整截止日或导入反馈。"));}
  else {
    const table=node("table",undefined,"radar-rows");
    const head=table.createTHead().insertRow();
    for (const label of ["主题 / 产品","本期来源","上期来源","走势"]) {
      const th=node("th",label);th.scope="col";head.append(th);
    }
    const body=table.createTBody();
    for (const t of data.topics) {
      const tr=body.insertRow();const title=node("th");title.scope="row";
      title.append(button(t.label,()=>openTopic(t),"text-button open-topic"),node("small",nameFor(t.product),"secondary-line"));tr.append(title);
      for (const [c,n] of [[t.current_sources,t.current_total],[t.previous_sources,t.previous_total]]) {
        const td=tr.insertCell();td.append(node("span",sources(c,n),"num"));
        td.append(node("small",n?(Math.round(c/n*100)+"%"):"无基线","secondary-line"));
      }
      tr.insertCell().append(node("span",t.trend,"trend"));
    }
    wrap.append(table);
  }
  $("channels").replaceChildren(channelTable(data.channel_mix));
  const method=$("method");
  method.replaceChildren();
  if (typeof data.method==="string") method.append(node("p",data.method));
  else if (data.method) for(const value of Object.values(data.method)) {
    if (typeof value==="string") method.append(node("p",value));
  }
  const un=data.unclassified || {count:0,refs:[]};
  if (un.count) addEvidence(method,un.refs,"查看未归类摘录");
  else method.append(node("p","当前窗口没有未归类摘录。"));
}
function matchingItem(t) {
  return state.followups.find((i)=>i.topic_id===t.topic_id && i.product===t.product && i.as_of===state.data.as_of && (i.window_days===undefined || i.window_days===state.data.window_days));
}
function renderAlerts() {
  const panel=$("alerts");panel.replaceChildren();
  if (!state.data.alerts.length) {panel.append(empty("当前范围内没有达到异动条件的主题。可在下方按来源核查每个主题。"));return;}
  for (const alert of state.data.alerts) {
    const row=node("article",undefined,"alert-row");
    const heading=node("div",undefined,"row-heading");
    heading.append(node("span","反馈异动","alert-label"),node("h3",nameFor(alert.product)+" · "+alert.label));
    const actions=node("div",undefined,"row-actions");
    addEvidence(actions,alert.refs,"核对触发证据");
    const t=state.data.topics.find((v)=>v.id===alert.topic_id && v.product===alert.product);
    if (t) actions.append(researchLink(t));
    const item=matchingItem(alert);
    if (item) actions.append(button("查看跟进 · "+statusNames[item.status],()=>openItem(item)));
    else {
      const add=button("登记跟进",async()=>{
        add.disabled=true;
        const g=state.generation;
        try {
          const s=state.scope;
          await api("/api/research/followups",post({left:s.left,right:s.right,as_of:state.data.as_of,
            window_days:state.data.window_days,product:alert.product,topic_id:alert.topic_id}));
          if (g===state.generation) await refresh("已登记，可在跟进事项中记录复核结果。");
        } catch (error) {if(g===state.generation) inform(error.message,true);}
        finally {add.disabled=false;}
      },"secondary");
      actions.append(add);
    }
    row.append(heading,node("p",alert.reason),actions);panel.append(row);
  }
}
function openItem(item) {
  const area=showDialog(item.label,nameFor(item.product)+" · 登记时的证据截止 "+item.as_of);
  const form=node("form");form.id="follow-edit";
  const statusLabel=node("label","处理状态","field"), select=node("select");
  for(const [key,value] of Object.entries(statusNames)) select.add(new Option(value,key,false,key===item.status));
  statusLabel.append(select);
  const noteLabel=node("label","复核说明与依据","field"), note=node("textarea");
  note.rows=3;note.maxLength=2000;note.value=item.note || "";note.placeholder="记录复测条件、核对的证据编号和结论";
  noteLabel.append(note);
  const links=node("details",undefined,"review-links");
  const linkTitle=node("summary","关联实测依据（可选）");
  const choices=node("div");
  const oldRecords=item.review_evidence || [];
  const selected=new Set([...(item.review_refs || []), ...oldRecords.map((r)=>r.reference || r.id)].filter(Boolean));
  const originalSelection=new Set(selected);
  const limitHint=node("p","","form-error");
  limitHint.setAttribute("role","status");
  links.append(linkTitle,choices,limitHint);
  function renderChoices(rows) {
    choices.replaceChildren();
    linkTitle.textContent="关联实测依据（已选 "+selected.size+" 条）";
    if (!rows.length) {
      choices.append(empty("此产品和能力暂无对应的实测。可先在研究决策台导入走查，也可直接填写说明。"));return;
    }
    for (const r of rows) {
      const ref=r.reference || r.id;
      const label=node("label",undefined,"walk-choice"),box=node("input");
      box.type="checkbox";box.value=ref;box.checked=selected.has(ref);
      box.addEventListener("change",()=>{
        if (box.checked && selected.size>=10) {
          box.checked=false;limitHint.textContent="一次复核最多关联 10 条直接实测。";return;
        }
        limitHint.textContent="";
        if (box.checked) selected.add(ref); else selected.delete(ref);
        linkTitle.textContent="关联实测依据（已选 "+selected.size+" 条）";
      });
      const caption=node("span");
      caption.append(node("small",r.date+" · "+r.version+" · "+ref),node("span",r.observation || r.excerpt));
      label.append(box,caption);choices.append(label);
    }
  }
  renderChoices(oldRecords);
  const pair=new URLSearchParams({left:state.scope.left,right:state.scope.right});
  api("/api/compare?"+pair).then((data)=>{
    if (!form.isConnected) return;
    const cap=item.topic?.capability;
    const match=data.observations.filter((r)=>r.product===item.product && r.step===cap);
    // Preserve a saved review's original excerpt if that observation has since been corrected.
    const rows=new Map([...match,...oldRecords].map((r)=>[r.reference || r.id,r]));
    renderChoices([...rows.values()].sort((a,b)=>b.date.localeCompare(a.date)));
  }).catch(()=>{
    if (form.isConnected) choices.append(empty("最新走查读取失败，已关联的依据仍然保留。"));
  });
  const save=node("button","保存跟进","primary");save.type="submit";
  const message=node("p","","form-error");message.setAttribute("role","status");
  form.append(statusLabel,noteLabel,links,save,message);area.append(form);
  form.addEventListener("submit",async(e)=>{
    e.preventDefault();
    if (select.value!=="open" && !note.value.trim()) {message.textContent="请先填写复核说明与依据。";note.focus();return;}
    save.disabled=true;message.textContent="";
    try {
      const update={status:select.value,note:note.value.trim()};
      if (selected.size!==originalSelection.size || [...selected].some((id)=>!originalSelection.has(id))) update.review_refs=[...selected];
      await api("/api/research/followups/"+encodeURIComponent(item.id),post(update,"PATCH"));
      $("radar-dialog").close();
      await refresh("已保存跟进状态和复核说明。");
    } catch(error) {message.textContent=error.message;}
    finally {save.disabled=false;}
  });
  if (oldRecords.length) {
    area.append(node("h3","复核时关联的实测（"+oldRecords.length+"）"));
    for (const r of oldRecords) {
      const entry=node("article",undefined,"quote");
      entry.append(node("small",r.date+" · "+r.version+" · "+(r.reference || r.id)),
        node("blockquote",r.observation || r.excerpt));
      area.append(entry);
    }
  }
  const records=item.evidence || [];
  area.append(node("h3","登记时的原文（"+records.length+"）"));
  renderQuotes(area,records);
}
function renderFollowups() {
  const panel=$("followups");panel.replaceChildren();
  $("follow-count").textContent=(state.before?"已显示 ":"")+state.followups.length+" 项";
  if (!state.followups.length) {panel.append(empty("尚未创建跟进事项。选中一条异动后可登记本轮证据和复核进度。"));return;}
  for (const item of state.followups) {
    const row=node("article",undefined,"follow-row"),heading=node("div",undefined,"row-heading");
    heading.append(node("span",statusNames[item.status] || item.status,"status-pill"),node("h3",nameFor(item.product)+" · "+item.label));
    row.append(heading,node("small","记录截止 "+item.as_of+" · "+(item.refs?.length || 0)+" 条证据","sub"));
    if (item.note) row.append(node("p",item.note));
    row.append(button("复核记录与原文",()=>openItem(item)));panel.append(row);
  }
  if (state.before) {
    const more=button("查看更多跟进",async()=>{
      more.disabled=true;
      const version=state.generation, {left,right}=state.scope;
      try {
        const args=new URLSearchParams({left,right,before:state.before});
        const page=await api("/api/research/followups?"+args);
        if (version!==state.generation) return;
        state.followups.push(...page.items);
        state.before=page.next_before;
        renderFollowups();renderAlerts();
      } catch(error) {if(version===state.generation) inform(error.message,true);}
      finally {more.disabled=false;}
    });
    panel.append(more);
  }
}
async function refresh(message="") {
  const version=++state.generation;
  const scope=selected();
  $("results").hidden=true;inform("正在读取反馈与跟进记录…");
  if (!scope.left || !scope.right || scope.left===scope.right) {
    inform("请选择两款不同的研究产品。",true);return;
  }
  try {
    const pair=new URLSearchParams({left:scope.left,right:scope.right});
    const [data,followups]=await Promise.all([api("/api/research/radar?"+new URLSearchParams(scope)),api("/api/research/followups?"+pair)]);
    if (version!==state.generation) return;
    state.data=data;state.scope=scope;state.followups=followups.items;state.before=followups.next_before;
    renderPeriods();renderTopics();renderFollowups();renderAlerts();
    $("results").hidden=false;inform(message);
  } catch(error) {if(version===state.generation) inform(error.message,true);}
}
function lockImport(locked) {
  state.importBusy=locked;
  for(const id of ["load-sample","choose-file","left","right","window","as-of"]) $(id).disabled=locked;
}
async function importFile(sample) {
  const file=sample?null:$("csv-file").files[0];$("csv-file").value="";
  if (state.importBusy || (!sample && !file)) return;
  lockImport(true);
  try {
    const path="/api/research/materials/feedback-sample";
    let payload=file;
    if(sample) {
      const response=await fetch(path);
      if (!response.ok) throw Error("反馈样例下载失败。");
      payload=await response.blob();
    }
    if(payload.size>1048576) throw Error("CSV 不能超过 1 MiB。");
    const result=await api("/api/research/materials/import",{method:"POST",headers:{"Content-Type":"text/csv"},body:payload});
    if(sample) {
      $("as-of").value="2026-10-17";$("window").value="7";
      $("left").value="INK";$("right").value="RILL";
    }
    await refresh((sample?"已载入两周样例，范围已切至 10 月 17 日。":"导入完成。")+"新增 "+result.inserted+" 条，已存在 "+result.unchanged+" 条。");
  } catch(error) {inform(error.message,true);}
  finally {lockImport(false);}
}
async function start() {
  $("close").addEventListener("click",()=>$("radar-dialog").close());
  $("load-sample").addEventListener("click",()=>importFile(true));
  $("choose-file").addEventListener("click",()=>$("csv-file").click());
  $("csv-file").addEventListener("change",()=>importFile(false));
  $("radar-controls").addEventListener("submit",(e)=>{e.preventDefault();refresh();});
  for(const id of ["left","right","window","as-of"]) $(id).addEventListener("change",()=>refresh());
  try {
    const {products}=await api("/api/products");
    const incoming=new URLSearchParams(location.search);
    for (const [id,defaultProduct] of [["left","INK"],["right","RILL"]]) {
      const select=$(id);
      select.replaceChildren(...products.map((p)=>new Option(p.name,p.product)));
      const value=incoming.get(id) || defaultProduct;
      if(products.some((p)=>p.product===value)) select.value=value;
    }
    if(incoming.has("as_of")) $("as-of").value=incoming.get("as_of");
    if(["7","14","30"].includes(incoming.get("window_days"))) $("window").value=incoming.get("window_days");
    await refresh();
  } catch(error) {inform(error.message,true);}
}
start();
