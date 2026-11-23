"use strict";
const $ = (id) => document.getElementById(id);
const stages = ["规划中", "开发中", "待验收", "已交付"],
  risks = ["正常", "关注", "延期"];
const labels = {
  name: "项目名称",
  owner: "负责人",
  stage: "阶段",
  progress: "完成度",
  milestone: "里程碑",
  due: "目标日期",
  risk: "风险",
  next_step: "下一步",
};
const esc = (text) =>
  String(text ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const stamp = (value) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "暂无发布";
const opt = (value, title = value, selected = false) =>
  '<option value="' +
  esc(value) +
  '" ' +
  (selected ? "selected" : "") +
  ">" +
  esc(title) +
  "</option>";
let items = [],
  active = null,
  view = "info",
  detailSeq = 0,
  refreshSeq = 0;
async function api(path, method = "GET", body) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok)
    throw Error(
      typeof data.detail === "string" ? data.detail : "请检查输入字段后再试。",
    );
  return data;
}
function showIssue(e) {
  $("notice").hidden = false;
  $("notice").textContent = e.message;
}
function sel(id, values) {
  const current = $(id).value;
  $(id).innerHTML =
    opt("", "全部") + values.map((x) => opt(x, x, x === current)).join("");
}
function render() {
  const rows = items.filter((p) =>
    ["stage", "owner", "risk"].every((k) => !$(k).value || p[k] === $(k).value),
  );
  $("summary").textContent =
    "共 " +
    items.length +
    " 个项目，其中 " +
    items.filter((p) => p.stage === "待验收").length +
    " 个待验收，" +
    items.filter((p) => p.risk === "延期").length +
    " 个延期。";
  const counts = stages.map((s) => items.filter((p) => p.stage === s).length);
  $("overview").innerHTML =
    '<div class="meter">' +
    counts
      .map((n, i) =>
        n ? '<span class="stage' + i + '" style="flex:' + n + '"></span>' : "",
      )
      .join("") +
    '</div><div class="legend">' +
    stages
      .map(
        (s, i) =>
          '<span><i class="stage' +
          i +
          '"></i>' +
          s +
          " " +
          counts[i] +
          "</span>",
      )
      .join("") +
    "</div>";
  $("row-count").textContent = rows.length + " 个项目";
  $("empty").hidden = !!rows.length;
  $("clear").hidden = !["stage", "owner", "risk"].some((k) => $(k).value);
  $("list").innerHTML = rows
    .map(
      (p) =>
        '<tr><td><button class="project" data-project="' +
        esc(p.id) +
        '">' +
        esc(p.name) +
        '</button><span class="meta">' +
        esc(p.id) +
        " · " +
        esc(p.owner) +
        "</span></td><td>" +
        esc(p.stage) +
        '<span class="progress"><progress max="100" value="' +
        esc(p.progress) +
        '"></progress>' +
        esc(p.progress) +
        "%</span></td><td>" +
        esc(p.milestone) +
        '<span class="meta">' +
        esc(p.due) +
        '</span></td><td><span class="risk ' +
        ["ok", "watch", "late"][risks.indexOf(p.risk)] +
        '">' +
        esc(p.risk) +
        "</span></td><td>" +
        esc(p.next_step) +
        '</td><td><span class="release">' +
        esc(p.production.version || "—") +
        '</span><span class="meta">' +
        stamp(p.production.released_at) +
        "</span></td></tr>",
    )
    .join("");
}
async function refresh() {
  const ticket = ++refreshSeq,
    data = await api("/api/projects");
  if (ticket !== refreshSeq) return;
  if (items.length && JSON.stringify(data) === JSON.stringify(items)) return;
  items = data;
  sel("stage", stages);
  sel("risk", risks);
  sel("owner", [...new Set(items.map((p) => p.owner))].sort());
  render();
}
function detailView() {
  for (const b of document.querySelectorAll("[data-view]"))
    b.setAttribute("aria-pressed", String(b.dataset.view === view));
  const p = active;
  $("detail-name").textContent = p.name;
  $("detail-id").textContent = p.id;
  if (view === "info") {
    $("detail-body").innerHTML =
      '<form id="edit-form" class="edit">' +
      Object.keys(labels)
        .map((key) => {
          let control;
          if (key === "stage" || key === "risk")
            control =
              '<select name="' +
              key +
              '">' +
              (key === "stage" ? stages : risks)
                .map((s) => opt(s, s, p[key] === s))
                .join("") +
              "</select>";
          else
            control =
              '<input required name="' +
              key +
              '" type="' +
              (key === "due"
                ? "date"
                : key === "progress"
                  ? "number"
                  : "text") +
              '" ' +
              (key === "progress" ? 'min="0" max="100"' : 'maxlength="160"') +
              ' value="' +
              esc(p[key]) +
              '">';
          return (
            '<label class="' +
            (key === "next_step" ? "full" : "") +
            '">' +
            labels[key] +
            control +
            "</label>"
          );
        })
        .join("") +
      '<div class="full"><button class="primary" type="submit">保存进度</button></div></form>';
    $("edit-form").onsubmit = async (e) => {
      e.preventDefault();
      const form = e.currentTarget,
        projectId = p.id,
        button = form.querySelector("button");
      button.disabled = true;
      try {
        const body = Object.fromEntries(new FormData(form));
        body.progress = Number(body.progress);
        const updated = await api(
          "/api/projects/" + encodeURIComponent(projectId),
          "PATCH",
          body,
        );
        if (active?.id === projectId) {
          active = updated;
          detailView();
          $("detail-msg").textContent = "已保存，可以在变更记录中查看。";
        }
        await refresh();
      } catch (err) {
        $("detail-msg").textContent = err.message;
      } finally {
        button.disabled = false;
      }
    };
  } else if (view === "releases") {
    $("detail-body").innerHTML =
      '<div class="envbox">' +
      ["production", "staging"]
        .map(
          (env) =>
            '<div><span class="small">' +
            (env === "production" ? "正式版本" : "预发版本") +
            "</span><h3>" +
            esc(p.environments[env].version || "尚未发布") +
            "</h3></div>",
        )
        .join("") +
      '</div><ol class="history">' +
      p.releases
        .map(
          (r) =>
            "<li><strong>" +
            esc(r.version) +
            " · " +
            (r.result === "success" ? "成功" : "失败") +
            "</strong><span>" +
            stamp(r.released_at) +
            " · " +
            (r.environment === "production" ? "正式" : "预发") +
            " · " +
            esc(r.pipeline_id) +
            "</span></li>",
        )
        .join("") +
      "</ol>" +
      (p.releases.length ? "" : '<p class="small">尚未收到发布回执。</p>');
  } else {
    $("detail-body").innerHTML =
      '<ol class="history">' +
      p.changes
        .map(
          (c) =>
            "<li><strong>" +
            esc(c.source) +
            " · " +
            stamp(c.at) +
            "</strong>" +
            Object.entries(c.changes)
              .map(
                ([key, v]) =>
                  "<span>" +
                  esc(labels[key]) +
                  "：" +
                  (v.before === null ? "" : esc(v.before) + " → ") +
                  esc(v.after) +
                  "</span>",
              )
              .join("") +
            "</li>",
        )
        .join("") +
      "</ol>";
  }
}
async function openDetail(id) {
  const ticket = ++detailSeq,
    data = await api("/api/projects/" + encodeURIComponent(id));
  if (ticket !== detailSeq) return;
  active = data;
  view = "info";
  $("detail-msg").textContent = "";
  detailView();
  $("detail-dialog").showModal();
}
$("list").onclick = (e) => {
  const b = e.target.closest("[data-project]");
  if (b) openDetail(b.dataset.project).catch(showIssue);
};
document.querySelector(".tabs").onclick = async (e) => {
  const tab = e.target.closest("[data-view]");
  if (!tab || !active) return;
  const ticket = ++detailSeq,
    id = active.id;
  try {
    const updated = await api("/api/projects/" + encodeURIComponent(id));
    if (ticket !== detailSeq || active?.id !== id) return;
    active = updated;
    view = tab.dataset.view;
    $("detail-msg").textContent = "";
    detailView();
  } catch (err) {
    $("detail-msg").textContent = err.message;
  }
};
document
  .querySelectorAll(".close")
  .forEach((b) => (b.onclick = () => b.closest("dialog").close()));
$("detail-dialog").onclose = () => {
  active = null;
  detailSeq++;
};
["stage", "owner", "risk"].forEach((k) => ($(k).onchange = render));
$("clear").onclick = () => {
  ["stage", "owner", "risk"].forEach((k) => ($(k).value = ""));
  render();
};
async function syncBadge() {
  const data = await api("/api/sync");
  $("sync-open").textContent =
    data.enabled && data.pending.length
      ? "飞书同步 · " + data.pending.length + " 项待回写"
      : "飞书同步";
}
async function syncView() {
  const data = await api("/api/sync"),
    target = $("sync-body");
  if (!data.enabled) {
    target.innerHTML =
      "<p>当前使用本地台账。要连接自己的多维表格，请按 FEISHU.md 设置应用与表格，然后重启。</p>";
    return;
  }
  target.innerHTML =
    '<p>从单表导入人工维护的项目字段。相同编号按表中值更新，正式版本由发布回执维护。</p><div class="actions"><button id="do-import">从飞书导入项目</button><button id="do-retry">重试版本回写</button></div><h3 style="margin-top:1rem">待同步项目（' +
    data.pending.length +
    '）</h3><ol class="history">' +
    data.pending
      .map(
        (row) =>
          "<li>" +
          esc(row.project_id) +
          "<span>" +
          esc(row.error || "等待回写") +
          "</span></li>",
      )
      .join("") +
    "</ol><p>" +
    esc(data.error) +
    "</p>";
  $("do-retry").disabled = !data.pending.length;
  for (const action of ["import", "retry"]) {
    $("do-" + action).onclick = async () => {
      if (
        action === "import" &&
        !confirm("按项目编号导入飞书数据并更新同名项目的人工字段？")
      )
        return;
      for (const btn of target.querySelectorAll("button")) btn.disabled = true;
      $("sync-msg").textContent = "正在连接…";
      try {
        const value = await api("/api/sync/" + action, "POST");
        $("sync-msg").textContent =
          action === "import"
            ? "已导入 " + value.count + " 个项目。"
            : "已完成本次重试，请查看待同步列表。";
        await refresh();
      } catch (err) {
        $("sync-msg").textContent = err.message;
      } finally {
        await syncView().catch(showIssue);
      }
    };
  }
}
$("sync-open").onclick = () => {
  $("sync-msg").textContent = "";
  $("sync-dialog").showModal();
  syncView().catch((err) => ($("sync-msg").textContent = err.message));
};
function refreshOverview() {
  return Promise.all([refresh(), syncBadge()]);
}
refreshOverview().catch(showIssue);
setInterval(() => {
  if (!document.hidden) refreshOverview().catch(showIssue);
}, 5000);
