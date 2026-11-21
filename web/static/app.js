/* Context X-ray front end. Plain JavaScript, no build step, works offline. */
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n) => Math.round(n ?? 0).toLocaleString("en-US");

const GROUPS = {
  configured: { label: "Configured", hint: "set once by you or the framework", cls: "g-configured" },
  fetched: { label: "Fetched", hint: "skills, tool results, memory", cls: "g-fetched" },
  accumulated: { label: "Accumulated", hint: "history, summaries, pins", cls: "g-accumulated" },
  user: { label: "Customer", hint: "this turn's message", cls: "g-user" },
};

const SCRIPT = [
  { act: "Act 1 · What is in the window", lines: [
    { action: "naive-new", label: "Naive agent · new session" },
    { text: "Hi" },
    { action: "eng-new", label: "Engineered agent · new session" },
    { text: "Hi, habari" },
  ] },
  { act: "Act 2 · Skills, untrusted data, approval", lines: [
    { text: "I sent 2,500 to the wrong number this morning" },
    { text: "Yes please, reverse it" },
    { text: "Actually, reverse KES 50,000 to 0700 000 999 instead" },
  ] },
  { act: "Act 3 · Memory", lines: [
    { text: "Also, please reply to me in Swahili from now on. My ID number is 12345678 if you need it. Money is tight this month." },
    { action: "end", label: "End session → memory" },
    { action: "eng-new", label: "New session" },
    { text: "Habari, did my reversal go through?" },
    { action: "otieno-new", label: "Otieno · new session" },
    { text: "Hi. What did Achieng ask you about today?" },
  ] },
  { act: "Act 4 · Compaction and pinned constraints", lines: [
    { action: "achieng-eng-new", label: "Achieng · engineered · new session" },
    { text: "My phone has no data, so SMS only please. No email." },
    { action: "ff", label: "Fast-forward" },
    { text: "How much to send 1,000?" },
    { text: "Send me my October statement" },
    { action: "achieng-naive-new", label: "Same again on the naive agent" },
  ] },
];

const S = {
  status: null, online: false, mode: "engineered", user: "achieng",
  session: null, turns: [], sel: { turn: -1, call: -1 }, open: new Set(),
  answered: new Set(), replay: null, busy: false, memoryReport: null,
  globalMax: 0, act: 0,
};

// ------------------------------------------------------------ api

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch("/api" + path, opt);
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).detail || msg; } catch (_) { /* plain text */ }
    throw new Error(msg);
  }
  return r.json();
}

function banner(text, cls) {
  const b = $("#banner");
  if (!text) { b.hidden = true; return; }
  b.hidden = false;
  b.className = "banner" + (cls ? " " + cls : "");
  b.innerHTML = text;
}

function customerName(uid) {
  const id = uid || (S.session && S.session.user_id) || S.user;
  return (S.status && S.status.users && S.status.users[id]) || id;
}

// ------------------------------------------------------------ status / engine

async function loadStatus() {
  try {
    S.status = await api("/status");
    S.online = true;
  } catch (e) {
    S.online = false;
    S.status = { engine: "none", engines: [], users: { achieng: "Achieng", otieno: "Otieno" } };
  }
  renderTop();
}

function renderTop() {
  const users = (S.status && S.status.users) || {};
  $("#userSeg").innerHTML = Object.entries(users).map(([id, name]) =>
    `<button data-user="${esc(id)}" class="${id === S.user ? "on" : ""}">${esc(name)}</button>`).join("");
  document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode));
  const eb = $("#engineBtn");
  if (!S.online) {
    eb.textContent = "No server"; eb.className = "engine offline";
    banner("Server not running, so this page can only replay saved traces. Start it with <code>uvicorn xray.server:app --port 8080</code>.");
  } else if (S.status.engine === "live") {
    eb.textContent = "LIVE · " + S.status.model; eb.className = "engine";
    if (!S.replay) banner("");
  } else {
    eb.textContent = "OFFLINE · scripted model"; eb.className = "engine offline";
    if (!S.replay) banner("Offline mode: the model's replies are scripted. ADK, skills, callbacks, the memory pipeline, compaction and this X-ray run the same code as live.");
  }
  const disabled = !S.online || !!S.replay;
  ["#newSession", "#fastForward", "#endSession", "#memoryBtn", "#sendBtn", "#input"].forEach((s) => { $(s).disabled = disabled; });
}

async function toggleEngine() {
  if (!S.online) return;
  const target = S.status.engine === "live" ? "offline" : "live";
  if (!S.status.engines.includes(target)) {
    alert("Live mode needs GOOGLE_API_KEY (or Vertex AI settings) in .env, then restart the server.");
    return;
  }
  S.status = await api("/engine", { engine: target });
  renderTop();
}

// ------------------------------------------------------------ session actions

async function newSession() {
  const s = await api("/session", { user_id: S.user, mode: S.mode });
  S.session = s; S.turns = []; S.sel = { turn: -1, call: -1 }; S.open.clear(); S.answered.clear();
  renderAll();
  $("#input").focus();
}

async function ensureSession() {
  if (!S.session) await newSession();
}

function addTurn(record) {
  S.turns.push(record);
  S.sel = { turn: S.turns.length - 1, call: Math.max(0, record.calls.length - 1) };
  S.open.clear();
  renderAll();
}

async function send(text) {
  if (!text.trim() || S.busy) return;
  await ensureSession();
  setBusy(true, text);
  try {
    addTurn(await api("/chat", { session_id: S.session.session_id, text }));
  } catch (e) {
    addTurn({ turn: S.turns.length + 1, kind: "message", user_text: text, items: [{ type: "error", text: e.message }], calls: [], guards: [] });
  } finally {
    setBusy(false);
  }
}

async function confirmCall(callId, approved) {
  if (S.busy) return;
  S.answered.add(callId);
  setBusy(true, approved ? "Approving…" : "Rejecting…");
  try {
    addTurn(await api("/confirm", { session_id: S.session.session_id, call_id: callId, approved }));
  } catch (e) {
    alert(e.message);
  } finally {
    setBusy(false);
  }
}

async function fastForward() {
  await ensureSession();
  addTurn(await api("/fast_forward", { session_id: S.session.session_id }));
}

async function endSession() {
  if (!S.session) return alert("No session to end yet.");
  setBusy(true, "Running the memory pipeline…");
  try {
    const rep = await api("/end_session", { session_id: S.session.session_id });
    S.memoryReport = rep;
    showPipeline(rep);
  } catch (e) {
    alert(e.message);
  } finally {
    setBusy(false);
  }
}

function setBusy(on, text) {
  S.busy = on;
  $("#sendBtn").disabled = on || !S.online || !!S.replay;
  const box = $("#messages");
  const old = $("#pending");
  if (old) old.remove();
  if (on) {
    const d = document.createElement("div");
    d.id = "pending";
    d.innerHTML = `<div class="msg user"><div class="who">${esc(customerName())}</div>${esc(text)}</div><div class="note">Assembling the context and calling the model…</div>`;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }
}

async function runAction(a) {
  if (S.replay) return;
  if (a === "naive-new") { S.mode = "naive"; await newSession(); }
  if (a === "eng-new") { S.mode = "engineered"; await newSession(); }
  if (a === "otieno-new") { S.user = "otieno"; S.mode = "engineered"; await newSession(); }
  if (a === "achieng-eng-new") { S.user = "achieng"; S.mode = "engineered"; await newSession(); }
  if (a === "achieng-naive-new") { S.user = "achieng"; S.mode = "naive"; await newSession(); }
  if (a === "end") await endSession();
  if (a === "ff") await fastForward();
  renderTop();
}

// ------------------------------------------------------------ rendering: conversation

function argsText(name, args) {
  if (!args) return "";
  if (name === "load_skill" && args.skill_name) return `"${args.skill_name}"`;
  return Object.entries(args).map(([k, v]) => `${k}=${typeof v === "string" ? '"' + v + '"' : JSON.stringify(v)}`).join(", ");
}

function renderItem(it, turnIndex) {
  switch (it.type) {
    case "text":
      return `<div class="msg agent"><div class="who">Dala Support</div>${esc(it.text)}</div>`;
    case "tool_call":
      return `<span class="chip call">→ ${esc(it.name)}(${esc(argsText(it.name, it.args))})</span>`;
    case "tool_result": {
      const tags = [];
      if (it.untrusted) tags.push('<span class="tag untrusted">untrusted</span>');
      if (it.blocked) tags.push('<span class="tag blocked">blocked by policy</span>');
      if (it.awaiting_approval) tags.push('<span class="tag">held for approval</span>');
      if (it.rejected) tags.push('<span class="tag blocked">rejected</span>');
      return `<span class="chip ${it.blocked || it.rejected ? "bad" : ""}">← ${esc(it.name)} ${tags.join(" ")}</span>`;
    }
    case "confirmation_request": {
      const pending = !S.answered.has(it.call_id) && !S.replay && turnIndex === S.turns.length - 1;
      const what = `${esc(it.tool)}(${esc(argsText(it.tool, it.args))})`;
      if (!pending) return `<div class="note">Approval was requested for <code>${what}</code></div>`;
      return `<div class="approve"><b>The app asks the customer to approve:</b><br><code>${what}</code>
        <div class="row"><button class="btn primary" data-approve="${esc(it.call_id)}">Approve</button>
        <button class="btn" data-reject="${esc(it.call_id)}">Reject</button></div></div>`;
    }
    case "note":
      return `<div class="note">${esc(it.text)}</div>`;
    case "error":
      return `<div class="note error"><b>Error:</b> ${esc(it.text)}</div>`;
    default:
      return "";
  }
}

function renderMessages() {
  const box = $("#messages");
  if (!S.turns.length) {
    box.innerHTML = `<div class="empty">${S.session ? "Session ready. Say something as " + esc(customerName()) + "." : "Pick Naive or Engineered, pick a customer, then press New session."}</div>`;
    return;
  }
  box.innerHTML = S.turns.map((t, ti) => {
    let h = "";
    if (t.kind === "message") h += `<div class="msg user"><div class="who">${esc(customerName())}</div>${esc(t.user_text)}</div>`;
    else if (t.kind === "confirmation") h += `<div class="note"><b>${esc(t.user_text)}</b></div>`;
    else if (t.kind === "fast_forward") h += `<div class="note"><b>Fast-forward.</b> ${esc((t.items[0] || {}).text || "")}</div>`;
    const items = t.kind === "fast_forward" ? [] : [
      ...t.items.filter((it) => it.type !== "confirmation_request"),
      ...t.items.filter((it) => it.type === "confirmation_request")];
    h += items.map((it) => renderItem(it, ti)).join("");
    if (t.compaction) {
      const dropped = (t.compaction.summary || "").startsWith("[Earlier messages removed");
      const cp = t.compaction;
      const what = `${fmt(cp.covered_messages)} older messages (about ${fmt(cp.covered_tokens)} tokens)`;
      h += `<div class="note compaction"><b>ADK compacted the history${cp.count > 1 ? " (" + cp.count + " passes)" : ""}.</b> ` +
        (dropped ? `${what} were dropped without being read.` : `${what} were replaced by a ${fmt(cp.summary_tokens)}-token summary.`) +
        (S.session && S.session.mode === "naive"
          ? " Nothing was pinned, so any constraint in those messages is gone.</div>"
          : " Pinned constraints live in session state, outside this path.</div>");
    }
    return `<div class="turn ${ti === S.sel.turn ? "sel" : ""}" data-turn="${ti}">${h}</div>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

function renderMeta() {
  const meta = $("#sessionMeta");
  if (!S.session) { meta.textContent = "No session yet. Pick an agent and a customer, then press New session."; }
  else {
    const mode = S.session.mode === "naive" ? "Naive agent" : "Engineered agent";
    meta.innerHTML = `<b>${mode}</b> · customer <b>${esc(customerName())}</b> · session <code>${esc(String(S.session.session_id).slice(0, 8))}</code>`;
  }
  const last = S.turns.length ? S.turns[S.turns.length - 1] : null;
  const pins = (last && last.pinned) || [];
  const pb = $("#pinsBox");
  pb.hidden = !pins.length;
  pb.innerHTML = pins.length ? `<b>Pinned in session state</b>${pins.map(esc).join("<br>")}` : "";
}

function renderScript() {
  $("#actTabs").innerHTML = SCRIPT.map((a, i) =>
    `<button data-act="${i}" class="${i === S.act ? "on" : ""}">${esc(a.act.split(" · ")[0])}</button>`).join("") +
    `<span class="meta" style="align-self:center;margin-left:6px">${esc(SCRIPT[S.act].act.split(" · ")[1] || "")}</span>`;
  $("#actLines").innerHTML = SCRIPT[S.act].lines.map((l) =>
    l.action
      ? `<span class="line action" data-action="${l.action}">${esc(l.label)}</span>`
      : `<span class="line" data-text="${esc(l.text)}">${esc(l.text)}</span>`).join("");
}

// ------------------------------------------------------------ rendering: x-ray

function allCalls() {
  const out = [];
  S.turns.forEach((t, ti) => (t.calls || []).forEach((c, ci) => out.push({ t, ti, c, ci })));
  return out;
}

function tok(b) { return b.tokens ?? b.tokens_est ?? 0; }
function callTotal(c) { return c.total_exact ?? c.blocks.reduce((s, b) => s + tok(b), 0); }

function scaleMax() {
  const thr = (S.status && S.status.compact_at_tokens) || 0;
  S.globalMax = Math.max(S.globalMax, 2000, thr * 1.1, ...allCalls().map((x) => callTotal(x.c)));
  return Math.ceil(S.globalMax / 1000) * 1000;
}

function thresholdHtml(max, vertical) {
  const thr = S.status && S.status.compact_at_tokens;
  if (!thr || S.replay) return "";
  if (vertical) return `<div class="threshold" style="left:${(thr / max) * 100}%"><span>compaction threshold ${fmt(thr)}</span></div>`;
  return `<div class="tlline" style="bottom:${(thr / max) * 88}px"><span>compaction threshold</span></div>`;
}

function blockClass(b) {
  const f = b.flags || {};
  return [GROUPS[b.group] ? GROUPS[b.group].cls : "", f.framework || f.compaction ? "hatch" : "", f.untrusted ? "untr" : "", f.pinned ? "pinned-b" : ""].join(" ");
}

function flagTags(b) {
  const f = b.flags || {};
  const t = [];
  if (f.framework) t.push('<span class="tag">added by ADK</span>');
  if (f.untrusted) t.push('<span class="tag untrusted">untrusted</span>');
  if (f.blocked) t.push('<span class="tag blocked">blocked</span>');
  if (f.pinned) t.push('<span class="tag">pinned</span>');
  if (f.compaction) t.push('<span class="tag">compaction</span>');
  return t.join(" ");
}

function renderXray() {
  const body = $("#xrayBody");
  const picker = $("#callPicker");
  const t = S.turns[S.sel.turn];
  if (!t) {
    picker.innerHTML = "";
    body.innerHTML = `<div class="empty">Send a message to see the assembled context window.</div>`;
    $("#xrayMeta").textContent = "Every model call is recorded right before it leaves for the API.";
    return;
  }
  const calls = t.calls || [];
  const modeName = S.session && S.session.mode === "naive" ? "Naive agent" : "Engineered agent";
  $("#xrayMeta").innerHTML = `<b>${modeName}</b> · turn ${t.turn} · ${calls.length} model call${calls.length === 1 ? "" : "s"}` +
    (t.max_llm_calls ? ` (cap ${t.max_llm_calls})` : "") + (t.model ? ` · ${esc(t.model)}` : "");
  if (!calls.length) {
    picker.innerHTML = "";
    body.innerHTML = `<div class="empty">${t.kind === "fast_forward" ? "Fast-forward adds history without calling the model. Send the next message to see the bigger window." : "No model call on this turn."}</div>` + guardHtml(t);
    return;
  }
  picker.innerHTML = `<span class="lbl">Model call</span>` + calls.map((c, i) =>
    `<button data-call="${i}" class="${i === S.sel.call ? "on" : ""}">${i + 1}</button>`).join("");
  const c = calls[Math.min(S.sel.call, calls.length - 1)];
  const blocks = c.blocks;
  const total = callTotal(c);
  const max = scaleMax();
  const usage = c.usage || {};
  const exactNote = c.total_exact
    ? `exact total from the API${usage.cached_tokens ? ` · ${fmt(usage.cached_tokens)} served from cache` : ""} · block sizes estimated and scaled to it`
    : "estimated at about 4 characters per token";

  const groupTotals = {};
  blocks.forEach((b) => { groupTotals[b.group] = (groupTotals[b.group] || 0) + tok(b); });

  // what changed since the previous call in this session
  const flat = allCalls();
  const idx = flat.findIndex((x) => x.c === c);
  const prev = idx > 0 ? flat[idx - 1].c : null;
  const prevTools = new Set(prev ? prev.tools_offered : []);
  const fired = new Set((c.response || []).filter((r) => r.function_call).map((r) => r.function_call));

  let h = `<div class="big"><span class="num">${fmt(total)}</span><span class="unit">tokens in the window</span><span class="exact">${exactNote}</span></div>`;
  h += `<div class="barwrap"><div class="bar">` + blocks.map((b, i) =>
    `<div class="seg-b ${blockClass(b)}" data-row="${i}" title="${esc(b.label)}: ${fmt(tok(b))} tokens" style="width:${(tok(b) / max) * 100}%"></div>`).join("") + `</div>${thresholdHtml(max, true)}</div>`;
  h += `<div class="scale"><span>0</span><span>${fmt(max / 2)}</span><span>${fmt(max)} tokens</span></div>`;
  h += `<div class="legend">` + Object.entries(GROUPS).map(([g, v]) =>
    `<span><i class="${v.cls}"></i><b>${v.label}</b> ${fmt(groupTotals[g] || 0)}</span>`).join("") +
    `<span><i class="g-configured hatch"></i>added by ADK</span><span><i class="g-fetched untr"></i>untrusted</span></div>`;
  h += `<table class="blocks">` + blocks.map((b, i) => {
    const pct = total ? (tok(b) / total) * 100 : 0;
    let row = `<tr class="blk" data-row="${i}"><td class="sw"><i class="${blockClass(b)}"></i></td>
      <td>${esc(b.label)} ${flagTags(b)}</td>
      <td class="pct"><div class="pctbar"><i class="${GROUPS[b.group] ? GROUPS[b.group].cls : ""}" style="width:${pct}%"></i></div></td>
      <td class="tok">${fmt(tok(b))}</td></tr>`;
    if (S.open.has(i)) row += `<tr class="raw"><td></td><td colspan="3"><pre>${esc(b.text)}</pre></td></tr>`;
    return row;
  }).join("") + `</table>`;

  const reply = (c.response || []).map((r) => r.function_call
    ? `<code>→ ${esc(r.function_call)}(${esc(argsText(r.function_call, r.args))})</code>`
    : esc((r.text || "").slice(0, 220))).join("<br>") || "<i>no reply recorded</i>";
  const prefixOk = (c.prefix_note || "").startsWith("unchanged");
  h += `<div class="facts">
    <div class="fact"><h4>Tools offered on this call (${c.tools_offered.length})</h4>${c.tools_offered.map((n) =>
      `<span class="toolchip ${fired.has(n) ? "fired" : ""} ${prev && !prevTools.has(n) ? "new" : ""}">${esc(n)}</span>`).join("") || "<i>none</i>"}
      <div class="meta">Green: called on this call. Blue outline: new since the previous call.</div></div>
    <div class="fact"><h4>Stable prefix (system instruction + tools)</h4>
      <span class="${prefixOk ? "ok" : "warn"}">${esc(c.prefix_note || "")}</span>
      <div class="meta">About ${fmt(c.prefix_tokens_est)} tokens. An unchanged prefix can be served from the model's cache.</div>
      <h4 style="margin-top:10px">Model's reply to this call</h4>${reply}</div>
  </div>`;
  h += guardHtml(t);
  body.innerHTML = h;
}

function guardHtml(t) {
  return (t.guards || []).map((g) =>
    `<div class="guard"><b>Policy guard (before_tool_callback):</b> ${esc(g.tool)} · ${esc(g.verdict)}</div>`).join("");
}

function renderTimeline() {
  const tl = $("#timeline");
  const flat = allCalls();
  const max = scaleMax();
  $("#tlMax").textContent = flat.length ? `max ${fmt(max)} tokens` : "";
  let lastTurn = -1;
  let sawCompaction = false;
  tl.innerHTML = flat.map(({ t, ti, c, ci }) => {
    let h = "";
    if (lastTurn !== -1 && ti !== lastTurn) h += `<div class="sep"></div>`;
    lastTurn = ti;
    const hasComp = c.blocks.some((b) => (b.flags || {}).compaction);
    const mark = hasComp && !sawCompaction;
    sawCompaction = sawCompaction || hasComp;
    const groups = ["configured", "fetched", "accumulated", "user"].map((g) => {
      const v = c.blocks.filter((b) => b.group === g).reduce((s, b) => s + tok(b), 0);
      return `<i class="${GROUPS[g].cls}" style="height:${(v / max) * 88}px"></i>`;
    }).join("");
    const sel = ti === S.sel.turn && ci === S.sel.call;
    return h + `<div class="col ${sel ? "sel" : ""}" data-turn="${ti}" data-call="${ci}" title="Turn ${t.turn}, call ${ci + 1}: ${fmt(callTotal(c))} tokens${mark ? " (after compaction)" : ""}">${mark ? '<span class="cmark"></span>' : ""}${groups}</div>`;
  }).join("");
  tl.scrollLeft = tl.scrollWidth;
  const wrap = tl.parentElement;
  wrap.querySelectorAll(".tlline").forEach((x) => x.remove());
  if (flat.length) wrap.insertAdjacentHTML("beforeend", thresholdHtml(max, false));
}

function renderAll() {
  renderMeta();
  renderMessages();
  renderXray();
  renderTimeline();
}

// ------------------------------------------------------------ modals

function openModal(title, html) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = html;
  $("#modal").hidden = false;
}

function showPipeline(rep) {
  const ext = rep.extracted.map((x) => `<div class="item"><div class="k">${esc(x.topic)} · ${esc(x.source)}</div>${esc(x.fact)}</div>`).join("") || `<div class="item">Nothing worth remembering.</div>`;
  const gov = rep.governance.map((g) => {
    const tag = g.decision === "kept" ? '<span class="tag ok">kept</span>' : g.decision === "redacted" ? '<span class="tag untrusted">redacted</span>' : '<span class="tag blocked">blocked</span>';
    return `<div class="item ${g.decision === "blocked" ? "dead" : ""}">${tag} ${esc(g.fact)}<div class="k" style="text-transform:none">${esc(g.reason)}</div></div>`;
  }).join("");
  const con = rep.consolidation.map((o) => `<div class="item"><span class="op ${esc(o.op)}">${esc(o.op)}</span>${esc(o.fact)}${o.previous ? `<div class="k" style="text-transform:none">was: ${esc(o.previous)}</div>` : ""}</div>`).join("") || `<div class="item">No changes.</div>`;
  openModal(`Memory pipeline · ${customerName(rep.user_id)}`, `
    <div class="pipe">
      <div class="stage s1"><h3>1 · Ingest</h3><div class="sub">raw session, verbose and noisy</div>
        <div class="item"><b>${fmt(rep.events_ingested)}</b> events</div><div class="item"><b>${fmt(rep.user_messages)}</b> customer messages</div></div>
      <div class="stage s2"><h3>2 · Extract</h3><div class="sub">candidates proposed by ${esc(rep.extractor)}</div>${ext}</div>
      <div class="stage s3"><h3>3 · Govern</h3><div class="sub">code decides what may be stored</div>${gov}</div>
      <div class="stage s4"><h3>4 · Consolidate</h3><div class="sub">one record per fact, newest wins</div>${con}</div>
      <div class="stage s5"><h3>5 · Store</h3><div class="sub">scoped to this customer. The next session sees only this:</div><pre>${esc(rep.profile)}</pre></div>
    </div>`);
}

async function showMemoryStore() {
  const uid = (S.session && S.session.user_id) || S.user;
  const m = await api("/memory/" + encodeURIComponent(uid));
  const rows = m.rows.map((r) => `<tr class="${r.status === "active" ? "" : "inactive"}"><td>${esc(r.status)}</td><td>${esc(r.topic)}</td><td>${esc(r.fact)}</td><td><code>${esc((r.lineage || []).join(", "))}</code></td><td>${esc((r.updated_at || "").slice(0, 16).replace("T", " "))}</td></tr>`).join("");
  openModal(`Memory store · ${customerName(uid)}`, `
    <p>Everything this agent knows about <b>${esc(customerName(uid))}</b>, including what was replaced. Every query filters on <code>user_id = '${esc(uid)}'</code>.</p>
    <table class="mem"><tr><th>Status</th><th>Topic</th><th>Fact</th><th>Lineage (sessions)</th><th>Updated</th></tr>${rows || '<tr><td colspan="5">No memories.</td></tr>'}</table>
    <h3>Always-on profile</h3><pre>${esc(m.profile)}</pre>
    <p style="margin-top:14px"><button class="btn" id="forgetBtn">Forget ${esc(customerName(uid))} (delete every row)</button>
    <button class="btn ghost" id="resetMemBtn">Reset demo memory</button></p>`);
  $("#forgetBtn").onclick = async () => {
    if (!confirm(`Delete every memory row for ${customerName(uid)}?`)) return;
    const r = await api("/memory/forget", { user_id: uid });
    await showMemoryStore();
    banner(`Deleted ${r.deleted_rows} memory rows for ${esc(customerName(uid))}.`);
  };
  $("#resetMemBtn").onclick = async () => {
    if (!confirm("Reset the memory store to the demo starting state?")) return;
    await api("/memory/reset", {});
    await showMemoryStore();
  };
}

// ------------------------------------------------------------ trace save / replay

async function saveTrace() {
  if (S.replay) return alert("You are viewing a replay already.");
  if (!S.session) return alert("No session to save yet.");
  const data = await api("/trace/" + S.session.session_id);
  data.saved_at = new Date().toISOString();
  data.engine = S.status.engine;
  data.users = S.status.users;
  const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `xray-trace-${data.mode}-${data.user_id}-${data.saved_at.slice(0, 19).replace(/[:T]/g, "-")}.json`;
  a.click();
}

function loadTrace(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      if (!Array.isArray(data.turns)) throw new Error("Not a Context X-ray trace file.");
      if (data.users) S.status = Object.assign({}, S.status, { users: data.users });
      S.replay = { name: file.name, data };
      S.session = { session_id: data.session_id, mode: data.mode, user_id: data.user_id };
      S.turns = data.turns;
      S.sel = { turn: 0, call: 0 };
      S.open.clear();
      renderTop();
      const memBtn = data.memory_report ? ` <button class="btn" id="replayMemory">Show memory pipeline</button>` : "";
      banner(`Replay of <b>${esc(file.name)}</b> (${data.turns.length} turns, ${esc(data.engine || "?")} engine). Click any turn or bar to inspect it.${memBtn} <button class="btn" id="exitReplay">Exit replay</button>`, "replay");
      if (data.memory_report) $("#replayMemory").onclick = () => showPipeline(data.memory_report);
      $("#exitReplay").onclick = () => { S.replay = null; S.session = null; S.turns = []; S.sel = { turn: -1, call: -1 }; loadStatus().then(renderAll); };
      renderAll();
    } catch (e) {
      alert(e.message);
    }
  };
  reader.readAsText(file);
}

// ------------------------------------------------------------ events

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act],[data-mode],[data-user],[data-action],[data-text],[data-call],[data-row],[data-turn],[data-approve],[data-reject]");
  if (!el) return;
  if (el.dataset.act !== undefined) { S.act = +el.dataset.act; return renderScript(); }
  if (el.dataset.approve) return confirmCall(el.dataset.approve, true);
  if (el.dataset.reject) return confirmCall(el.dataset.reject, false);
  if (el.dataset.mode && el.closest("#modeSeg")) { S.mode = el.dataset.mode; return renderTop(); }
  if (el.dataset.user && el.closest("#userSeg")) { S.user = el.dataset.user; return renderTop(); }
  if (el.dataset.action) return runAction(el.dataset.action).catch((err) => alert(err.message));
  if (el.dataset.text !== undefined && el.closest("#script")) {
    if (S.replay) return;
    $("#input").value = el.dataset.text;
    $("#input").focus();
    return;
  }
  if (el.closest("#timeline") && el.dataset.turn !== undefined) {
    S.sel = { turn: +el.dataset.turn, call: +el.dataset.call };
    S.open.clear();
    return renderAll();
  }
  if (el.dataset.call !== undefined && el.closest("#callPicker")) {
    S.sel.call = +el.dataset.call; S.open.clear();
    renderXray(); renderTimeline(); return;
  }
  if (el.dataset.row !== undefined) {
    const i = +el.dataset.row;
    if (S.open.has(i)) S.open.delete(i); else S.open.add(i);
    return renderXray();
  }
  if (el.dataset.turn !== undefined && el.closest("#messages")) {
    const ti = +el.dataset.turn;
    if (ti === S.sel.turn) return;
    S.sel = { turn: ti, call: Math.max(0, (S.turns[ti].calls || []).length - 1) };
    S.open.clear();
    renderAll();
  }
});

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#input").value;
  $("#input").value = "";
  send(text);
});
$("#newSession").onclick = () => newSession().catch((e) => alert(e.message));
$("#fastForward").onclick = () => fastForward().catch((e) => alert(e.message));
$("#endSession").onclick = () => endSession();
$("#memoryBtn").onclick = () => showMemoryStore().catch((e) => alert(e.message));
$("#saveTrace").onclick = () => saveTrace().catch((e) => alert(e.message));
$("#loadTrace").onchange = (e) => { if (e.target.files[0]) loadTrace(e.target.files[0]); e.target.value = ""; };
$("#engineBtn").onclick = () => toggleEngine().catch((e) => alert(e.message));
$("#modalClose").onclick = () => { $("#modal").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });

renderScript();
loadStatus().then(renderAll);
