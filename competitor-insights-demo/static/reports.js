"use strict";
(() => {
  const el = id => document.getElementById(id);
  let before = null;
  async function api(path) {
    const r = await fetch(path); if (!r.ok) throw Error('报告暂时读取失败，请刷新重试。');
    return r.json();
  }
  function render(reports, target, research) {
    for (const r of reports) {
      const link = document.createElement('a'); link.className = 'past';
      link.href = (research ? '/?report=' : '/static/walkthrough.html?report=') + r.id;
      link.textContent = '#' + r.id + '　' + r.title;
      const time = document.createElement('small');
      time.textContent = r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : '历史快照';
      link.append(time); el(target).append(link);
    }
  }
  async function loadResearch() {
    el('more').disabled = true;
    try {
      const d = await api('/api/research/reports?limit=30' + (before ? '&before=' + before : ''));
      render(d.reports, 'research-reports', true);
      if (!d.reports.length && !before) el('research-reports').textContent = '还没有研究报告。到研究决策台生成一份后，会保存在这里。';
      before = d.next_before; el('more').hidden = before == null;
    } catch (e) { el('notice').textContent = e.message; }
    finally { el('more').disabled = false; }
  }
  el('more').onclick = loadResearch;
  loadResearch();
  api('/api/reports').then(d => {
    render(d.reports, 'walkthrough-reports', false);
    if (!d.reports.length) el('walkthrough-reports').textContent = '还没有任务走查报告。';
  }).catch(e => { el('notice').textContent = e.message; });
})();
