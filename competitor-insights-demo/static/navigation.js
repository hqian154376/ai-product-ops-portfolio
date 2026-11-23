"use strict";
(() => {
  const base = new URL('.', document.currentScript.src);
  const home = location.protocol === 'file:' ? new URL('index.html', base).href : '/';
  const entries = [
    ['research', '研究决策台', '对比能力与形成建议', home],
    ['collection', '来源采集', '抓取网页与整理资料', new URL('collection.html', base).href],
    ['radar', '反馈雷达', '看趋势与跟进复核', new URL('radar.html', base).href],
    ['walkthrough', '任务走查', '查看实际操作记录', new URL('walkthrough.html', base).href],
    ['reports', '报告库', '重开与导出历史报告', new URL('reports.html', base).href],
    ['replay', '模型回放', '浏览已保存的示例', new URL('model-replay.html', base).href],
  ];
  const current = document.body.dataset.workspacePage;
  const header = document.getElementById('workspace-header');
  header.className = 'workspace-header';
  const inner = document.createElement('div'); inner.className = 'workspace-header-inner';
  const brand = document.createElement('a'); brand.className = 'workspace-brand'; brand.href = home;
  brand.textContent = '◧ 竞品观察室'; inner.append(brand);
  const nav = document.createElement('nav'); nav.setAttribute('aria-label', '工作区导航');
  for (const [id, label, description, href] of entries) {
    const a = document.createElement('a'); a.href = href; a.dataset.nav = id;
    if (id === current) a.setAttribute('aria-current', 'page');
    const title = document.createElement('span'); title.textContent = label;
    const detail = document.createElement('small'); detail.textContent = description;
    a.append(title, detail); nav.append(a);
  }
  inner.append(nav); header.append(inner);
})();
