"use strict";
const el=id=>document.getElementById(id);
const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
let busy=false;
async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const d=await r.json();if(!r.ok)throw Error(d.detail||'请求失败');return d;}
function tell(text){el('notice').textContent=text;}
async function action(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();}catch(e){tell(e.message);}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
async function render(){
 const data=await api('/api/collection/snapshots');el('snapshots').replaceChildren();
 for(const s of data.items){
  const box=node('section');box.className='panel';box.append(node('h3',`#${s.id} · ${s.title}`),node('p',`采集于 ${new Date(s.fetched_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间） · 正文 ${s.body.length} 字 · SHA256 ${s.digest.slice(0,12)} · ${s.previous_id?'前一快照 #'+s.previous_id:'首次采集'}`));
  const link=node('a','打开原始网页');link.href=s.url;link.target='_blank';link.rel='noopener';box.append(link);
  const details=node('details');details.append(node('summary','查看完整正文'));const pre=node('pre',s.body);pre.style.whiteSpace='pre-wrap';details.append(pre);box.append(details);
  const input=node('textarea');input.value=s.body.slice(0,800);input.rows=7;input.maxLength=1200;input.style.width='100%';input.setAttribute('aria-label','确认入库的原文摘录');box.append(input);
  const b=node('button',JSON.parse(s.evidence_ids).length?'已入库 · 再确认相同摘录不会重复':'确认摘录并加入证据库');b.className='secondary';b.onclick=()=>action(async()=>{const r=await api(`/api/collection/snapshots/${s.id}/approve`,{excerpt:input.value});tell('已入库：'+r.id+'。现在可在研究台选择对应产品。');await render();});box.append(b);el('snapshots').append(box);
 }
}
(async()=>{try{const d=await api('/api/collection/sources');const batch=node('button','一键采集全部来源');batch.className='primary';batch.onclick=()=>action(async()=>{let count=0;for(const source of d.sources){tell('正在采集 '+source.name+'…');await api(`/api/collection/sources/${source.id}/collect`,{});count++;}tell('已完成 '+count+' 个来源，请核对摘录后入库。');await render();});el('sources').append(batch);for(const s of d.sources){const b=node('button','采集 '+s.name+' 官方 PNG 文档');b.className='secondary';b.onclick=()=>action(async()=>{tell('正在读取公开网页…');const r=await api(`/api/collection/sources/${s.id}/collect`,{});tell(r.unchanged?'正文未变化，复用已有快照 #'+r.id:'已采集新快照 #'+r.id+'，请核对正文后入库。');await render();});el('sources').append(b);}await render();}catch(e){tell(e.message);}})();

el('agent-collect').onclick=()=>action(async()=>{tell('采集 Agent 正在查看来源并调用抓取工具…');const r=await api('/api/collection/agent-run',{question:'查看已登记来源，采集 Krita 和 GIMP 的 PNG 官方文档，各采集一次，遇到失败如实记录。'});el('agent-trace').textContent=r.trace.map(t=>t.tool+' '+JSON.stringify(t.arguments)+' → '+JSON.stringify(t.result)).join('\n')+'\n'+r.summary;tell('Agent 采集已完成，请核对正文并确认摘录。');await render();});
