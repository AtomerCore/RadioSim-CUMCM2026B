/* 机器狗可视化：Canvas 地图、实时跟随与事件回放 */
"use strict";

const Viz = (() => {
  const canvas = document.getElementById("viz-canvas");
  const ctx = canvas.getContext("2d");
  const $ = (sel) => document.querySelector(sel);

  const CHANNEL_COLORS = [];
  for (let i = 0; i < 20; i++) {
    CHANNEL_COLORS.push(`hsl(${Math.round(i * 360 / 20)}, 68%, 64%)`);
  }
  const chColor = (ch) => CHANNEL_COLORS[(ch - 1) % 20];

  // ---------------- 状态 ----------------
  let events = [];        // 当前会话全部事件（按 seq 升序）
  let snapshot = null;    // 最新会话快照
  let live = true;        // 实时模式
  let playIdx = 0;        // 回放事件索引
  let playing = false;
  let playSpeed = 5;      // 事件/秒
  let playTimer = null;

  // 视图（世界坐标中心 + 每米像素数）
  let view = { cx: 0, cy: 0, scale: 0.15 };
  let follow = false;
  let dragging = null;

  const opts = {
    truth: true, coverage: false, rays: false, trail: true, grid: true,
  };

  // 干扰源单源图层状态（按频道，显式值）：{ coverage, rays, hidden }
  // 右侧栏全局开关 = 批量设置全部源；单源开关只改该源；两者互不粘滞，
  // 混合状态时全局复选框显示不确定态（indeterminate）
  let srcOpts = {};
  function srcState(ch) {
    if (!srcOpts[ch])
      srcOpts[ch] = { coverage: opts.coverage, rays: opts.rays, hidden: false };
    return srcOpts[ch];
  }

  // 依据当前各源状态刷新右侧栏全局复选框（全开=勾选，混合=不确定，全关=空）
  function syncLayerChecks() {
    const list = (snapshot && snapshot.sources) || [];
    const setCk = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (!list.length) { el.indeterminate = false; return; }
      const vals = list.map((s) => srcState(s.channel)[key]);
      el.checked = vals.every(Boolean);
      el.indeterminate = !el.checked && vals.some(Boolean);
    };
    setCk("vc-coverage", "coverage");
    setCk("vc-rays", "rays");
  }

  // 全局开关：批量作用于全部源，并刷新打开中的详情卡片
  function applyLayerToAll(key, val) {
    Object.keys(srcOpts).forEach((ch) => { srcOpts[ch][key] = val; });
    cardKey = null;
    renderCard();
    syncLayerChecks();
    draw();
  }

  // 点位选中（点击画布查看详情）：{ type: "source"|"event"|"robot", idx?/seq? }
  let selected = null;

  // ---------------- 坐标变换 ----------------
  let W = 0, H = 0, DPR = 1;
  function resize() {
    DPR = window.devicePixelRatio || 1;
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    selected = null; hideCard();
    // 画布隐藏（clientWidth=0）时不初始化视图，等首次可见再适配
    if (W > 0 && H > 0 && !view.inited) fitAll();
    draw();
  }
  const sx = (x) => W / 2 + (x - view.cx) * view.scale;
  const sy = (y) => H / 2 - (y - view.cy) * view.scale;
  const wx = (px) => (px - W / 2) / view.scale + view.cx;
  const wy = (py) => (H / 2 - py) / view.scale + view.cy;

  function fitAll() {
    const R = snapshot ? snapshot.arena_radius : 1800;
    if (W > 0 && H > 0) {
      view.scale = Math.max(1e-4, Math.min(W, H) / (2 * R * 1.14));
    }
    view.cx = 0; view.cy = 0;
    view.inited = true;
    follow = false;
  }
  function followRobot() {
    follow = true;
    const r = robotState();
    view.cx = r.x; view.cy = r.y;
    if (view.scale < 0.25) view.scale = 0.35;
    draw();
  }

  // ---------------- 事件推导 ----------------
  function stateAt(k) {
    // 由 events[0..k] 推导机器狗状态
    let pos = { x: 0, y: 0 }, heading = null, channel = snapshot ? 1 : 1;
    const trail = [], rays = [], cleared = new Set();
    let vt = 0, measures = 0, clears = 0;
    const upto = live ? events.length : k + 1;
    for (let i = 0; i < upto && i < events.length; i++) {
      const ev = events[i];
      if (ev.kind === "enter") { pos = { x: 0, y: 0 }; trail.push(pos); }
      else if (ev.kind === "measure" || ev.kind === "clear") {
        pos = { x: ev.pos[0], y: ev.pos[1] };
        heading = ev.heading != null ? ev.heading : heading;
        trail.push(pos);
        vt = ev.vt_s;
        if (ev.kind === "measure") {
          channel = ev.channel;
          measures++;
          if (ev.result === "direction")
            rays.push({ x: ev.pos[0], y: ev.pos[1], deg: ev.svd, ch: ev.channel, vt: ev.vt_s });
        } else {
          clears++;
          if (ev.cleared) cleared.add(ev.channel);
        }
      } else if (ev.kind === "exit" || ev.kind === "reject") {
        if (ev.vt_s) vt = ev.vt_s;
      }
    }
    return { pos, heading, channel, trail, rays, cleared, vt, measures, clears };
  }
  function robotState() {
    if (live && snapshot && snapshot.robot)
      return { x: snapshot.robot.x, y: snapshot.robot.y, heading: snapshot.robot.heading };
    const s = stateAt(playIdx - 1);
    return { x: s.pos.x, y: s.pos.y, heading: s.heading };
  }

  // ---------------- 对外接口 ----------------
  function reset() {
    events = []; live = true; playing = false; stopPlay();
    playIdx = 0; selected = null; hideCard();
    srcOpts = {};
    syncLayerChecks();
    updateSlider(); draw(); updateStats(null);
  }
  function push(statePayload, newEvents) {
    for (const ev of newEvents) events.push(ev);
    events.sort((a, b) => a.seq - b.seq);
    if (events.length > 20000) events = events.slice(-20000);
    updateSlider();
    if (live) draw();
  }
  function setSnapshot(snap) {
    snapshot = snap;
    if (selected && selected.type === "source" &&
        !(snapshot.sources && snapshot.sources[selected.idx])) {
      selected = null; hideCard();
    }
    if (live) {
      if (follow) { const r = robotState(); view.cx = r.x; view.cy = r.y; }
      draw();
    }
    renderCard();   // 机器狗位置 / 已清除状态可能变化
    syncLayerChecks();
    updateStats(snap);
    buildLegend();
  }
  function setLive(v) {
    live = v;
    if (v) { stopPlay(); playIdx = events.length - 1; }
    updateSlider();
    draw();
  }

  // ---------------- 回放控制 ----------------
  const slider = document.getElementById("viz-slider");
  function updateSlider() {
    slider.max = Math.max(0, events.length - 1);
    if (live) slider.value = slider.max;
    else if (+slider.value > +slider.max) slider.value = slider.max;
    $("#viz-progress").textContent = events.length
      ? (live ? "实时（" + events.length + " 个事件）" : (playIdx + 1) + " / " + events.length)
      : "暂无事件";
  }
  function stopPlay() {
    playing = false;
    if (playTimer) { clearInterval(playTimer); playTimer = null; }
    const b = document.getElementById("viz-play");
    if (b) b.textContent = "▶ 播放";
  }
  function startPlay() {
    playing = true;
    if (playIdx >= events.length - 1) playIdx = 0;
    document.getElementById("viz-play").textContent = "⏸ 暂停";
    playTimer = setInterval(() => {
      playIdx = Math.min(events.length - 1, playIdx + 1);
      slider.value = playIdx;
      if (playIdx >= events.length - 1) stopPlay();
      draw();
    }, 1000 / playSpeed);
  }

  // ---------------- 绘制 ----------------
  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#0a0b0f";
    ctx.fillRect(0, 0, W, H);
    if (!snapshot || !(view.scale > 0) || !(W > 0)) {
      if (!snapshot) {
        ctx.fillStyle = "rgba(190,196,208,.45)";
        ctx.font = "15px 'Microsoft YaHei'";
        ctx.textAlign = "center";
        ctx.fillText("暂无测试会话：请在“测试控制”页开始一局测试", W / 2, H / 2);
      }
      return;
    }
    drawGrid();
    drawSources();
    drawRays();   // 内部按全局开关与单源覆盖逐条过滤
    if (opts.trail) drawTrail();
    drawRobot();
    drawSelection();
    drawHUD();
    updateCardPos();
  }

  function gridStep() {
    // 自适应网格间距：屏幕上约 100px 一格，防止缩放过深时循环爆炸
    const target = 100 / view.scale;
    const steps = [50, 100, 200, 300, 500, 1000, 2000, 5000,
                   10000, 20000, 50000, 100000];
    for (const s of steps) if (s >= target) return s;
    return 200000;
  }

  function drawGrid() {
    const R = snapshot.arena_radius;
    ctx.save();
    if (opts.grid) {
      const step = gridStep();
      const x0 = Math.floor(wx(0) / step) * step, x1 = wx(W);
      const y0 = Math.floor(wy(H) / step) * step, y1 = wy(0);
      const nxCols = Math.min(300, Math.floor((x1 - x0) / step));
      const nyRows = Math.min(300, Math.floor((y1 - y0) / step));
      ctx.strokeStyle = "rgba(255,255,255,.05)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i <= nxCols; i++) {
        const x = x0 + i * step;
        ctx.moveTo(sx(x), 0); ctx.lineTo(sx(x), H);
      }
      for (let i = 0; i <= nyRows; i++) {
        const y = y0 + i * step;
        ctx.moveTo(0, sy(y)); ctx.lineTo(W, sy(y));
      }
      ctx.stroke();
      // 坐标轴
      ctx.strokeStyle = "rgba(255,255,255,.2)";
      ctx.beginPath();
      ctx.moveTo(sx(0), 0); ctx.lineTo(sx(0), H);
      ctx.moveTo(0, sy(0)); ctx.lineTo(W, sy(0));
      ctx.stroke();
      // 坐标标注
      ctx.fillStyle = "rgba(255,255,255,.38)";
      ctx.font = "11px Consolas";
      ctx.textAlign = "left";
      for (let i = 0; i <= nxCols; i += 2) {
        const x = x0 + i * step;
        if (x !== 0) ctx.fillText(String(x), sx(x) + 3, sy(0) + 13);
      }
      for (let i = 0; i <= nyRows; i += 2) {
        const y = y0 + i * step;
        if (y !== 0) ctx.fillText(String(y), sx(0) + 4, sy(y) - 4);
      }
    }
    // 目标区域（白色辉光边界）
    ctx.save();
    ctx.beginPath();
    ctx.arc(sx(0), sy(0), R * view.scale, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255,255,255,.6)";
    ctx.lineWidth = 1.4;
    ctx.shadowColor = "rgba(255,255,255,.4)";
    ctx.shadowBlur = 14;
    ctx.setLineDash([8, 6]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
    // 指北针
    const nx = W - 34, ny = 34;
    ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.fillStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(nx, ny + 12); ctx.lineTo(nx, ny - 10);
    ctx.lineTo(nx - 4, ny - 3); ctx.moveTo(nx, ny - 10); ctx.lineTo(nx + 4, ny - 3);
    ctx.stroke();
    ctx.font = "12px 'Microsoft YaHei'"; ctx.textAlign = "center";
    ctx.fillText("北", nx, ny + 26);
    ctx.restore();
  }

  function clearedNow() {
    if (live && snapshot && snapshot.stats)
      return new Set((snapshot.sources || []).filter((s) => s.cleared).map((s) => s.channel));
    return stateAt(playIdx - 1).cleared;
  }

  function drawSources() {
    if (!opts.truth || !snapshot || !snapshot.sources) return;
    const clearedSet = clearedNow();
    for (const s of snapshot.sources) {
      const o = srcState(s.channel);
      const c = chColor(s.channel);
      const px = sx(s.x), py = sy(s.y);
      const cleared = clearedSet.has(s.channel);
      ctx.save();
      if (o.hidden) {
        // 幽灵模式：极淡圆点，不画覆盖/标签，但保留可点击性以便恢复显示
        ctx.globalAlpha = 0.16;
        ctx.beginPath();
        ctx.arc(px, py, 4, 0, Math.PI * 2);
        ctx.fillStyle = c;
        ctx.fill();
        ctx.restore();
        continue;
      }
      ctx.globalAlpha = cleared ? 0.45 : 1;
      // 检测范围（独立图层：全向虚线圆 / 定向 ±90° 扇形）
      if (o.coverage) {
        if (s.kind === "omni") {
          ctx.beginPath();
          ctx.arc(px, py, s.recv_radius * view.scale, 0, Math.PI * 2);
          ctx.strokeStyle = c;
          ctx.globalAlpha = cleared ? 0.2 : 0.5;
          ctx.setLineDash([5, 5]);
          ctx.lineWidth = 1;
          ctx.stroke();
          ctx.setLineDash([]);
        } else {
          // 定向扇形起止角从 -(dir+90) 到 -(dir-90)，保证朝向 direction_deg 一侧
          const a0 = -(s.direction_deg + 90) * Math.PI / 180;
          const a1 = -(s.direction_deg - 90) * Math.PI / 180;
          ctx.beginPath();
          ctx.moveTo(px, py);
          ctx.arc(px, py, s.recv_radius * view.scale, a0, a1);
          ctx.closePath();
          ctx.fillStyle = c;
          ctx.globalAlpha = cleared ? 0.06 : 0.13;
          ctx.fill();
          ctx.strokeStyle = c;
          ctx.globalAlpha = cleared ? 0.25 : 0.6;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
      // 定向朝向箭头：源自身属性，独立于检测范围常显
      if (s.kind === "directional") {
        const ad = -s.direction_deg * Math.PI / 180;
        const L = Math.min(46, s.recv_radius * view.scale * 0.5);
        ctx.strokeStyle = c;
        ctx.globalAlpha = cleared ? 0.35 : 0.85;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(px + Math.cos(ad) * L, py + Math.sin(ad) * L);
        ctx.stroke();
      }
      // 源本体
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(px, py, 6, 0, Math.PI * 2);
      ctx.fillStyle = cleared ? "rgba(255,255,255,.28)" : c;
      ctx.fill();
      ctx.strokeStyle = "#0a0b0f"; ctx.lineWidth = 1.5; ctx.stroke();
      // 频道标签
      ctx.font = "bold 11px Consolas";
      ctx.textAlign = "center";
      ctx.fillStyle = cleared ? "rgba(255,255,255,.45)" : "#fff";
      ctx.fillText("ch" + s.channel, px, py - 10);
      if (cleared) {
        ctx.strokeStyle = "#46d68a"; ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(px - 4, py); ctx.lineTo(px - 1, py + 4); ctx.lineTo(px + 5, py - 4);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  function drawRays() {
    const st = live ? stateAt(events.length - 1) : stateAt(playIdx - 1);
    const rays = st.rays.slice(-160);   // 最多显示最近 160 条
    const err = snapshot.svd_error_deg || 1;
    for (const r of rays) {
      if (!srcState(r.ch).rays) continue;
      const c = chColor(r.ch);
      const px = sx(r.x), py = sy(r.y);
      const rad = -r.deg * Math.PI / 180;
      const L = 520 * view.scale;
      ctx.save();
      // ±误差楔形（注意起止角顺序：canvas 默认正向扫过增大方向，
      // 需从 -(deg+err) 画到 -(deg-err)，才是 2err 的短楔形而非几乎整圆）
      ctx.beginPath();
      ctx.moveTo(px, py);
      const w0 = -(r.deg + err) * Math.PI / 180, w1 = -(r.deg - err) * Math.PI / 180;
      ctx.arc(px, py, L, w0, w1);
      ctx.closePath();
      ctx.fillStyle = c; ctx.globalAlpha = 0.08; ctx.fill();
      // 中心射线
      ctx.globalAlpha = 0.75;
      ctx.strokeStyle = c; ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(px + Math.cos(rad) * L, py + Math.sin(rad) * L);
      ctx.stroke();
      // 检测点
      ctx.globalAlpha = 0.9;
      ctx.beginPath(); ctx.arc(px, py, 3.2, 0, Math.PI * 2);
      ctx.fillStyle = c; ctx.fill();
      ctx.restore();
    }
  }

  function drawTrail() {
    const st = live ? stateAt(events.length - 1) : stateAt(playIdx - 1);
    if (st.trail.length < 2) return;
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,.5)";
    ctx.lineWidth = 1.6;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(sx(st.trail[0].x), sy(st.trail[0].y));
    for (let i = 1; i < st.trail.length; i++)
      ctx.lineTo(sx(st.trail[i].x), sy(st.trail[i].y));
    ctx.stroke();
    ctx.restore();
  }

  function drawRobot() {
    const r = robotState();
    const px = sx(r.x), py = sy(r.y);
    const heading = r.heading == null ? 0 : r.heading;
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(-heading * Math.PI / 180);
    // 阴影
    ctx.fillStyle = "rgba(0,0,0,.5)";
    ctx.beginPath(); ctx.ellipse(1, 3, 17, 11, 0, 0, Math.PI * 2); ctx.fill();
    // 四条腿
    ctx.strokeStyle = "rgba(220,224,232,.85)"; ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(-9, -8); ctx.lineTo(-12, -12);
    ctx.moveTo(-3, -8); ctx.lineTo(-3, -13);
    ctx.moveTo(3, -8); ctx.lineTo(3, -13);
    ctx.moveTo(9, -8); ctx.lineTo(12, -12);
    ctx.moveTo(-9, 8); ctx.lineTo(-12, 12);
    ctx.moveTo(-3, 8); ctx.lineTo(-3, 13);
    ctx.moveTo(3, 8); ctx.lineTo(3, 13);
    ctx.moveTo(9, 8); ctx.lineTo(12, 12);
    ctx.stroke();
    // 身体（白色 + 柔和辉光）
    ctx.shadowColor = "rgba(255,255,255,.55)";
    ctx.shadowBlur = 16;
    ctx.fillStyle = "#eceef2";
    ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(-14, -9, 28, 18, 5) : ctx.rect(-14, -9, 28, 18);
    ctx.fill(); ctx.stroke();
    ctx.shadowBlur = 0;
    // 头部（朝向 = x 正方向）
    ctx.beginPath(); ctx.arc(17, 0, 6, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff"; ctx.fill(); ctx.stroke();
    // 尾部天线
    ctx.beginPath(); ctx.moveTo(-14, 0); ctx.lineTo(-21, -6); ctx.stroke();
    ctx.shadowColor = "rgba(255,255,255,.9)";
    ctx.shadowBlur = 10;
    ctx.beginPath(); ctx.arc(-21, -6, 2.2, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff"; ctx.fill();
    ctx.restore();
    // 标签（不随朝向旋转）
    ctx.save();
    ctx.font = "bold 12px 'Microsoft YaHei'";
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(255,255,255,.92)";
    ctx.shadowColor = "rgba(0,0,0,.8)";
    ctx.shadowBlur = 4;
    ctx.fillText("机器狗 · 频道 " + currentChannel(), px, py - 24);
    ctx.restore();
  }
  function currentChannel() {
    if (live && snapshot && snapshot.robot) return snapshot.robot.channel;
    return stateAt(playIdx - 1).channel;
  }

  function drawHUD() {
    ctx.save();
    ctx.font = "11px Consolas";
    ctx.fillStyle = "rgba(255,255,255,.55)";
    ctx.textAlign = "left";
    ctx.fillText("比例尺：100 m = " + (100 * view.scale).toFixed(0) + " px", 10, H - 10);
    if (!live) {
      ctx.fillStyle = "#f2b13d";
      ctx.textAlign = "right";
      ctx.fillText("回放模式", W - 12, H - 10);
    } else if (follow) {
      ctx.fillStyle = "#46d68a";
      ctx.textAlign = "right";
      ctx.fillText("跟随机器狗", W - 12, H - 10);
    }
    ctx.restore();
  }

  // ---------------- 点位选中与详情卡片 ----------------
  const cardEl = document.getElementById("point-card");
  const cardTypeEl = document.getElementById("pc-type");
  const cardBodyEl = document.getElementById("pc-body");

  function hideCard() { cardEl.classList.add("hidden"); cardKey = null; }
  function clearSelection() { selected = null; hideCard(); draw(); }
  document.getElementById("pc-close").addEventListener("click", clearSelection);

  function setSrcOpt(ch, key, val) {
    srcState(ch)[key] = val;
  }

  function findEventBySeq(seq) {
    return events.find((e) => e.seq === seq) || null;
  }
  const fmtN = (v, d = 1) => (+v).toFixed(d);
  const fmtPosW = (p) => "(" + fmtN(p[0]) + ", " + fmtN(p[1]) + ") m";
  const escH = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  const cardRow = (k, v) => `<div><span>${escH(k)}</span><b>${escH(v)}</b></div>`;

  function canvasClick(px, py) {
    if (!snapshot) return;
    const cands = [];
    if (opts.truth && snapshot.sources) {
      snapshot.sources.forEach((s, i) => {
        const d = Math.hypot(px - sx(s.x), py - sy(s.y));
        if (d <= 14) cands.push({ type: "source", idx: i, d });
      });
    }
    for (const ev of events) {
      if (!ev.pos) continue;
      let d = Math.hypot(px - sx(ev.pos[0]), py - sy(ev.pos[1]));
      if (d > 12) continue;
      if (ev.kind === "reject") d += 3;   // 同位置时优先真实指令点
      cands.push({ type: "event", seq: ev.seq, d });
    }
    const rb = robotState();
    const dr = Math.hypot(px - sx(rb.x), py - sy(rb.y));
    if (dr <= 22) cands.push({ type: "robot", d: dr - 1.5 });   // 点击机器狗本体时优先选中
    if (!cands.length) { clearSelection(); return; }
    cands.sort((a, b) => a.d - b.d);
    selected = cands[0];
    showCard(px, py);
    startSelAnim();
    draw();
  }

  function placeCard(px, py) {
    const host = canvas.parentElement;
    const baseX = canvas.offsetLeft + px, baseY = canvas.offsetTop + py;
    const cw = cardEl.offsetWidth, ch = cardEl.offsetHeight;
    let x = baseX + 16, y = baseY + 14;
    if (x + cw > host.clientWidth - 6) x = baseX - cw - 16;
    if (y + ch > host.clientHeight - 6) y = baseY - ch - 14;
    cardEl.style.left = Math.max(6, x) + "px";
    cardEl.style.top = Math.max(6, y) + "px";
  }

  function showCard(px, py) {
    renderCard();
    cardEl.classList.remove("hidden");
    placeCard(px, py);
  }

  // 平移/缩放/跟随导致点位在画布内移动时，卡片同步贴附到点旁；
  // 点被移出视野时暂时隐藏（内容保留），回到视野自动恢复。
  function updateCardPos() {
    if (!selected || !snapshot) return;
    const p = selectedScreenPos();
    if (!p) return;
    if (p.x < -24 || p.x > W + 24 || p.y < -24 || p.y > H + 24) {
      cardEl.classList.add("hidden");
      return;
    }
    cardEl.classList.remove("hidden");
    placeCard(p.x, p.y);
  }

  // 卡片渲染节流：同一目标且关键状态未变时跳过重建，避免轮询期间打断开关交互
  let cardKey = null;

  function renderCard() {
    if (!selected || !snapshot) { hideCard(); return; }
    let title = "", rows = "";
    if (selected.type === "source") {
      const s = snapshot.sources && snapshot.sources[selected.idx];
      if (!s) { selected = null; hideCard(); return; }
      const cleared = clearedNow().has(s.channel);
      const key = "src:" + s.channel + ":" + cleared;
      if (key === cardKey && !cardEl.classList.contains("hidden")) return;
      cardKey = key;
      const o = srcState(s.channel);
      title = "干扰源 · ch" + s.channel;
      rows = cardRow("类型", s.kind === "directional" ? "定向" : "全向") +
        cardRow("位置", fmtPosW([s.x, s.y])) +
        cardRow("接收半径", fmtN(s.recv_radius, 0) + " m") +
        (s.kind === "directional" ? cardRow("定向方向", fmtN(s.direction_deg, 0) + "°") : "") +
        cardRow("状态", cleared ? "已清除" : "未清除") +
        `<div class="pc-toggles">
          <label class="pc-toggle" title="单独控制该源的检测范围（有效接收半径圆 / 定向扇形）显示">
            <input type="checkbox" id="pc-cov"${o.coverage ? " checked" : ""}>检测范围</label>
          <label class="pc-toggle" title="单独控制该频道测得的示向度射线（含 ±误差楔形与检测点）">
            <input type="checkbox" id="pc-rays"${o.rays ? " checked" : ""}>示向度射线</label>
          <label class="pc-toggle" title="关闭后以幽灵模式淡显，仍可点击恢复">
            <input type="checkbox" id="pc-vis"${o.hidden ? "" : " checked"}>显示该源</label>
        </div>`;
      cardTypeEl.textContent = title;
      cardBodyEl.innerHTML = rows;
      const covEl = cardBodyEl.querySelector("#pc-cov");
      const raysEl = cardBodyEl.querySelector("#pc-rays");
      const visEl = cardBodyEl.querySelector("#pc-vis");
      covEl.addEventListener("change", () => {
        setSrcOpt(s.channel, "coverage", covEl.checked);
        syncLayerChecks();
        draw();
      });
      raysEl.addEventListener("change", () => {
        setSrcOpt(s.channel, "rays", raysEl.checked);
        syncLayerChecks();
        draw();
      });
      visEl.addEventListener("change", () => {
        setSrcOpt(s.channel, "hidden", !visEl.checked);
        draw();
      });
      return;
    } else if (selected.type === "event") {
      const ev = findEventBySeq(selected.seq);
      if (!ev) { selected = null; hideCard(); return; }
      if (ev.kind === "measure") {
        title = "检测点 · ch" + ev.channel;
        const res = ev.result === "direction" ? "示向度 " + fmtN(ev.svd, 2) + "°"
          : ev.result === "near" ? "距离过近" : "无信号";
        rows = cardRow("虚拟时间", fmtN(ev.vt_s, 1) + " s") +
          cardRow("位置", fmtPosW(ev.pos)) +
          cardRow("结果", res) +
          (ev.result === "direction"
            ? cardRow("误差范围", "±" + fmtN(snapshot.svd_error_deg || 1, 1) + "°") : "");
      } else if (ev.kind === "clear") {
        title = "清除点 · ch" + ev.channel;
        rows = cardRow("虚拟时间", fmtN(ev.vt_s, 1) + " s") +
          cardRow("位置", fmtPosW(ev.pos)) +
          cardRow("结果", ev.result === "success" ? "清除成功" : "范围内无目标");
      } else {
        title = "被拒请求 · " + ((ev.path || "").replace("/", "") || "—");
        rows = cardRow("虚拟时间", fmtN(ev.vt_s, 1) + " s") +
          cardRow("位置", fmtPosW(ev.pos)) +
          cardRow("HTTP", ev.http_status != null ? ev.http_status : "—") +
          cardRow("原因", ev.detail || "rejected");
      }
    } else {
      const r = robotState();
      title = "机器狗";
      rows = cardRow("位置", fmtPosW([r.x, r.y])) +
        cardRow("朝向", fmtN(r.heading == null ? 0 : r.heading, 0) + "°") +
        cardRow("频道", currentChannel()) +
        cardRow("阶段", phaseText(snapshot.phase));
    }
    cardTypeEl.textContent = title;
    cardBodyEl.innerHTML = rows;
  }

  // 选中点在画布内的当前屏幕坐标
  function selectedScreenPos() {
    if (selected.type === "source") {
      const s = snapshot.sources && snapshot.sources[selected.idx];
      return s ? { x: sx(s.x), y: sy(s.y) } : null;
    }
    if (selected.type === "event") {
      const ev = findEventBySeq(selected.seq);
      return (ev && ev.pos) ? { x: sx(ev.pos[0]), y: sy(ev.pos[1]) } : null;
    }
    const r = robotState();
    return { x: sx(r.x), y: sy(r.y) };
  }

  function drawSelection() {
    if (!selected || !snapshot) return;
    const p = selectedScreenPos();
    if (!p) return;
    const px = p.x, py = p.y;
    // 实线亮环 + 呼吸脉冲（区别于覆盖范围的虚线圆）
    const t = performance.now() / 1000;
    const pulse = 0.5 + 0.5 * Math.sin(t * 3.2);
    ctx.save();
    ctx.strokeStyle = "#fff";
    ctx.shadowColor = "rgba(255,255,255,.9)";
    ctx.shadowBlur = 8 + 8 * pulse;
    ctx.lineWidth = 1.8;
    ctx.beginPath(); ctx.arc(px, py, 13, 0, Math.PI * 2); ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = "rgba(255,255,255," + (0.55 - 0.35 * pulse).toFixed(3) + ")";
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(px, py, 17 + 5 * pulse, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }

  // 选中期间的呼吸动画循环（节流约 30fps，无选中时自动停止）
  let selAnimId = null;
  let lastSelTick = 0;
  function selectionTick(ts) {
    if (!selected) { selAnimId = null; return; }
    if (!ts || ts - lastSelTick > 33) { lastSelTick = ts || 0; draw(); }
    selAnimId = requestAnimationFrame(selectionTick);
  }
  function startSelAnim() {
    if (selAnimId == null) selAnimId = requestAnimationFrame(selectionTick);
  }

  // ---------------- 侧栏 ----------------
  function updateStats(snap) {
    const el = document.getElementById("viz-stats");
    if (!snap) { el.innerHTML = "无进行中的测试"; return; }
    if (live) {
      const s = snap.stats || {};
      el.innerHTML = `
        阶段：<b>${phaseText(snap.phase)}</b><br>
        虚拟时间：<b>${(+snap.virtual_time_s).toFixed(1)} s</b><br>
        位置：<b>(${snap.robot.x.toFixed(0)}, ${snap.robot.y.toFixed(0)})</b><br>
        频道：<b>${snap.robot.channel}</b><br>
        检测：<b>${s.measures ?? 0}</b> 次<br>
        清除成功：<b>${s.clear_success ?? 0}</b> 次<br>
        已清除：<b>${s.cleared ?? 0}</b>${snap.truth_visible && snap.sources ? " / " + snap.sources.length + " 个" : ""}`;
    } else {
      const st = stateAt(playIdx - 1);
      el.innerHTML = `
        模式：<b>回放</b><br>
        虚拟时间：<b>${(+st.vt).toFixed(1)} s</b><br>
        位置：<b>(${st.pos.x.toFixed(0)}, ${st.pos.y.toFixed(0)})</b><br>
        频道：<b>${st.channel}</b><br>
        检测：<b>${st.measures}</b> 次<br>
        清除成功：<b>${st.clears}</b> 次<br>
        已清除频道：<b>${[...st.cleared].join(",") || "—"}</b>`;
    }
  }
  function phaseText(p) {
    return { preparing: "准备中", countdown: "倒计时", window: "等待 /enter", entered: "测试进行中", ended: "已结束" }[p] || p;
  }
  function buildLegend() {
    const el = document.getElementById("viz-legend");
    el.innerHTML = `
      <div><span class="swatch" style="background:#eceef2"></span>机器狗（当前频道标注于上方）</div>
      <div><span class="swatch" style="background:hsl(0,68%,64%)"></span>干扰源 · 色环按频道 ch1–ch20 循环</div>
      <div><span class="swatch" style="background:#46d68a"></span>✓ 已清除</div>
      <div><span class="swatch" style="background:rgba(255,255,255,.55)"></span>机器狗轨迹</div>
      <div>示向度射线（楔形 = ±误差角，按频道着色）</div>
      <div>虚线圆 = 全向检测范围；扇形 = 定向 ±90° 检测范围</div>
      <div>箭头 = 定向源朝向（独立于检测范围显示）</div>`;
  }

  // ---------------- 交互 ----------------
  canvas.addEventListener("mousedown", (e) => {
    dragging = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy };
    canvas.style.cursor = "grabbing";
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - dragging.x, dy = e.clientY - dragging.y;
    view.cx = dragging.cx - dx / view.scale;
    view.cy = dragging.cy + dy / view.scale;
    follow = false;
    draw();
  });
  window.addEventListener("mouseup", (e) => {
    if (dragging) {
      const dx = e.clientX - dragging.x, dy = e.clientY - dragging.y;
      // 位移极小视为点击（而非拖拽平移），进入点位命中检测
      if (dx * dx + dy * dy < 16 && e.target === canvas)
        canvasClick(e.offsetX, e.offsetY);
    }
    dragging = null;
    canvas.style.cursor = "grab";
  });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const mx = wx(e.offsetX), my = wy(e.offsetY);
    view.scale = Math.max(0.01, Math.min(20, view.scale * f));
    if (!follow) {  // 以鼠标为中心缩放
      view.cx = mx - (e.offsetX - W / 2) / view.scale;
      view.cy = my + (e.offsetY - H / 2) / view.scale;
    }
    draw();
  }, { passive: false });

  slider.addEventListener("input", () => {
    live = false;
    document.getElementById("viz-live").classList.remove("active");
    playIdx = +slider.value;
    draw(); updateStats(snapshot);
    updateSlider();
  });
  document.getElementById("viz-play").addEventListener("click", () => {
    if (playing) { stopPlay(); return; }
    live = false;
    document.getElementById("viz-live").classList.remove("active");
    startPlay();
  });
  document.getElementById("viz-live").addEventListener("click", () => {
    setLive(true);
    document.getElementById("viz-live").classList.add("active");
  });
  document.getElementById("viz-speed").addEventListener("change", (e) => {
    playSpeed = +e.target.value;
    if (playing) { stopPlay(); startPlay(); }
  });
  document.getElementById("viz-fit").addEventListener("click", () => { fitAll(); draw(); });
  document.getElementById("viz-follow").addEventListener("click", followRobot);
  document.getElementById("viz-zoom-in").addEventListener("click", () => {
    view.scale = Math.min(20, view.scale * 1.3); draw();
  });
  document.getElementById("viz-zoom-out").addEventListener("click", () => {
    view.scale = Math.max(0.01, view.scale / 1.3); draw();
  });
  document.getElementById("vc-truth").addEventListener("change", (e) => { opts.truth = e.target.checked; draw(); });
  document.getElementById("vc-coverage").addEventListener("change", (e) => {
    opts.coverage = e.target.checked;
    applyLayerToAll("coverage", opts.coverage);
  });
  document.getElementById("vc-rays").addEventListener("change", (e) => {
    opts.rays = e.target.checked;
    applyLayerToAll("rays", opts.rays);
  });
  document.getElementById("vc-trail").addEventListener("change", (e) => { opts.trail = e.target.checked; draw(); });
  document.getElementById("vc-grid").addEventListener("change", (e) => { opts.grid = e.target.checked; draw(); });

  window.addEventListener("resize", resize);
  document.getElementById("viz-live").classList.add("active");
  resize();
  buildLegend();

  return { reset, push, setSnapshot, setLive, draw, resize };
})();
