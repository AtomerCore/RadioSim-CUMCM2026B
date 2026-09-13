/* 模拟器主界面逻辑：轮询状态、测试控制、指令与反馈、日志、设置 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let lastSeq = 0;          // 已拉取的事件序号
let sessionCode = null;   // 当前会话（变更时重置本地缓存）
let cmdRows = [];         // 指令与反馈行缓存
let pollTimer = null;
let configCache = null;

/* ---------------------- 工具 ---------------------- */
function fmtSec(s, digits = 0) {
  if (s == null) return "—";
  return s.toFixed ? s.toFixed(digits) : s;
}
function fmtClock(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}
function fmtMMSS(sec) {
  if (sec == null) return "—";
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}
function fmtPos(p) {
  if (!p) return "—";
  return "(" + (+p[0]).toFixed(1) + ", " + (+p[1]).toFixed(1) + ")";
}

async function apiPost(path, payload) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  return r.json();
}

/* ---------------------- 确认弹窗 ---------------------- */
let modalHandler = null;
function confirmModal(title, body, onOk, opts = {}) {
  $("#modal-title").textContent = title;
  $("#modal-body").textContent = body;
  const ok = $("#modal-ok");
  ok.textContent = opts.double ? "确认" : "确定";
  ok.classList.toggle("btn-danger", opts.danger !== false);
  $("#modal-cancel").classList.remove("hidden");
  modalHandler = { onOk, double: !!opts.double, stage: 1 };
  $("#modal-mask").classList.remove("hidden");
}
function closeModal() {
  $("#modal-mask").classList.add("hidden");
  modalHandler = null;
}
$("#modal-ok").addEventListener("click", () => {
  if (!modalHandler) return;
  if (modalHandler.double && modalHandler.stage === 1) {
    modalHandler.stage = 2;
    $("#modal-ok").textContent = "再次点击确认";
    return;
  }
  const fn = modalHandler.onOk;
  closeModal();
  if (fn) fn();
});
$("#modal-cancel").addEventListener("click", closeModal);

/* ---------------------- 标签页 ---------------------- */
$$(".tab").forEach((tab) => tab.addEventListener("click", () => {
  $$(".tab").forEach((t) => t.classList.remove("active"));
  $$(".tabpane").forEach((p) => p.classList.remove("active"));
  tab.classList.add("active");
  $("#tab-" + tab.dataset.tab).classList.add("active");
  if (tab.dataset.tab === "viz") Viz && Viz.resize();
  if (tab.dataset.tab === "batch") pollBatch();
}));

/* ---------------------- 轮询 ---------------------- */
async function poll() {
  const reveal = $("#vc-truth").checked ? 1 : 0;
  const after = sessionCode === null ? -1 : lastSeq;
  let st;
  try {
    const r = await fetch("/api/state?after=" + after + "&reveal=" + reveal);
    st = await r.json();
  } catch (e) {
    $("#pill-interface").textContent = "连接中断";
    $("#pill-interface").className = "pill bad";
    return;
  }
  if (!st || !st.ok) return;

  $("#hdr-info").textContent =
    "接口 127.0.0.1:" + st.port +
    " · 正式次数剩余 P3:" + st.attempts.p3_remaining + " / P4:" + st.attempts.p4_remaining;

  const snap = st.session;
  const code = snap ? snap.case_code : null;
  if (code !== sessionCode) {
    sessionCode = code;
    lastSeq = 0;
    cmdRows = [];
    Viz && Viz.reset();
    $("#cmd-tbody").innerHTML = "";
    $("#cmd-empty").classList.toggle("hidden", false);
  }
  if (snap && st.events.length) {
    for (const ev of st.events) if (ev.kind !== "session") addCmdRow(ev);
    cmdRows = cmdRows.slice(-(configCache ? configCache.display_count : 5000));
    lastSeq = Math.max(lastSeq, st.last_seq);
    renderCmdTable();          // 批量刷新一次，避免逐条全量重建表格
  } else if (snap) {
    lastSeq = Math.max(lastSeq, st.last_seq);
  }
  // 会话阶段事件也要推进 last_seq 并喂给可视化
  if (snap && st.events.length) Viz && Viz.push(st, st.events);

  renderState(st, snap);
  renderLogs(st.logs);

  // 批量跑测：批量进行中或停留在批量页时轮询
  const batchActive = !!(st.batch && st.batch.active);
  if (batchActive || $("#tab-batch").classList.contains("active")) pollBatch();

  const active = snap && snap.phase !== "ended";
  schedulePoll(active || batchActive ? 250 : 600);
}
function schedulePoll(ms) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, ms);
}

/* ---------------------- 指令与反馈表 ---------------------- */
function addCmdRow(ev) {
  let cmd = (ev.path || "").replace("/", "");
  let result = "", cls = "res-dim";
  if (ev.kind === "enter") { cmd = "enter"; result = "accepted（剩余 " + ev.remaining_real_s + "s）"; cls = "res-ok"; }
  else if (ev.kind === "exit") { cmd = "exit"; result = "user_exit"; cls = "res-ok"; }
  else if (ev.kind === "measure") {
    if (ev.result === "direction") { result = "direction  " + ev.svd.toFixed(2) + "°"; cls = "res-ok"; }
    else if (ev.result === "near") { result = "near（距离过近）"; cls = "res-warn"; }
    else result = "no_signal";
  } else if (ev.kind === "clear") {
    if (ev.result === "success") { result = "success（已清除）"; cls = "res-ok"; }
    else result = "no_target_in_range";
  } else if (ev.kind === "reject") {
    cmd = (ev.path || "").replace("/", "") || "—";
    result = (ev.http_status ? "HTTP " + ev.http_status + "  " : "") +
      (ev.detail || "rejected");
    cls = "res-bad";
  }
  cmdRows.push({
    t: ev.t_ms, vt: ev.vt_s, cmd, rid: ev.request_id || "—",
    pos: ev.pos || null, ch: ev.channel != null ? ev.channel : "",
    result, cls,
  });
}
function renderCmdTable() {
  const disp = configCache ? configCache.display_count : 1000;
  const rows = cmdRows.slice(-disp);
  const tbody = $("#cmd-tbody");
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td class="plain">${fmtClock(r.t)}</td>
      <td>${(+r.vt).toFixed(r.vt % 1 ? 2 : 0)}</td>
      <td class="plain">${r.cmd}</td>
      <td>${r.rid}</td>
      <td>${r.pos ? fmtPos(r.pos) : "—"}</td>
      <td>${r.ch}</td>
      <td class="plain ${r.cls}">${r.result}</td>
    </tr>`).join("");
  $("#cmd-empty").classList.toggle("hidden", rows.length > 0);
  $("#cmd-count").textContent = rows.length ? "（最近 " + rows.length + " 条，全部记录见日志）" : "";
  const wrap = tbody.closest(".table-wrap");
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

/* ---------------------- 状态渲染 ---------------------- */
function renderState(st, snap) {
  // 顶栏
  const pillI = $("#pill-interface"), pillS = $("#pill-session");
  if (!snap) {
    pillI.textContent = "接口未开放"; pillI.className = "pill";
    pillS.textContent = "空闲"; pillS.className = "pill pill-dim";
  } else if (snap.phase === "countdown" || snap.phase === "preparing") {
    pillI.textContent = "倒计时中…"; pillI.className = "pill warn";
    pillS.textContent = snap.case_code; pillS.className = "pill pill-dim";
  } else if (snap.phase === "ended") {
    pillI.textContent = "测试已结束"; pillI.className = "pill bad";
    pillS.textContent = snap.case_code; pillS.className = "pill pill-dim";
  } else {
    pillI.textContent = "机器狗接口已就绪"; pillI.className = "pill on";
    pillS.textContent = snap.case_code; pillS.className = "pill pill-dim";
  }

  // 正式次数（每次轮询整体重算 disabled，避免一局结束后按钮停留在禁用状态）
  const sessionActive = !!(snap && snap.phase !== "ended");
  const batchActive = !!(st.batch && st.batch.active);
  $$(".card[data-mode='formal']").forEach((card) => {
    const p = card.dataset.problem;
    const left = p === "3" ? st.attempts.p3_remaining : st.attempts.p4_remaining;
    card.querySelector(".attempts-left").textContent = left + " / " + st.formal_attempts_limit;
    card.querySelector(".btn-start").disabled = left <= 0 || sessionActive || batchActive;
  });
  $$(".card[data-mode='practice'] .btn-start").forEach((b) => { b.disabled = sessionActive || batchActive; });

  // 批量跑测顶栏提示
  const pb = $("#pill-batch");
  if (batchActive) {
    pb.textContent = "批量跑测中 " + st.batch.done + "/" + st.batch.total;
    pb.classList.remove("hidden");
  } else pb.classList.add("hidden");

  // 面板显隐
  const active = snap && snap.phase !== "ended";
  $("#active-panel").classList.toggle("hidden", !snap);
  $("#btn-abort").disabled = !active;
  const endPanel = $("#end-panel");
  if (snap && snap.phase === "ended") {
    endPanel.classList.remove("hidden");
    renderEndPanel(snap);
  } else endPanel.classList.add("hidden");
  if (!snap) { $("#active-panel").classList.add("hidden"); return; }

  // 面板内容
  $("#ap-mode").textContent = snap.mode === "formal" ? "正式" : "演练";
  $("#ap-mode").className = "tag " + (snap.mode === "formal" ? "tag-f" : "tag-u");
  $("#ap-name").textContent = "问题 " + snap.problem +
    (snap.mode === "formal" ? " 正式测试" : " 演练测试");
  $("#ap-code").textContent = snap.case_code;

  $("#tm-countdown").textContent = snap.phase === "countdown"
    ? fmtSec(snap.countdown_remaining_s, 1) + " s" : "—";
  $("#tm-window").textContent = fmtMMSS(snap.window_remaining_s);
  $("#tm-program").textContent = snap.program_remaining_s == null ? "（未 /enter）" : fmtMMSS(snap.program_remaining_s);
  $("#tm-virtual").textContent = fmtSec(snap.virtual_time_s, snap.virtual_time_s % 1 ? 2 : 0) + " s";

  $("#st-interface").textContent = snap.interface_ready ? "已就绪" :
    (snap.phase === "countdown" ? "倒计时中" : "未开放");
  $("#st-pos").textContent = fmtPos([snap.robot.x, snap.robot.y]);
  $("#st-channel").textContent = snap.robot.channel;
  $("#st-measures").textContent = snap.stats.measures;
  $("#st-clears").textContent = snap.stats.clear_success + " / " +
    (snap.stats.clear_success + snap.stats.clear_fail);
  const showTotal = snap.truth_visible;
  $("#st-cleared-wrap").classList.toggle("hidden", !showTotal);
  if (showTotal) {
    const s = snap.sources || [];
    $("#st-cleared").textContent = snap.stats.cleared + " / " + s.length +
      "（全向 " + s.filter((x) => x.kind === "omni").length +
      " · 定向 " + s.filter((x) => x.kind === "directional").length + "）";
  }

  Viz && Viz.setSnapshot(snap);
}

const REASON_TEXT = {
  user_exit: "机器狗主动退出（user_exit）",
  user_abort: "手工中止测试",
  window_timeout: "25 分钟测试窗口超时",
  program_timeout: "程序运行超时",
  virtual_timeout: "虚拟世界时间超时（100 小时）",
};

function renderEndPanel(snap) {
  const s = snap.summary || {};
  const el = $("#end-panel");
  const truth = snap.truth_visible;
  let head = `<div class="panel-head"><h3 style="margin:0">测试结束：${snap.case_code}（问题 ${snap.problem} ${snap.mode === "formal" ? "正式" : "演练"}）</h3></div>`;
  let body = `<p class="dim">${REASON_TEXT[s.reason] || s.reason || ""}</p>`;
  const items = [];
  items.push(["已清除干扰源", s.cleared != null ? s.cleared : "—"]);
  if (truth && s.total != null) {
    items.push(["干扰源总数", `${s.total}（全向 ${s.omni_total} · 定向 ${s.directional_total}）`]);
    if (snap.mode === "practice")
      body += `<p class="res-ok">演练测试真值：共 ${s.total} 个干扰源，全向 ${s.omni_total} 个，定向 ${s.directional_total} 个。</p>`;
  }
  items.push(["被清除比例", s.total && s.cleared != null ? (100 * s.cleared / s.total).toFixed(1) + "%" : "—"]);
  items.push(["检测次数", s.measures != null ? s.measures : "—"]);
  items.push(["清除成功 / 尝试", `${s.clear_success ?? "—"} / ${(s.clear_success ?? 0) + (s.clear_fail ?? 0)}`]);
  items.push(["总虚拟时间", s.virtual_total_s != null ? fmtSec(s.virtual_total_s, 1) + " s" : "—"]);
  items.push(["平均定位清除时间", s.avg_clear_s != null ? fmtSec(s.avg_clear_s, 1) + " s/个" : "—"]);
  items.push(["程序运行时间", s.program_run_s != null ? fmtSec(s.program_run_s, 1) + " s" : "—"]);
  items.push(["累计移动距离", s.total_move_m != null ? fmtSec(s.total_move_m, 0) + " m" : "—"]);
  if (truth && s.cleared_channels) items.push(["已清除频道", s.cleared_channels.join(", ") || "无"]);
  body += `<div class="end-grid">` + items.map(([k, v]) =>
    `<div class="timer"><label>${k}</label><b>${v}</b></div>`).join("") + `</div>`;
  body += `<p class="dim small" style="margin-top:10px">完整日志已保存，可在“测试日志”页导出。</p>`;
  el.innerHTML = head + body;
}

/* ---------------------- 日志页 ---------------------- */
let lastLogCodes = null;
function renderLogs(logs) {
  const codes = logs.map((l) => l.case_code).join("|");
  if (codes === lastLogCodes) return;
  lastLogCodes = codes;
  const tbody = $("#logs-tbody");
  tbody.innerHTML = logs.map((l) => {
    const s = l.summary || {};
    return `<tr>
      <td class="mono">${l.case_code}</td>
      <td class="plain">问题 ${l.problem}</td>
      <td class="plain">${l.mode === "formal" ? "正式" : "演练"}</td>
      <td>${fmtClock(l.ended_ms)}</td>
      <td class="plain">${REASON_TEXT[s.reason] || s.reason || "—"}</td>
      <td>${s.cleared ?? "—"} / ${s.total ?? "—"}</td>
      <td>${s.virtual_total_s != null ? (+s.virtual_total_s).toFixed(1) : "—"}</td>
      <td><div class="btn-row">
        <button class="btn" onclick="location.href='/api/log/${l.case_code}'">导出</button>
        <button class="btn" onclick="window.__deleteLog('${l.case_code}')">删除</button>
      </div></td>
    </tr>`;
  }).join("");
  $("#logs-empty").classList.toggle("hidden", logs.length > 0);
  $("#ra-p3").textContent = window.__attemptsCache ? window.__attemptsCache.p3_remaining : "—";
}
$("#btn-refresh-logs").addEventListener("click", () => { lastLogCodes = null; poll(); });

window.__deleteLog = (code) => {
  confirmModal("删除日志",
    `确定删除测试日志 ${code} 吗？\n对应的 JSON 文件将被永久删除。`,
    async () => {
      await apiPost("/api/log_delete", { case_code: code });
      lastLogCodes = null;
      poll();
    });
};
$("#btn-clear-logs").addEventListener("click", () => {
  confirmModal("清空全部日志",
    "将删除全部测试日志文件（含正式测试日志），且不可恢复。\n确定继续？",
    async () => {
      await apiPost("/api/logs_clear", {});
      lastLogCodes = null;
      poll();
    }, { double: true });
});
$("#btn-open-logs-folder").addEventListener("click", () => {
  apiPost("/api/open_logs_folder", {});
});

/* ---------------------- 开始 / 中止 ---------------------- */
$$(".btn-start").forEach((btn) => btn.addEventListener("click", () => {
  const card = btn.closest(".card");
  const problem = +card.dataset.problem, mode = card.dataset.mode;
  const name = `问题 ${problem} ${mode === "formal" ? "正式" : "演练"}测试`;
  const doStart = () => apiPost("/api/start", { problem, mode }).then((r) => {
    if (!r.ok) { confirmModal("无法开始", r.error || "启动失败", null, { danger: false }); }
    else { sessionCode = null; lastSeq = 0; poll(); }
  });
  if (mode === "formal") {
    const leftSel = problem === 3 ? "p3_remaining" : "p4_remaining";
    confirmModal("开始正式测试",
      `即将开始「${name}」。\n启动后将占用一次正式测试机会（中止同样占用）。\n确认继续？`,
      doStart);
  } else doStart();
}));

$("#btn-abort").addEventListener("click", () => {
  confirmModal("中止测试",
    "中止测试将立即结束本局测试。\n若是正式测试，仍会占用一次测试机会。",
    () => apiPost("/api/abort", {}).then(() => poll()),
    { double: true });
});

/* ---------------------- 设置页 ---------------------- */
const CUSTOM_FIELDS = ["kind", "channel", "x", "y", "recv_radius", "direction_deg"];

async function loadConfig() {
  const r = await fetch("/api/config");
  const data = await r.json();
  if (!data.ok) return;
  configCache = data.config;
  fillSettings(data.config);
  $("#settings-lock").classList.toggle("hidden", !data.active);
}
function fillSettings(c) {
  $("#cf-port").value = c.port;
  $("#cf-display").value = c.display_count;
  $("#cf-arena").value = c.arena_radius;
  $("#cf-nmin").value = c.source_count_min;
  $("#cf-nmax").value = c.source_count_max;
  $("#cf-chmin").value = c.channel_min;
  $("#cf-chmax").value = c.channel_max;
  $("#cf-rrmin").value = c.recv_radius_min;
  $("#cf-rrmax").value = c.recv_radius_max;
  $("#cf-err").value = c.svd_error_deg;
  $("#cf-seed").value = c.random_seed == null ? "" : c.random_seed;
  $("#cf-speed").value = c.move_speed;
  $("#cf-measure").value = c.measure_time;
  $("#cf-switch").value = c.switch_time;
  $("#cf-clearfail").value = c.clear_fail_time;
  $("#cf-clearok").value = c.clear_success_time;
  $("#cf-near").value = c.near_distance;
  $("#cf-clearr").value = c.clear_radius;
  $("#cf-init-ch").value = c.initial_channel;
  $("#cf-countdown").value = c.countdown_s;
  $("#cf-window").value = c.window_s;
  $("#cf-maxreal").value = c.max_real_s;
  $("#cf-maxvirt").value = c.max_virtual_s;
  $("#cf-attempts").value = c.formal_attempts;
  $("#cf-reveal").value = String(!!c.reveal_formal_truth);
  $("#cf-custom-enabled").checked = !!c.custom_case_enabled;
  renderCustomTable(c.custom_sources || []);
}
function renderCustomTable(sources) {
  const tbody = $("#custom-tbody");
  tbody.innerHTML = sources.map((s, i) => `
    <tr data-i="${i}">
      <td class="plain"><select data-f="kind">
        <option value="omni" ${s.kind !== "directional" ? "selected" : ""}>全向</option>
        <option value="directional" ${s.kind === "directional" ? "selected" : ""}>定向</option>
      </select></td>
      <td><input type="number" data-f="channel" value="${s.channel}" min="1" max="20"></td>
      <td><input type="number" data-f="x" value="${s.x}" step="1"></td>
      <td><input type="number" data-f="y" value="${s.y}" step="1"></td>
      <td><input type="number" data-f="recv_radius" value="${s.recv_radius}" step="10"></td>
      <td><input type="number" data-f="direction_deg" value="${s.direction_deg == null ? 0 : s.direction_deg}" step="1" ${s.kind !== "directional" ? "disabled" : ""}></td>
      <td><button class="btn btn-del-row" title="删除">×</button></td>
    </tr>`).join("");
  tbody.querySelectorAll("select[data-f='kind']").forEach((sel) => {
    sel.addEventListener("change", () => {
      const row = sel.closest("tr");
      row.querySelector("input[data-f='direction_deg']").disabled = sel.value !== "directional";
    });
  });
  tbody.querySelectorAll(".btn-del-row").forEach((b) => b.addEventListener("click", () => {
    b.closest("tr").remove();
  }));
}
function collectCustomSources() {
  const rows = Array.from($("#custom-tbody").querySelectorAll("tr"));
  return rows.map((row) => {
    const get = (f) => row.querySelector(`[data-f='${f}']`).value;
    const kind = get("kind");
    return {
      kind,
      channel: parseInt(get("channel"), 10),
      x: parseFloat(get("x")),
      y: parseFloat(get("y")),
      recv_radius: parseFloat(get("recv_radius")),
      direction_deg: kind === "directional" ? parseFloat(get("direction_deg")) : null,
    };
  });
}
$("#btn-add-source").addEventListener("click", () => {
  const used = new Set(collectCustomSources().map((s) => s.channel));
  let ch = 1; while (used.has(ch) && ch <= 20) ch++;
  const cur = collectCustomSources();
  cur.push({ kind: "omni", channel: ch, x: 0, y: 0, recv_radius: 1200, direction_deg: null });
  renderCustomTable(cur);
});
$("#btn-src-case1").addEventListener("click", () => {
  renderCustomTable([
    { kind: "omni", channel: 7, x: 600, y: 200, recv_radius: 1400, direction_deg: null },
  ]);
});

function collectSettings() {
  const seedStr = $("#cf-seed").value.trim();
  return {
    port: parseInt($("#cf-port").value, 10),
    display_count: parseInt($("#cf-display").value, 10),
    arena_radius: parseFloat($("#cf-arena").value),
    source_count_min: parseInt($("#cf-nmin").value, 10),
    source_count_max: parseInt($("#cf-nmax").value, 10),
    channel_min: parseInt($("#cf-chmin").value, 10),
    channel_max: parseInt($("#cf-chmax").value, 10),
    recv_radius_min: parseFloat($("#cf-rrmin").value),
    recv_radius_max: parseFloat($("#cf-rrmax").value),
    svd_error_deg: parseFloat($("#cf-err").value),
    random_seed: seedStr === "" ? null : (isNaN(+seedStr) ? seedStr : parseInt(seedStr, 10)),
    move_speed: parseFloat($("#cf-speed").value),
    measure_time: parseFloat($("#cf-measure").value),
    switch_time: parseFloat($("#cf-switch").value),
    clear_fail_time: parseFloat($("#cf-clearfail").value),
    clear_success_time: parseFloat($("#cf-clearok").value),
    near_distance: parseFloat($("#cf-near").value),
    clear_radius: parseFloat($("#cf-clearr").value),
    initial_channel: parseInt($("#cf-init-ch").value, 10),
    countdown_s: parseFloat($("#cf-countdown").value),
    window_s: parseFloat($("#cf-window").value),
    max_real_s: parseFloat($("#cf-maxreal").value),
    max_virtual_s: parseFloat($("#cf-maxvirt").value),
    formal_attempts: parseInt($("#cf-attempts").value, 10),
    reveal_formal_truth: $("#cf-reveal").value === "true",
    custom_case_enabled: $("#cf-custom-enabled").checked,
    custom_sources: collectCustomSources(),
  };
}

$("#btn-save-settings").addEventListener("click", async () => {
  const msg = $("#settings-msg");
  msg.textContent = "保存中…";
  const r = await apiPost("/api/config", { config: collectSettings() });
  if (!r.ok) { msg.textContent = ""; confirmModal("保存失败", r.error || "未知错误", null, { danger: false }); return; }
  if (r.port_changed) {
    msg.textContent = "端口已切换，即将跳转…";
    setTimeout(() => { location.href = "http://127.0.0.1:" + r.new_port + "/"; }, 1200);
    return;
  }
  msg.textContent = "已保存。";
  await loadConfig();
  setTimeout(() => { msg.textContent = ""; }, 2500);
});
$("#btn-revert-settings").addEventListener("click", () => loadConfig());

$("#btn-reset-p3").addEventListener("click", () =>
  apiPost("/api/reset_attempts", { problem: 3 }).then(() => poll()));
$("#btn-reset-p4").addEventListener("click", () =>
  apiPost("/api/reset_attempts", { problem: 4 }).then(() => poll()));

/* ---------------------- 批量跑测 ---------------------- */
const BATCH_METRIC_KEYS = ["cleared", "cleared_ratio", "measures",
  "clear_success", "clear_fail", "virtual_total_s", "avg_clear_s",
  "program_run_s", "total_move_m"];
const BATCH_PHASE_TEXT = {
  starting: "准备开局", countdown: "倒计时等待接口开放",
  running_robot: "机器人程序运行中", aborting: "正在中止本局",
};
const BATCH_STATUS_TEXT = {
  running: "运行中", stopping: "停止中", finished: "已完成", stopped: "已停止",
};
const SHORT_REASON = {
  user_exit: "正常退出", user_abort: "手工中止", window_timeout: "窗口超时",
  program_timeout: "程序超时", virtual_timeout: "虚拟时间超时",
};

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function fmtPct(v) { return v == null ? "—" : (+v).toFixed(1) + "%"; }

function defaultBatchGroups() {
  return [
    { name: "Q3", problem: 3, mode: "practice", count: 10,
      robot_command: localStorage.getItem("btCmd:0") || "", cwd: "" },
    { name: "Q4", problem: 4, mode: "practice", count: 10,
      robot_command: localStorage.getItem("btCmd:1") || "", cwd: "" },
  ];
}
function renderGroupRows(groups) {
  const wrap = $("#batch-groups");
  wrap.innerHTML = groups.map((g, i) => `
    <div class="group-row" data-i="${i}">
      <input type="text" class="g-name" placeholder="名称" value="${esc(g.name)}" title="分组名称">
      <select class="g-problem">
        <option value="3" ${g.problem !== 4 ? "selected" : ""}>问题 3</option>
        <option value="4" ${g.problem === 4 ? "selected" : ""}>问题 4</option>
      </select>
      <select class="g-mode">
        <option value="practice" ${g.mode !== "formal" ? "selected" : ""}>演练</option>
        <option value="formal" ${g.mode === "formal" ? "selected" : ""}>正式</option>
      </select>
      <input type="number" class="g-count" min="1" max="200" value="${+g.count || 10}" title="局数">
      <input type="text" class="g-cmd" placeholder="机器人程序命令，如 D:\\path\\Final_Robot_For_Q3.exe 或 python my_robot.py" value="${esc(g.robot_command)}">
      <input type="text" class="g-cwd" placeholder="工作目录（可选）" value="${esc(g.cwd)}">
      <button class="btn btn-del-row" title="删除分组">×</button>
    </div>`).join("");
  wrap.querySelectorAll(".btn-del-row").forEach((b) => b.addEventListener("click", () => {
    if (wrap.querySelectorAll(".group-row").length <= 1) return;
    b.closest(".group-row").remove();
  }));
}
function collectBatchGroups() {
  return $$("#batch-groups .group-row").map((row) => ({
    name: row.querySelector(".g-name").value.trim(),
    problem: +row.querySelector(".g-problem").value,
    mode: row.querySelector(".g-mode").value,
    count: +row.querySelector(".g-count").value || 1,
    robot_command: row.querySelector(".g-cmd").value.trim(),
    cwd: row.querySelector(".g-cwd").value.trim() || null,
  }));
}
function parseBatchSeeds(str) {
  return str.split(/[\s,;，；]+/).filter(Boolean)
    .map((s) => (/^-?\d+$/.test(s) ? parseInt(s, 10) : s));
}
function collectBatchConfig() {
  const t = $("#bt-timeout").value.trim();
  return {
    groups: collectBatchGroups(),
    seeds: parseBatchSeeds($("#bt-seeds").value),
    per_run_timeout_s: t === "" ? null : +t,
    inter_run_delay_s: +$("#bt-delay").value || 0,
    continue_on_error: $("#bt-onerr").value !== "stop",
  };
}
function setBatchFormLocked(locked) {
  $("#batch-fieldset").disabled = locked;
  $("#btn-batch-start").disabled = locked;
  $("#btn-batch-start").classList.toggle("hidden", locked);
  $("#btn-batch-stop").classList.toggle("hidden", !locked);
  $("#batch-lock").classList.toggle("hidden", !locked);
}

$("#btn-batch-add-group").addEventListener("click", () => {
  renderGroupRows(collectBatchGroups().concat(defaultBatchGroups().slice(0, 1)));
});
$("#btn-batch-start").addEventListener("click", () => {
  const cfg = collectBatchConfig();
  if (!cfg.groups.length) return;
  const bad = cfg.groups.find((g) => !g.robot_command);
  if (bad) { confirmModal("无法开始", "每个分组都需要填写机器人程序命令。", null, { danger: false }); return; }
  cfg.groups.forEach((g, i) => localStorage.setItem("btCmd:" + i, g.robot_command));
  const total = cfg.groups.reduce((a, g) => a + g.count, 0);
  const formal = cfg.groups.some((g) => g.mode === "formal");
  const doStart = async () => {
    $("#batch-msg").textContent = "启动中…";
    const r = await apiPost("/api/batch_start", cfg);
    $("#batch-msg").textContent = "";
    if (!r.ok) { confirmModal("无法开始批量", r.error || "启动失败", null, { danger: false }); return; }
    pollBatch();
  };
  const warn = formal
    ? `共 ${total} 局，含正式测试分组，将消耗正式测试机会（中止同样占用）。\n确认开始？`
    : `即将连续自动运行 ${total} 局（自动开局并启动机器人程序）。\n确认开始？`;
  confirmModal("开始批量跑测", warn, doStart);
});
$("#btn-batch-stop").addEventListener("click", () => {
  confirmModal("停止批量跑测",
    "将中止当前局并跳过剩余局，已完成局数的统计仍会保留。\n确认停止？",
    async () => { await apiPost("/api/batch_stop", {}); pollBatch(); },
    { double: true });
});
$("#btn-batch-refresh").addEventListener("click", () => { batchHistoryKey = null; pollBatch(); });

async function pollBatch() {
  let b = null;
  try {
    const r = await fetch("/api/batch_state");
    const d = await r.json();
    b = d.batch;
  } catch (e) { return; }
  if (b) {
    const live = b.status === "running" || b.status === "stopping";
    setBatchFormLocked(live);
    renderBatchLive(b);
    if (!live && b.stats) renderBatchResult(b);
    else if (live) $("#batch-result-panel").classList.add("hidden");
  }
  await loadBatchHistory();
}

function renderBatchLive(b) {
  $("#batch-live-panel").classList.remove("hidden");
  const cfg = b.config || {};
  $("#bl-title").textContent = b.batch_id +
    (cfg.groups ? " · " + cfg.groups.map((g) =>
      `${g.name}(P${g.problem}${g.mode === "formal" ? "正式" : "演练"}×${g.count})`).join(" + ") : "");
  $("#bl-progress-text").textContent = b.done_runs + " / " + b.total_runs;
  $("#bl-fill").style.width = (b.total_runs ? 100 * b.done_runs / b.total_runs : 0) + "%";
  const cur = b.current;
  $("#bl-current").textContent = cur
    ? `第 ${b.done_runs + 1} 局 · ${cur.group_name} · ${BATCH_PHASE_TEXT[cur.phase] || cur.phase}` +
      (cur.case_code ? ` · ${cur.case_code}` : "") + ` · 种子 ${cur.seed}`
    : (b.status === "running" || b.status === "stopping" ? "—" :
       (BATCH_STATUS_TEXT[b.status] || b.status) +
       (b.error ? "：" + b.error : ""));
  const rows = b.runs || [];
  $("#bl-tbody").innerHTML = rows.map((r) => {
    const s = r.summary || {};
    let status = '<span class="res-dim">—</span>';
    if (r.error) status = '<span class="res-bad">失败</span>';
    else if (r.timeout) status = '<span class="res-warn">超时</span>';
    else if (r.ok) status = '<span class="res-ok">完成</span>';
    const reason = r.error ? esc(r.error)
      : (SHORT_REASON[s.reason] || s.reason || "—");
    return `<tr>
      <td>${r.index}</td>
      <td class="plain">${esc(r.group_name)}</td>
      <td class="mono">${r.case_code || "—"}</td>
      <td>${status}</td>
      <td>${s.total != null ? s.cleared + " / " + s.total : "—"}</td>
      <td>${s.measures ?? "—"}</td>
      <td>${s.virtual_total_s != null ? (+s.virtual_total_s).toFixed(1) : "—"}</td>
      <td>${r.wall_s != null ? r.wall_s.toFixed(1) : "—"}</td>
      <td class="plain">${reason}${r.timeout ? "（已强制中止）" : ""}</td>
      <td>${r.robot_log ? `<a href="/api/robot_log/${encodeURIComponent(r.robot_log.replace(/\.log$/, ""))}" target="_blank">查看</a>` : "—"}</td>
    </tr>`;
  }).join("");
  const wrap = $("#bl-tbody").closest(".table-wrap");
  if (wrap && b.status === "running") wrap.scrollTop = wrap.scrollHeight;
}

function metricTableHtml(metrics) {
  const rows = BATCH_METRIC_KEYS.filter((k) => metrics[k]).map((k) => {
    const m = metrics[k];
    if (!m.n) return `<tr><td class="plain">${m.label}${m.unit && m.unit !== "%" ? "（" + m.unit + "）" : ""}</td><td>0</td><td colspan="5" class="res-dim">无有效数据</td></tr>`;
    const c = (v, d = 2) => (+v).toFixed(d);
    return `<tr>
      <td class="plain">${m.label}${m.unit && m.unit !== "%" ? "（" + m.unit + "）" : ""}</td>
      <td>${m.n}</td>
      <td>${c(m.mean)}${m.unit === "%" ? "%" : ""}</td>
      <td>±${c(m.std)}</td>
      <td>${c(m.min)}</td>
      <td>${c(m.median)}</td>
      <td>${c(m.max)}</td>
    </tr>`;
  }).join("");
  return `<div class="table-wrap" style="max-height:none"><table class="tbl">
    <thead><tr><th>指标</th><th style="width:60px">有效局数</th><th style="width:90px">平均</th>
    <th style="width:80px">标准差</th><th style="width:80px">最小</th>
    <th style="width:80px">中位数</th><th style="width:80px">最大</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function renderBatchResult(b) {
  const s = b.stats;
  if (!s) return;
  const el = $("#batch-result-panel");
  el.classList.remove("hidden");
  const cards = [
    ["完成局数", `${s.runs_ok} / ${s.runs_total}`],
    ["整体清除率", fmtPct(s.clear_rate_overall)],
    ["全清局数", `${s.full_clear_runs ?? "—"}（${fmtPct(s.full_clear_rate)}）`],
    ["平均清除比例", fmtPct(s.metrics.cleared_ratio && s.metrics.cleared_ratio.mean)],
    ["平均虚拟时间", s.metrics.virtual_total_s && s.metrics.virtual_total_s.mean != null
      ? (+s.metrics.virtual_total_s.mean).toFixed(1) + " s" : "—"],
    ["平均程序耗时", s.metrics.program_run_s && s.metrics.program_run_s.mean != null
      ? (+s.metrics.program_run_s.mean).toFixed(1) + " s" : "—"],
    ["失败 / 超时局数", `${s.runs_error} / ${s.timeout_runs}`],
    ["批量总耗时", s.wall_total_s != null ? s.wall_total_s.toFixed(0) + " s" : "—"],
  ];
  const chips = Object.keys(s.end_reasons || {}).map((k) =>
    `<span class="chip">${SHORT_REASON[k] || k} × ${s.end_reasons[k]}</span>`).join("");
  let html = `<div class="panel-head"><h3 style="margin:0">统计结果 <span class="dim small">${b.batch_id} · ${BATCH_STATUS_TEXT[b.status] || b.status}</span></h3>
    <div class="btn-row">
      <a class="btn" href="/api/batch/${b.batch_id}" download="${b.batch_id}.json">导出 JSON</a>
      <a class="btn" href="/api/batch_csv/${b.batch_id}" download="${b.batch_id}.csv">导出 CSV</a>
    </div></div>
    <div class="end-grid">${cards.map(([k, v]) =>
      `<div class="timer"><label>${k}</label><b>${v}</b></div>`).join("")}</div>
    <div style="margin-top:10px">${chips || '<span class="dim small">无结束局</span>'}</div>
    <h3>总体指标（逐局分布）</h3>${metricTableHtml(s.metrics)}`;
  (s.groups || []).forEach((g) => {
    const info = g.group || {};
    html += `<h3>分组：${esc(info.name)} · 问题 ${info.problem}${info.mode === "formal" ? "正式" : "演练"} × ${info.count} 局
      <span class="dim small">完成 ${g.runs_ok}/${g.runs_total} · 全清率 ${fmtPct(g.full_clear_rate)} · 整体清除率 ${fmtPct(g.clear_rate_overall)}</span></h3>`;
    html += metricTableHtml(g.metrics);
  });
  if (s.zero_clear_runs) html += `<p class="dim small">注：有 ${s.zero_clear_runs} 局未清除任何干扰源。</p>`;
  el.innerHTML = html;
}

let batchHistoryKey = null;
async function loadBatchHistory() {
  if (!$("#tab-batch").classList.contains("active")) return;
  let list;
  try {
    const r = await fetch("/api/batches");
    list = (await r.json()).batches || [];
  } catch (e) { return; }
  const key = list.map((b) => b.batch_id + ":" + b.status).join("|");
  if (key === batchHistoryKey) return;
  batchHistoryKey = key;
  $("#bh-tbody").innerHTML = list.map((b) => `<tr>
    <td class="mono">${b.batch_id}</td>
    <td class="plain">${esc(b.desc)}</td>
    <td>${b.done_runs} / ${b.total_runs}</td>
    <td>${fmtPct(b.clear_rate_overall)}</td>
    <td>${fmtPct(b.full_clear_rate)}</td>
    <td class="plain">${BATCH_STATUS_TEXT[b.status] || b.status}</td>
    <td><div class="btn-row">
      <button class="btn" onclick="window.__viewBatch('${b.batch_id}')">查看</button>
      <a class="btn" href="/api/batch_csv/${b.batch_id}" download="${b.batch_id}.csv">CSV</a>
      <button class="btn" onclick="window.__deleteBatch('${b.batch_id}')">删除</button>
    </div></td>
  </tr>`).join("");
  $("#bh-empty").classList.toggle("hidden", list.length > 0);
}
window.__viewBatch = async (id) => {
  try {
    const r = await fetch("/api/batch/" + id);
    const b = await r.json();
    if (!b || !b.batch_id) return;
    renderBatchLive(b);
    if (b.stats) renderBatchResult(b);
    $("#tab-batch").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { /* 忽略 */ }
};
window.__deleteBatch = (id) => {
  confirmModal("删除批量记录",
    `确定删除批量 ${id} 的报告与机器人输出日志吗？\n（不影响单局测试日志）`,
    async () => {
      await apiPost("/api/batch_delete", { batch_id: id });
      batchHistoryKey = null;
      pollBatch();
    });
};

renderGroupRows(defaultBatchGroups());

/* ---------------------- 可视化联动 ---------------------- */
$("#vc-truth").addEventListener("change", poll);
["vc-coverage", "vc-rays", "vc-trail", "vc-grid"].forEach((id) => {
  const el = document.getElementById(id);
  el.addEventListener("change", () => Viz && Viz.draw());
});

/* ---------------------- 启动 ---------------------- */
window.__attemptsCache = null;
const _origRenderState = renderState;
renderState = function (st, snap) {
  window.__attemptsCache = st.attempts;
  _origRenderState(st, snap);
};

loadConfig();
poll();
