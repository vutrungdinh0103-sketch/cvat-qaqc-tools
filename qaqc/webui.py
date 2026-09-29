"""Giao diện web tối giản cho ``python -m qaqc serve`` (không cần Docker).

Trang HTML được nhúng thẳng trong module này để service chỉ cần 1 file, không
phụ thuộc ``node_modules``/CDN - tiện chạy offline trên localhost. Trang gọi
đúng các endpoint mà plugin CVAT UI dùng (``/health``, ``/tasks/{id}/report``,
``/tasks/{id}/run``) nên có thể dùng để kiểm tra service trước khi build plugin.
"""

from __future__ import annotations

from typing import Final

#: Nội dung trang chủ của QA/QC service.
INDEX_HTML: Final[str] = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA/QC - CVAT</title>
<style>
  :root { --error:#d4380d; --warning:#d48806; --info:#096dd9; --border:#e5e5e5; }
  * { box-sizing: border-box; }
  body { margin:0; padding:20px; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         color:#1f1f1f; background:#fafafa; font-size:14px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8c8c8c; font-size:12px; margin-bottom:16px; }
  .card { background:#fff; border:1px solid var(--border); border-radius:8px; padding:16px;
          margin-bottom:16px; }
  .row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  label { font-weight:600; font-size:12px; color:#595959; }
  input, select { padding:6px 8px; border:1px solid #d9d9d9; border-radius:4px; font-size:14px; }
  input[type=number] { width:110px; }
  button { padding:7px 14px; border:1px solid #d9d9d9; border-radius:4px; background:#fff;
           cursor:pointer; font-size:14px; }
  button.primary { background:#1677ff; border-color:#1677ff; color:#fff; font-weight:600; }
  button:disabled { opacity:.55; cursor:progress; }
  .stats { display:flex; flex-wrap:wrap; gap:24px; }
  .stat b { display:block; font-size:22px; line-height:1.2; }
  .stat span { color:#8c8c8c; font-size:12px; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; font-size:12px; color:#595959; border-bottom:1px solid var(--border);
       padding:8px; white-space:nowrap; }
  td { border-bottom:1px solid #f5f5f5; padding:8px; vertical-align:top; }
  tr:hover td { background:#fafafa; }
  .sev { font-weight:700; text-transform:uppercase; font-size:11px; }
  .sev.error { color:var(--error); } .sev.warning { color:var(--warning); }
  .sev.info { color:var(--info); }
  code { background:#f5f5f5; padding:1px 5px; border-radius:3px; font-size:12px; }
  .msg { color:#8c8c8c; }
  .ok { color:#389e0d; font-weight:600; }
  .err { color:var(--error); white-space:pre-wrap; }
  .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px;
          background:#f0f0f0; color:#595959; }
  .pill.on { background:#f6ffed; color:#389e0d; }
  .pill.off { background:#fff1f0; color:var(--error); }
</style>
</head>
<body>
<h1>QA/QC annotation CVAT <span id="health" class="pill">đang kiểm tra...</span></h1>
<div class="sub">
  Kiểm tra kích thước / tràn khung / trùng lặp / thiếu nhãn - attribute và track.
  Service: <code id="source">...</code>
</div>
<div class="card">
  <div class="row">
    <div>
      <label for="task">Task ID</label><br>
      <input id="task" type="number" min="1" value="1">
    </div>
    <div>
      <label for="rules">File rule (bỏ trống = mặc định)</label><br>
      <input id="rules" type="text" size="28" placeholder="rules/driving_v1.yaml">
    </div>
    <div>
      <label for="only">Chỉ chạy rule (phẩy)</label><br>
      <input id="only" type="text" size="22" placeholder="invalid_size,duplicate_bbox">
    </div>
    <div>
      <label>&nbsp;</label><br>
      <button id="run" class="primary" type="button">Chạy QA/QC</button>
      <button id="reload" type="button">Tải lại kết quả</button>
      <button id="json" type="button" disabled>JSON</button>
      <button id="csv" type="button" disabled>CSV</button>
    </div>
  </div>
</div>

<div id="result" class="card" style="display:none">
  <div class="stats">
    <div class="stat"><b id="nissues">0</b><span>tổng lỗi</span></div>
    <div class="stat"><b id="nerror" style="color:var(--error)">0</b><span>error</span></div>
    <div class="stat"><b id="nwarning" style="color:var(--warning)">0</b><span>warning</span></div>
    <div class="stat"><b id="ninfo" style="color:var(--info)">0</b><span>info</span></div>
    <div class="stat"><b id="nobjects">0</b><span>object đã quét</span></div>
    <div class="stat"><b id="nframes">0</b><span>frame có annotation/tổng</span></div>
    <div class="stat"><b id="ntask">-</b><span>task</span></div>
  </div>
  <div class="msg" id="meta" style="margin-top:12px"></div>
  <div class="msg" id="warnings" style="margin-top:6px"></div>
</div>

<div id="filters" class="card" style="display:none">
  <div class="row">
    <div>
      <label for="severity">Mức độ</label><br>
      <select id="severity">
        <option value="">Tất cả</option>
        <option value="error">error</option>
        <option value="warning">warning</option>
        <option value="info">info</option>
      </select>
    </div>
    <div>
      <label for="rule">Rule</label><br>
      <select id="rule"><option value="">Tất cả</option></select>
    </div>
    <div>
      <label for="search">Tìm trong mô tả</label><br>
      <input id="search" type="text" size="26" placeholder="ví dụ: thiếu attribute">
    </div>
    <div><label>&nbsp;</label><br><span class="pill" id="shown">0 lỗi</span></div>
  </div>
</div>

<div class="card" id="tablecard" style="display:none; padding:0">
  <table>
    <thead>
      <tr>
        <th>Mức độ</th><th>Rule</th><th>Frame</th><th>Job</th><th>Nhãn</th><th>Mô tả</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</div>

<div class="card" id="errorbox" style="display:none">
  <div class="err" id="errortext"></div>
</div>
<script>
"use strict";
var report = null;
function $(id) { return document.getElementById(id); }

function currentQuery(extra) {
  var params = new URLSearchParams();
  var rules = $("rules").value.trim();
  var only = $("only").value.trim();
  if (rules) { params.set("rules", rules); }
  if (only) { params.set("only", only); }
  if (extra) { Object.keys(extra).forEach(function (key) { params.set(key, extra[key]); }); }
  var query = params.toString();
  return query ? "?" + query : "";
}

function setBusy(busy) {
  $("run").disabled = busy;
  $("reload").disabled = busy;
  if (busy) { $("health").textContent = "đang chạy QA/QC..."; $("health").className = "pill"; }
}

function showError(text) {
  $("errorbox").style.display = "block";
  $("errortext").textContent = text;
}

function taskId() {
  var value = Number($("task").value || 0);
  return value > 0 ? value : null;
}

async function request(path, method, extra) {
  var id = taskId();
  if (!id) { showError("Hãy nhập Task ID (số nguyên dương)."); return null; }
  setBusy(true);
  $("errorbox").style.display = "none";
  try {
    var response = await fetch("/tasks/" + id + path + currentQuery(extra), { method: method });
    if (!response.ok) {
      throw new Error("Service trả về HTTP " + response.status + ": " + (await response.text()));
    }
    return await response.json();
  } catch (error) {
    showError("Không lấy được kết quả QA/QC: " + (error && error.message ? error.message : error));
    return null;
  } finally {
    setBusy(false);
  }
}

function render(data) {
  report = data;
  $("result").style.display = "block";
  $("filters").style.display = "block";
  $("tablecard").style.display = "block";
  $("json").disabled = false;
  $("csv").disabled = false;

  var issues = data.issues || [];
  var counts = data.counts_by_severity || {};
  $("nissues").textContent = issues.length;
  $("nerror").textContent = counts.error || 0;
  $("nwarning").textContent = counts.warning || 0;
  $("ninfo").textContent = counts.info || 0;
  $("nobjects").textContent = data.objects_scanned;
  $("nframes").textContent = data.frames_total
    ? data.frames_scanned + "/" + data.frames_total
    : data.frames_scanned;
  $("ntask").textContent = data.task_id + (data.task_name ? " - " + data.task_name : "");
  var duration = data.duration_seconds ? " (" + data.duration_seconds.toFixed(2) + "s)" : "";
  var rulesInfo = "rules: " + data.rules_name + " (hash " +
    String(data.rules_hash || "").slice(0, 12) + ")";
  $("meta").textContent = "Quét lúc " + data.generated_at + duration + " | " + rulesInfo +
    " | rule bật: " + Object.keys(data.counts_by_rule || {}).length;
  var warningLines = (data.warnings || []).map(function (item) { return "! " + item; });
  $("warnings").textContent = warningLines.join(" | ");

  var ruleSelect = $("rule");
  ruleSelect.innerHTML = '<option value="">Tất cả</option>';
  Object.keys(data.counts_by_rule || {}).sort().forEach(function (rule) {
    var option = document.createElement("option");
    option.value = rule;
    option.textContent = rule + " (" + data.counts_by_rule[rule] + ")";
    ruleSelect.appendChild(option);
  });
  renderRows();
}

function renderRows() {
  var body = $("rows");
  body.innerHTML = "";
  if (!report) { return; }
  var severity = $("severity").value;
  var rule = $("rule").value;
  var needle = $("search").value.trim().toLowerCase();
  var rows = (report.issues || []).filter(function (issue) {
    if (severity && issue.severity !== severity) { return false; }
    if (rule && issue.rule_id !== rule) { return false; }
    if (needle && String(issue.message).toLowerCase().indexOf(needle) < 0) { return false; }
    return true;
  });
  $("shown").textContent = rows.length + " lỗi";
  $("shown").className = "pill" + (rows.length ? " off" : " on");
  if (!rows.length) {
    var empty = document.createElement("tr");
    var cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "Không có lỗi nào khớp bộ lọc.";
    cell.className = "msg";
    empty.appendChild(cell);
    body.appendChild(empty);
    return;
  }
  rows.forEach(function (issue) {
    var tr = document.createElement("tr");
    [["sev " + issue.severity, issue.severity], ["", issue.rule_id], ["", issue.frame],
     ["", issue.job_id === null ? "-" : issue.job_id], ["", issue.label || "-"],
     ["", issue.message]].forEach(function (pair) {
      var td = document.createElement("td");
      if (pair[0]) { td.className = pair[0]; }
      td.textContent = String(pair[1]);
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

async function runQAQC() {
  var data = await request("/run", "POST", null);
  if (data) { render(data); }
}

async function reloadReport() {
  var data = await request("/report", "GET", { refresh: "true" });
  if (data) { render(data); }
}

function download(kind) {
  var id = taskId();
  if (!id) { showError("Hãy nhập Task ID."); return; }
  var suffix = kind === "csv" ? ".csv" : "";
  window.open("/tasks/" + id + "/report" + suffix + currentQuery(null), "_blank");
}

async function checkHealth() {
  try {
    var response = await fetch("/health");
    var data = await response.json();
    $("health").textContent = "service OK (v" + data.version + ")";
    $("health").className = "pill on";
    $("source").textContent = data.data_source;
  } catch (error) {
    $("health").textContent = "service không phản hồi";
    $("health").className = "pill off";
  }
}

$("run").addEventListener("click", runQAQC);
$("reload").addEventListener("click", reloadReport);
$("json").addEventListener("click", function () { download("json"); });
$("csv").addEventListener("click", function () { download("csv"); });
["severity", "rule"].forEach(function (id) { $(id).addEventListener("change", renderRows); });
$("search").addEventListener("input", renderRows);
$("task").addEventListener("keydown", function (event) {
  if (event.key === "Enter") { runQAQC(); }
});

checkHealth();
if ($("rules").value === "" && window.location.search.indexOf("rules=") >= 0) {
  $("rules").value = new URLSearchParams(window.location.search).get("rules");
}
if ($("task").value) { runQAQC(); }
</script>


</body>
</html>
"""
