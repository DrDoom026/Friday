/* PART 11: dashboard application logic - state, drawer rendering, chat
 * lifecycle, and action wiring. Renders API data and sends API requests.
 * Makes no security decisions and never executes a tool directly - every
 * action here is a fetch() against an existing FIRDAY endpoint. */

(function () {
  "use strict";

  const Api = window.FridayApi;

  const state = {
    drawerOpen: false,
    fieldState: "listening", // listening | thinking | responding
    pendingResponse: false,
    lastToolsSnapshot: null,
  };

  const els = {
    canvas: document.getElementById("field"),
    menuBtn: document.getElementById("menuBtn"),
    drawer: document.getElementById("drawer"),
    drawerClose: document.getElementById("drawerClose"),
    drawerBody: document.getElementById("drawerBody"),
    scrim: document.getElementById("scrim"),
    stateWord: document.getElementById("stateWord"),
    inputForm: document.getElementById("inputForm"),
    chatInput: document.getElementById("chatInput"),
    chips: document.getElementById("chips"),
    responseOverlay: document.getElementById("responseOverlay"),
    responseText: document.getElementById("responseText"),
    toast: document.getElementById("toast"),
    keyGate: document.getElementById("keyGate"),
    keyGateInput: document.getElementById("keyGateInput"),
    keyGateSubmit: document.getElementById("keyGateSubmit"),
  };

  const field = new window.FridayField(els.canvas);
  field.start();

  // ---------- small helpers ----------

  function setStateWord(word) {
    state.fieldState = word;
    els.stateWord.textContent = word;
  }

  function showToast(message, { amber = false } = {}) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    els.toast.classList.toggle("amber", amber);
    requestAnimationFrame(() => els.toast.classList.add("visible"));
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => {
      els.toast.classList.remove("visible");
      setTimeout(() => { els.toast.hidden = true; }, 300);
    }, 3200);
  }

  function ripple() {
    field.form();
    field.ripple();
  }

  /** Reads a ToolResult and classifies what the Security Engine actually
   * decided, from the real backend text - never guessed, never faked. */
  function classifyToolResult(result) {
    if (!result) return { decision: "error", label: "unavailable" };
    if (result.status === "success") return { decision: "allow", label: "done" };
    const blockedUntil = result.output && result.output.blocked_until;
    const errText = (result.error || "") + " " + (blockedUntil || "");
    if (/confirmation/i.test(errText)) {
      return { decision: "require_confirmation", label: "requires confirmation (not yet available)" };
    }
    return { decision: "deny", label: "blocked" };
  }

  function statusDotClass(decision) {
    if (decision === "allow" || decision === "healthy") return "healthy";
    if (decision === "require_confirmation" || decision === "warning") return "warning";
    if (decision === "deny" || decision === "error") return "error";
    return "inactive";
  }

  // ---------- drawer open/close ----------

  function openDrawer() {
    state.drawerOpen = true;
    els.drawer.classList.add("open");
    els.drawer.setAttribute("aria-hidden", "false");
    els.scrim.classList.add("visible");
    els.menuBtn.classList.add("open");
    refreshAllSections();
  }

  function closeDrawer() {
    state.drawerOpen = false;
    els.drawer.classList.remove("open");
    els.drawer.setAttribute("aria-hidden", "true");
    els.scrim.classList.remove("visible");
    els.menuBtn.classList.remove("open");
  }

  els.menuBtn.addEventListener("click", () => (state.drawerOpen ? closeDrawer() : openDrawer()));
  els.drawerClose.addEventListener("click", closeDrawer);
  els.scrim.addEventListener("click", closeDrawer);

  // ---------- section rendering scaffolding ----------

  const sectionOrder = [
    "system", "containers", "providers", "adapters",
    "tools", "automations", "integrations", "activity",
  ];
  const sectionEls = {};
  for (const id of sectionOrder) {
    const section = document.createElement("div");
    section.className = "section";
    section.innerHTML = `
      <div class="section-header">
        <span>${id}</span>
        <span class="count" data-role="count"></span>
      </div>
      <div data-role="body"><div class="empty">unavailable</div></div>
    `;
    els.drawerBody.appendChild(section);
    sectionEls[id] = {
      count: section.querySelector('[data-role="count"]'),
      body: section.querySelector('[data-role="body"]'),
    };
  }

  function setSection(id, bodyHtml, count) {
    sectionEls[id].body.innerHTML = bodyHtml;
    sectionEls[id].count.textContent = count == null ? "" : String(count);
  }

  function row({ name, dot = "inactive", value = "", actionLabel = "", actionAmber = false, onAction = null, disabled = false }) {
    const wrap = document.createElement("div");
    wrap.className = "row";
    wrap.innerHTML = `
      <span class="row-name">${name}</span>
      <span class="row-right">
        ${value ? `<span class="row-value">${value}</span>` : ""}
        <span class="dot ${dot}"></span>
      </span>
    `;
    if (actionLabel) {
      const btn = document.createElement("button");
      btn.className = "action" + (actionAmber ? " amber" : "");
      btn.textContent = actionLabel;
      btn.disabled = disabled;
      if (onAction) btn.addEventListener("click", (e) => { e.stopPropagation(); onAction(btn); });
      wrap.querySelector(".row-right").appendChild(btn);
    }
    return wrap;
  }

  function barRow(label, percent) {
    const wrap = document.createElement("div");
    const pct = percent == null ? null : clampPct(percent);
    wrap.className = "row";
    wrap.style.flexDirection = "column";
    wrap.style.alignItems = "stretch";
    wrap.innerHTML = `
      <div style="display:flex;justify-content:space-between;">
        <span class="row-name">${label}</span>
        <span class="row-value">${pct == null ? "unavailable" : pct + "%"}</span>
      </div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct == null ? 0 : pct}%"></div></div>
    `;
    return wrap;
  }

  function clampPct(v) { return Math.max(0, Math.min(100, Math.round(v))); }

  function formatUptime(seconds) {
    if (seconds == null) return "unavailable";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  // ---------- system ----------

  async function refreshSystem() {
    const [health, status] = await Promise.all([Api.get("/health"), Api.get("/system/status")]);
    if (!health.ok && !status.ok) { setSection("system", `<div class="empty">unavailable</div>`); return; }

    const s = status.body || {};
    const frag = document.createElement("div");
    frag.appendChild(row({
      name: "health",
      dot: health.ok ? "healthy" : "error",
      value: health.ok ? (health.body.status || "ok") : "unavailable",
    }));
    frag.appendChild(row({
      name: "uptime",
      dot: status.ok ? "healthy" : "inactive",
      value: formatUptime(s.uptime_seconds),
    }));
    frag.appendChild(row({
      name: "tailscale",
      dot: s.tailscale_connected ? "healthy" : "warning",
      value: s.tailscale_connected ? "connected" : "unverified",
    }));
    frag.appendChild(barRow("cpu", s.cpu_percent));
    frag.appendChild(barRow("memory", s.memory_percent));
    frag.appendChild(barRow("vault", s.vault_percent));
    setSection("system", "", null);
    sectionEls.system.body.replaceChildren(frag);
  }

  // ---------- containers ----------

  async function refreshContainers() {
    const res = await Api.post("/docker/containers", {});
    if (!res.ok || !res.body || res.body.status !== "success") {
      setSection("containers", `<div class="empty">unavailable</div>`, 0);
      return;
    }
    const list = (res.body.output && res.body.output.containers) || [];
    const frag = document.createElement("div");
    if (list.length === 0) frag.appendChild(Object.assign(document.createElement("div"), { className: "empty", textContent: "no containers" }));
    for (const c of list) {
      const running = /running/i.test(c.state || c.status || "");
      frag.appendChild(row({
        name: c.name,
        dot: running ? "healthy" : "inactive",
        value: c.status || c.state || "",
        actionLabel: "restart",
        actionAmber: true,
        onAction: (btn) => restartContainer(c.name, btn),
      }));
    }
    setSection("containers", "", list.length);
    sectionEls.containers.body.replaceChildren(frag);
  }

  async function restartContainer(name, btn) {
    ripple();
    btn.disabled = true;
    const res = await Api.post("/docker/restart", { container: name });
    btn.disabled = false;
    const classified = classifyToolResult(res.body);
    showToast(`${name}: ${classified.label}`, { amber: classified.decision !== "allow" });
  }

  // ---------- providers ----------

  async function refreshProviders(cachedStatus) {
    const status = cachedStatus || (await Api.get("/system/status")).body;
    if (!status) { setSection("providers", `<div class="empty">unavailable</div>`); return; }
    const frag = document.createElement("div");
    frag.appendChild(row({ name: "active planner", dot: "healthy", value: status.planner_mode }));
    frag.appendChild(row({
      name: "omniroute (cloud)",
      dot: status.omniroute_configured ? "healthy" : "inactive",
      value: status.omniroute_configured ? "configured" : "not configured",
    }));
    frag.appendChild(row({ name: "ollama (local)", dot: "inactive", value: "not monitored" }));
    setSection("providers", "", null);
    sectionEls.providers.body.replaceChildren(frag);
  }

  // ---------- adapters ----------

  async function refreshAdapters(cachedStatus) {
    const status = cachedStatus || (await Api.get("/system/status")).body;
    if (!status) { setSection("adapters", `<div class="empty">unavailable</div>`); return; }
    const frag = document.createElement("div");
    frag.appendChild(row({
      name: "gmail",
      dot: status.gmail_configured ? "healthy" : "inactive",
      value: status.gmail_configured ? "configured" : "not configured",
      actionLabel: "poll",
      onAction: (btn) => pollGmail(btn),
      disabled: !status.gmail_configured,
    }));
    setSection("adapters", "", 1);
    sectionEls.adapters.body.replaceChildren(frag);
  }

  async function pollGmail(btn) {
    ripple();
    btn.disabled = true;
    const res = await Api.post("/comm/gmail/poll", {});
    btn.disabled = false;
    if (!res.ok) { showToast("gmail poll unavailable", { amber: true }); return; }
    const count = Array.isArray(res.body) ? res.body.length : 0;
    showToast(`gmail: ${count} message${count === 1 ? "" : "s"} processed`);
    refreshAllSections();
  }

  // ---------- tools + integrations (share one /tools fetch) ----------

  async function refreshTools() {
    const res = await Api.get("/tools");
    if (!res.ok || !Array.isArray(res.body)) {
      setSection("tools", `<div class="empty">unavailable</div>`, 0);
      setSection("integrations", `<div class="empty">unavailable</div>`, 0);
      return;
    }
    const tools = res.body;
    state.lastToolsSnapshot = tools;

    const total = tools.length;
    const confirm = tools.filter((t) => t.permissions && t.permissions.requires_confirmation).length;
    const allowed = total - confirm;

    const frag = document.createElement("div");
    frag.appendChild(row({ name: "total", dot: "healthy", value: String(total) }));
    frag.appendChild(row({ name: "allowed by default", dot: "healthy", value: String(allowed) }));
    frag.appendChild(row({ name: "requires confirmation", dot: "warning", value: String(confirm) }));
    setSection("tools", "", total);
    sectionEls.tools.body.replaceChildren(frag);

    // Integrations: grouped by tool-name namespace, so a future prefix (e.g.
    // "spotify.*") shows up here without any dashboard code change.
    const groups = new Map();
    for (const t of tools) {
      const ns = t.name.includes(".") ? t.name.split(".")[0] : t.name;
      groups.set(ns, (groups.get(ns) || 0) + 1);
    }
    const gFrag = document.createElement("div");
    for (const [ns, count] of [...groups.entries()].sort()) {
      gFrag.appendChild(row({ name: ns, dot: "healthy", value: `${count} tool${count === 1 ? "" : "s"}` }));
    }
    setSection("integrations", "", groups.size);
    sectionEls.integrations.body.replaceChildren(gFrag);
  }

  // ---------- automations + activity (share one /automation/tasks fetch) ----------

  async function refreshAutomations() {
    const res = await Api.get("/automation/tasks");
    if (!res.ok || !Array.isArray(res.body)) {
      setSection("automations", `<div class="empty">unavailable</div>`, 0);
      setSection("activity", `<div class="empty">unavailable</div>`, 0);
      return;
    }
    const tasks = res.body;
    const frag = document.createElement("div");
    if (tasks.length === 0) frag.appendChild(Object.assign(document.createElement("div"), { className: "empty", textContent: "no tasks" }));
    for (const t of tasks) {
      frag.appendChild(row({
        name: t.name,
        dot: t.enabled ? "healthy" : "inactive",
        value: t.enabled ? "enabled" : "disabled",
        actionLabel: "run",
        onAction: (btn) => runAutomation(t.task_id, t.name, btn),
      }));
    }
    setSection("automations", "", tasks.length);
    sectionEls.automations.body.replaceChildren(frag);

    // Activity: every task's execution history, flattened and sorted, most
    // recent first. Sourced entirely from data GET /automation/tasks already
    // returns - no second persistence layer.
    const events = [];
    for (const t of tasks) {
      for (const h of t.history || []) events.push({ ...h, task_name: t.name });
    }
    events.sort((a, b) => new Date(b.started_at) - new Date(a.started_at));
    const aFrag = document.createElement("div");
    if (events.length === 0) aFrag.appendChild(Object.assign(document.createElement("div"), { className: "empty", textContent: "no activity yet" }));
    for (const e of events.slice(0, 10)) {
      const decision = e.security_decision || e.outcome;
      aFrag.appendChild(row({
        name: `${e.task_name} — ${e.tool_name}`,
        dot: statusDotClass(decision === "allow" || e.outcome === "success" ? "allow" : decision),
        value: e.outcome,
      }));
    }
    setSection("activity", "", events.length);
    sectionEls.activity.body.replaceChildren(aFrag);
  }

  async function runAutomation(taskId, name, btn) {
    ripple();
    btn.disabled = true;
    const res = await Api.post(`/automation/tasks/${taskId}/run`, {});
    btn.disabled = false;
    if (!res.ok) { showToast(`${name}: run failed`, { amber: true }); return; }
    const decision = res.body.security_decision;
    const amber = decision && decision !== "allow";
    const label = decision === "require_confirmation"
      ? "requires confirmation (not yet available)"
      : (decision === "deny" ? "blocked" : res.body.outcome);
    showToast(`${name}: ${label}`, { amber });
    refreshAutomations();
  }

  // ---------- centralized, bounded polling ----------

  function refreshAllSections() {
    refreshSystemGroup();
    refreshContainers();
    refreshTools();
    refreshAutomations();
  }

  async function refreshSystemGroup() {
    const statusRes = await Api.get("/system/status");
    await refreshSystem();
    await refreshProviders(statusRes.body);
    await refreshAdapters(statusRes.body);
  }

  Api.poll(() => { if (state.drawerOpen) refreshSystemGroup(); }, 10000);
  Api.poll(() => { if (state.drawerOpen) refreshContainers(); }, 20000);
  Api.poll(() => { if (state.drawerOpen) refreshTools(); }, 60000);
  Api.poll(() => { if (state.drawerOpen) refreshAutomations(); }, 15000);

  // ---------- chat ----------

  els.chips.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    ripple();
    sendChat(chip.dataset.text);
  });

  els.inputForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = els.chatInput.value.trim();
    if (!text) return;
    els.chatInput.value = "";
    sendChat(text);
  });

  els.chatInput.addEventListener("focus", () => ripple());
  els.chatInput.addEventListener("input", () => ripple());

  async function sendChat(text) {
    if (state.pendingResponse) return;
    state.pendingResponse = true;
    setStateWord("thinking");
    ripple();
    const res = await Api.post("/request", { input: text });
    ripple();
    state.pendingResponse = false;
    setStateWord("responding");
    if (!res.ok || !res.body) {
      showOverlay("unavailable — the request could not be completed.");
    } else {
      showOverlay(res.body.output || "(no response)");
    }
    setTimeout(() => setStateWord("listening"), 1500);
  }

  function showOverlay(text) {
    els.responseText.textContent = text;
    els.responseOverlay.hidden = false;
    clearTimeout(showOverlay._t);
    showOverlay._t = setTimeout(() => { els.responseOverlay.hidden = true; }, 9000);
  }

  // ---------- api key gate ----------

  function ensureApiKey() {
    if (Api.getApiKey()) return;
    els.keyGate.hidden = false;
  }
  els.keyGateSubmit.addEventListener("click", () => {
    Api.setApiKey(els.keyGateInput.value.trim());
    els.keyGate.hidden = true;
    refreshAllSections();
  });

  ensureApiKey();
  setStateWord("listening");
})();
