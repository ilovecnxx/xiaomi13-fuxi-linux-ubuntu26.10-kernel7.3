/* SuperTerm UI logic. Bridges to Python via window.superterm.call(method, params). */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const call = (m, p) => window.superterm.call(m, p);

  const state = {
    sessionId: null,
    provider: "codex",
    workspace: "",
    sessions: [],
    bootState: null,
    streaming: false,
    streamEl: null,      // current assistant bubble being streamed
    streamText: "",
    toolEls: {},         // call_id -> element
    evCursor: 0,
    seenSeq: new Set(),
  };

  // ---------- helpers ----------
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function toast(msg, ms) {
    const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms || 2200);
  }
  function scrollBottom() { const m = $("#messages"); m.scrollTop = m.scrollHeight; }

  // ---------- rendering events ----------
  function clearMessages() { $("#messages").innerHTML = ""; state.seenSeq.clear(); state.evCursor = 0; }

  function renderEvent(ev) {
    if (state.seenSeq.has(ev.seq)) return;
    state.seenSeq.add(ev.seq);
    if (ev.seq > state.evCursor) state.evCursor = ev.seq;
    const d = ev.data || {};
    switch (ev.kind) {
      case "message": return renderMessage(d);
      case "tool_call": return renderToolCall(d);
      case "tool_result": return renderToolResult(d);
      case "diff": return renderDiff(d);
      case "error": return renderError(d);
      case "usage": return renderUsage(d);
      case "lifecycle": return renderLifecycle(d);
      case "done": return renderDone(d);
      case "approval_request": return renderApproval(d);
      default: return;
    }
  }

  function renderMessage(d) {
    const role = d.role || "assistant";
    if (role === "assistant" && !d.delta) {
      // If a streaming bubble is open, finalize it.
      if (state.streamEl) { state.streamEl = null; state.streamText = ""; }
      const m = el("div", "msg assistant");
      m.appendChild(el("div", "bubble", d.text || ""));
      $("#messages").appendChild(m);
      scrollBottom();
      return;
    }
    if (role === "assistant" && d.delta) {
      if (!state.streamEl) {
        const m = el("div", "msg assistant");
        state.streamEl = el("div", "bubble", "");
        m.appendChild(state.streamEl);
        $("#messages").appendChild(m);
      }
      state.streamText += (d.text || "");
      state.streamEl.textContent = state.streamText;
      scrollBottom();
      return;
    }
    // user / reasoning / system
    const m = el("div", "msg " + role);
    if (role === "reasoning") m.appendChild(el("div", "role-label", "思考"));
    m.appendChild(el("div", "bubble", d.text || ""));
    $("#messages").appendChild(m);
    scrollBottom();
  }

  function renderToolCall(d) {
    const card = el("div", "tool-card");
    card.dataset.callId = d.call_id;
    const head = el("div", "tool-head");
    head.appendChild(el("span", "tool-name", "⚙ " + (d.name || "tool")));
    const status = el("span", "tool-status", "运行中…");
    head.appendChild(status);
    card.appendChild(head);
    const body = el("div", "tool-body");
    body.textContent = summarizeArgs(d.arguments);
    card.appendChild(body);
    head.addEventListener("click", () => card.classList.toggle("open"));
    state.toolEls[d.call_id] = { card, status, body };
    $("#messages").appendChild(card);
    scrollBottom();
  }

  function renderToolResult(d) {
    const ref = state.toolEls[d.call_id];
    const out = typeof d.output === "string" ? d.output : JSON.stringify(d.output);
    const isErr = d.is_error || (d.exit_code != null && d.exit_code !== 0);
    if (ref) {
      ref.status.textContent = isErr ? ("✗ " + (d.exit_code != null ? d.exit_code : "错误")) : "✓";
      ref.status.className = "tool-status " + (isErr ? "err" : "ok");
      ref.body.textContent = (out || "(无输出)");
      if (isErr) ref.card.classList.add("open");
    } else {
      const card = el("div", "tool-card open");
      const head = el("div", "tool-head");
      head.appendChild(el("span", "tool-name", "⚙ 结果"));
      head.appendChild(el("span", "tool-status " + (isErr ? "err" : "ok"), isErr ? "✗" : "✓"));
      card.appendChild(head);
      const body = el("div", "tool-body"); body.textContent = out || "(无输出)";
      card.appendChild(body);
      $("#messages").appendChild(card);
    }
    scrollBottom();
  }

  function renderDiff(d) {
    const wrap = el("div", "diff");
    wrap.appendChild(el("div", "path", "📝 " + (d.path || "(文件)")));
    const patch = d.patch || "";
    patch.split("\n").forEach((ln) => {
      let cls = "ln";
      if (ln.startsWith("+") && !ln.startsWith("+++")) cls += " add";
      else if (ln.startsWith("-") && !ln.startsWith("---")) cls += " del";
      wrap.appendChild(el("div", cls, ln));
    });
    $("#messages").appendChild(wrap);
    scrollBottom();
  }

  function renderError(d) {
    const m = el("div", "msg system");
    m.appendChild(el("div", "bubble", "⚠ " + (d.message || "错误")));
    $("#messages").appendChild(m);
    scrollBottom();
  }

  function renderUsage(d) {
    const parts = [];
    if (d.input_tokens != null) parts.push("in " + d.input_tokens);
    if (d.output_tokens != null) parts.push("out " + d.output_tokens);
    if (!parts.length) return;
    const u = el("div", "usage", parts.join(" · "));
    $("#messages").appendChild(u);
    scrollBottom();
  }

  function renderLifecycle(d) {
    if (d.state === "process_exited") setStreaming(false);
  }

  function renderDone(d) {
    setStreaming(false);
    if (state.streamEl) { state.streamEl = null; state.streamText = ""; }
  }

  function renderApproval(d) {
    const card = el("div", "tool-card open");
    const head = el("div", "tool-head");
    head.appendChild(el("span", "tool-name", "需要确认: " + (d.description || d.action || "")));
    card.appendChild(head);
    const body = el("div", "tool-body");
    body.appendChild(el("p", "error", "当前驱动无法提交交互式审批。此请求未获处理。"));
    card.appendChild(body);
    $("#messages").appendChild(card);
    scrollBottom();
  }

  function summarizeArgs(a) {
    if (a == null) return "";
    if (typeof a === "string") return a;
    if (a.command) return "$ " + a.command;
    return JSON.stringify(a, null, 2);
  }

  // ---------- streaming state ----------
  function setStreaming(on) {
    state.streaming = on;
    $("#btn-send").classList.toggle("hidden", on);
    $("#btn-stop").classList.toggle("hidden", !on);
    $("#input").disabled = false;
  }

  // ---------- event push from Python ----------
  window.supertermOnEvent = function (msg) {
    if (msg && msg.kind === "event" && msg.payload) {
      const ev = msg.payload;
      if (ev.session_id && state.sessionId && ev.session_id !== state.sessionId) return;
      renderEvent(ev);
    }
  };

  // ---------- sessions ----------
  async function refreshSessions() {
    try {
      const r = await call("session_list", { limit: 100 });
      state.sessions = r.sessions || [];
      renderSessionList();
    } catch (e) { /* ignore */ }
  }

  function renderSessionList() {
    const ul = $("#session-list"); ul.innerHTML = "";
    if (!state.sessions.length) { ul.appendChild(el("li", "muted", "暂无会话")); return; }
    state.sessions.forEach((s) => {
      const li = el("li");
      if (s.id === state.sessionId) li.classList.add("active");
      li.appendChild(el("div", "s-title", s.title || "(新会话)"));
      li.appendChild(el("div", "s-meta", s.provider + " · " + (s.workspace || "")));
      li.onclick = () => openSession(s.id);
      ul.appendChild(li);
    });
  }

  async function openSession(id) {
    toggleDrawer(false);
    const r = await call("session_open", { session_id: id, after: 0 });
    state.sessionId = id;
    state.provider = r.session.provider;
    state.workspace = r.session.workspace;
    $("#btn-agent").textContent = state.provider;
    $("#title").textContent = r.session.title || "会话";
    $("#subtitle").textContent = r.session.provider + " · " + (r.session.workspace || "");
    clearMessages();
    (r.events || []).forEach(renderEvent);
    setStreaming(false);
    await refreshSessions();
  }

  async function newSession() {
    const provider = state.provider || "codex";
    const ws = state.workspace || "";
    try {
      const r = await call("session_create", { provider, workspace: ws, title: "" });
      await openSession(r.session.id);
      toggleDrawer(false);
    } catch (e) { toast("新建失败: " + (e.message || e)); }
  }

  // ---------- send ----------
  async function send() {
    const input = $("#input");
    const text = input.value.trim();
    if (!text || state.streaming) return;
    if (!state.sessionId) {
      try {
        const r = await call("session_create", { provider: state.provider, workspace: state.workspace, title: text.slice(0, 40) });
        state.sessionId = r.session.id;
        $("#title").textContent = r.session.title;
      } catch (e) { toast("无法创建会话: " + (e.message || e)); return; }
    }
    input.value = ""; input.style.height = "auto";
    // render user message locally (also persisted server-side? show immediately)
    renderMessage({ role: "user", text });
    setStreaming(true);
    try {
      await call("turn_start", { session_id: state.sessionId, prompt: text });
    } catch (e) {
      toast("发送失败: " + (e.message || e));
      setStreaming(false);
    }
  }

  async function stop() {
    if (!state.sessionId) return;
    try { await call("turn_cancel", { session_id: state.sessionId }); } catch (e) { /* ignore */ }
    setStreaming(false);
  }

  // ---------- drawer ----------
  function toggleDrawer(show) {
    $("#drawer").classList.toggle("hidden", !show);
    $("#scrim").classList.toggle("hidden", !show);
    if (show) refreshSessions();
  }

  // ---------- settings ----------
  async function openSettings() {
    $("#settings").classList.remove("hidden");
    await refreshBootState();
  }
  function closeSettings() { $("#settings").classList.add("hidden"); }

  async function refreshBootState() {
    try {
      const b = await call("app_get_boot_state", {});
      state.bootState = b;
      renderBindings(b.bindings || {}, b.profiles || []);
      renderProfiles(b.profiles || []);
    } catch (e) { toast("加载设置失败: " + (e.message || e)); }
  }

  function renderBindings(bindings, profiles) {
    const wrap = $("#bindings"); wrap.innerHTML = "";
    wrap.appendChild(el("div", "section-title", "Agent 绑定"));
    ["codex", "claude", "dsh"].forEach((agent) => {
      const row = el("div", "bind-row");
      row.appendChild(el("span", "", agent));
      const sel = el("select");
      sel.appendChild(new Option("— 未绑定 —", ""));
      profiles.filter((p) => p.app_type === agent).forEach((p) => {
        const o = new Option(p.display_name, p.id);
        if (bindings[agent] && bindings[agent].provider_id === p.id) o.selected = true;
        sel.appendChild(o);
      });
      const model = el("input"); model.placeholder = "模型(可选)";
      if (bindings[agent] && bindings[agent].model_id) model.value = bindings[agent].model_id;
      model.style.width = "90px";
      const btn = el("button", "pill-btn", "应用");
      btn.onclick = async () => {
        if (!sel.value) return toast("请选择供应商");
        try {
          const r = await call("config_set_binding", { agent, provider_id: sel.value, model_id: model.value || null });
          toast("已应用 → " + (r.materialize && r.materialize.ok ? "写入配置成功" : "写入失败"));
        } catch (e) { toast("失败: " + (e.message || e)); }
      };
      row.appendChild(sel); row.appendChild(model); row.appendChild(btn);
      wrap.appendChild(row);
    });
  }

  function renderProfiles(profiles) {
    const ul = $("#profile-list"); ul.innerHTML = "";
    if (!profiles.length) { ul.appendChild(el("li", "muted", "暂无供应商档案，可从 CCSwitch 导入或新建。")); return; }
    profiles.forEach((p) => {
      const li = el("li");
      li.appendChild(el("div", "p-name", p.display_name + "  ·  " + p.app_type));
      li.appendChild(el("div", "p-meta", (p.base_url || "(无 base_url)") + "  ·  " + (p.has_token ? ("token " + p.token_masked) : "无 token")));
      const act = el("div", "p-actions");
      const del = el("button", "pill-btn ghost", "删除");
      del.onclick = async () => { await call("config_delete_profile", { id: p.id }); refreshBootState(); };
      act.appendChild(del);
      li.appendChild(act);
      ul.appendChild(li);
    });
  }

  async function importCC() {
    try {
      const rep = await call("config_import_cc_switch", {});
      toast("导入 " + (rep.imported ? rep.imported.length : 0) + " 个供应商");
      await refreshBootState();
    } catch (e) {
      toast("导入失败: " + (e.error ? e.error.message : (e.message || e)));
    }
  }

  async function saveProfile(ev) {
    ev.preventDefault();
    const f = ev.target;
    const body = {
      app_type: f.app_type.value, display_name: f.display_name.value,
      base_url: f.base_url.value, wire_api: f.wire_api.value, token: f.token.value || undefined,
    };
    try {
      const r = await call("config_save_profile", body);
      if (f.model && f.model.value) await call("config_save_profile", { id: r.profile.id, display_name: body.display_name, app_type: body.app_type, base_url: body.base_url, wire_api: body.wire_api, models: [f.model.value] });
      f.reset();
      toast("已保存");
      await refreshBootState();
    } catch (e) { toast("保存失败: " + (e.message || e)); }
  }

  async function loadLogs() {
    if (!state.sessionId) return toast("先打开一个会话");
    const kind = $("#log-filter").value.trim() || undefined;
    try {
      const r = await call("logs_query", { session_id: state.sessionId, after: 0, limit: 300, kind });
      const ul = $("#log-list"); ul.innerHTML = "";
      (r.events || []).forEach((e) => {
        const li = el("li");
        li.appendChild(el("div", "p-name", "#" + e.seq + " " + e.kind));
        const d = JSON.stringify(e.data || {});
        li.appendChild(el("div", "p-meta", d.slice(0, 200)));
        ul.appendChild(li);
      });
      if (!(r.events || []).length) ul.appendChild(el("li", "muted", "无日志"));
    } catch (e) { toast("读取日志失败: " + (e.message || e)); }
  }

  // ---------- wiring ----------
  function init() {
    $("#btn-menu").onclick = () => toggleDrawer(true);
    $("#scrim").onclick = () => toggleDrawer(false);
    $("#btn-new-session").onclick = newSession;
    $("#btn-settings").onclick = openSettings;
    $("#btn-settings-close").onclick = closeSettings;
    $("#btn-send").onclick = send;
    $("#btn-stop").onclick = stop;
    $("#btn-import-cc").onclick = importCC;
    $("#profile-form").addEventListener("submit", saveProfile);
    $("#btn-log-load").onclick = loadLogs;

    document.querySelectorAll(".tab").forEach((t) => {
      t.onclick = () => {
        document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
        t.classList.add("active");
        document.querySelectorAll(".tab-pane").forEach((p) => p.classList.add("hidden"));
        const pane = $("#tab-" + t.dataset.tab); if (pane) pane.classList.remove("hidden");
      };
    });

    const input = $("#input");
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    input.addEventListener("input", () => {
      input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px";
    });

    // agent switcher cycles codex/claude/dsh, applies to next new session
    $("#btn-agent").onclick = () => {
      const order = ["codex", "claude", "dsh"];
      state.provider = order[(order.indexOf(state.provider) + 1) % order.length];
      $("#btn-agent").textContent = state.provider;
      toast("新会话将使用 " + state.provider);
    };

    // boot
    call("app_get_boot_state", {}).then((b) => {
      state.bootState = b;
      $("#subtitle").textContent = "就绪 · 三个后端";
    }).catch(() => {});

    refreshSessions();
    window.addEventListener("keydown", (e) => { if (e.key === "Escape") { toggleDrawer(false); closeSettings(); } });
    setStreaming(false);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
