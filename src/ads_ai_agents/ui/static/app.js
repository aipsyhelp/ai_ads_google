"use strict";
(() => {
  const TOKEN = document.querySelector('meta[name="ui-token"]').content;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const LIST_FIELDS = new Set(["approved_domains", "unserved_hosts", "focus_cities", "locales"]);
  let CONFIG = {};

  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: { "X-UI-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({ ok: false, error: "bad response" }));
    data._status = res.status;
    return data;
  }

  // ---------------------------------------------------------------- navigation
  function show(page) {
    $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.nav === page));
    $$(".page").forEach((p) => p.classList.toggle("active", p.dataset.page === page));
    if (page === "reports") loadReports();
    if (page === "agents") loadAgents();
    if (page === "overview") loadOverview();
    if (page === "tags") loadConnectInfo();
    history.replaceState(null, "", "#" + page);
  }
  $$("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.nav)));

  // ---------------------------------------------------------------- forms
  function setForm(form, values) {
    if (!form || !values) return;
    for (const el of form.elements) {
      if (!el.name || !(el.name in values)) continue;
      if (el.type === "password" || el.type === "file") continue; // never echo secrets
      const v = values[el.name];
      if (el.type === "checkbox") el.checked = !!v;
      else if (el.type === "radio") el.checked = el.value === v;
      else if (Array.isArray(v)) el.value = v.join(el.tagName === "TEXTAREA" ? "\n" : ", ");
      else if (typeof v === "object" && v !== null) el.value = JSON.stringify(v, null, 2);
      else el.value = v ?? "";
    }
  }

  function readForm(form) {
    const out = {};
    for (const el of form.elements) {
      if (!el.name || el.type === "file") continue;
      if (el.type === "checkbox") out[el.name] = el.checked;
      else if (el.type === "radio") { if (el.checked) out[el.name] = el.value; }
      else if (LIST_FIELDS.has(el.name)) out[el.name] = el.value.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
      else out[el.name] = el.value;
    }
    return out;
  }

  function fillAll(cfg) {
    CONFIG = cfg;
    setForm($("#form-organization"), cfg.organization);
    setForm($("#form-credentials"), cfg.credentials);
    $("#form-credentials [name=developer_token]").placeholder = cfg.credentials.developer_token_set ? "•••••••• saved (leave empty to keep)" : "not set";
    setForm($("#form-account"), cfg.account);
    setForm($("#form-brand"), cfg.brand);
    setForm($("#form-domains"), cfg.domains);
    setForm($("#form-tenant"), cfg.tenant);
    $("#form-tenant [name=slack_webhook_url]").placeholder = cfg.tenant.slack_webhook_set ? "•••••••• saved" : "not set";
    setForm($("#form-grants"), cfg.grants);
    setForm($("#form-guards"), cfg.guards);
    setForm($("#form-mode"), { mode: cfg.mode.mode });
    $("#form-account_ids [name=account_ids]").value = JSON.stringify(cfg.account_ids, null, 2);
    const t = cfg.tags || {};
    const ids = [t.gtm, t.ga4, t.ads_conversion_id].filter(Boolean).join(", ");
    if (ids && !$("#form-tags [name=text]").value) $("#form-tags [name=text]").value = ids;
    for (const [ev, label] of Object.entries(t.ads_conversion_labels || {})) {
      const el = $(`#form-tags [name=label_${ev}]`);
      if (el) el.value = label;
    }
    setPill(cfg.mode.mode);
    toggleOrgType();
  }

  function setPill(mode) {
    const pill = $("#mode-pill");
    pill.textContent = mode;
    pill.classList.toggle("live", mode !== "READ_ONLY");
  }

  function toggleOrgType() {
    const np = $("#form-organization [name=type][value=nonprofit]").checked;
    $("#form-organization").classList.toggle("is-nonprofit", np);
  }
  $$("#form-organization [name=type]").forEach((r) => r.addEventListener("change", toggleOrgType));

  function payloadFor(section, form) {
    const data = readForm(form);
    if (section === "credentials") {
      if (!data.developer_token) delete data.developer_token;
      if (SA_CONTENT) data.service_account_json_content = SA_CONTENT;
    }
    if (section === "tenant" && !data.slack_webhook_url) delete data.slack_webhook_url;
    if (section === "tags") {
      data.ads_conversion_labels = {};
      for (const k of Object.keys(data)) {
        if (k.startsWith("label_")) { if (data[k]) data.ads_conversion_labels[k.slice(6)] = data[k]; delete data[k]; }
      }
    }
    if (section === "gtm_container") data.container_public_id = $("#gtm-public").value;
    return data;
  }

  let SA_CONTENT = "";
  $("#sa-file").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    SA_CONTENT = f ? await f.text() : "";
  });

  $$("[data-save]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const section = btn.dataset.save;
      const form = $(`#form-${section}`);
      const msg = $(`#msg-${section}`);
      msg.className = "msg";
      msg.textContent = "Saving…";
      const res = await api("POST", `/api/config/${section}`, payloadFor(section, form));
      if (res.ok) {
        msg.className = "msg ok";
        msg.textContent = "Saved ✓" + (res.result.warnings.length ? " · " + res.result.warnings.join(" ") : "");
        if (section === "credentials") { SA_CONTENT = ""; $("#sa-file").value = ""; $("#form-credentials [name=developer_token]").value = ""; }
        if (section === "tenant") $("#form-tenant [name=slack_webhook_url]").value = "";
        if (section === "mode") $("#form-mode [name=confirm]").value = "";
        fillAll(res.config);
        renderSettings(res.settings);
        if (section === "organization") renderOrgUsage(res.settings.organization);
      } else {
        msg.className = "msg bad";
        msg.textContent = Object.entries(res.errors || { error: res.error }).map(([k, v]) => `${k}: ${v}`).join(" · ");
      }
    })
  );

  function renderOrgUsage(org) {
    if (!org) return;
    $("#org-usage").innerHTML =
      `<p><b>Main offering:</b> ${esc(org.main_offering || "—")} <span class="chip ${org.type === "nonprofit" ? "y" : ""}">${esc(org.type)}</span></p>` +
      `<p><b>Seed terms:</b> ${org.seed_terms.map((t) => `<span class="chip">${esc(t)}</span>`).join(" ") || "—"}</p>`;
  }

  // ---------------------------------------------------------------- overview
  function renderSettings(s) { $("#loaded-settings").textContent = JSON.stringify(s, null, 2); }

  async function loadOverview() {
    const [st, s] = await Promise.all([api("GET", "/api/status"), api("GET", "/api/settings")]);
    renderSettings(s.settings);
    setPill(st.mode);
    const files = Object.entries(st.files).map(([k, v]) => `<span class="${v.exists ? "ok" : "bad"}">${v.exists ? "✓" : "✗"} ${esc(v.path)}</span>`).join("<br>");
    $("#overview-cards").innerHTML = `
      <div class="card kpi"><span>Automation mode</span><b class="${st.mode === "READ_ONLY" ? "ok" : "bad"}">${esc(st.mode)}</b><small class="hint">${st.mutations_enabled ? "writes enabled" : "writes blocked"}</small></div>
      <div class="card kpi"><span>Credentials</span><b class="${st.credentials_ready ? "ok" : "warn-t"}">${st.credentials_ready ? "ready" : "incomplete"}</b><small class="hint">token, key file, customer ID</small></div>
      <div class="card kpi"><span>Guard limits</span><b>${st.limits.max_budget_change_percent}% · $${st.limits.max_account_daily_budget}/day</b><small class="hint">approval above ${st.limits.require_approval_above_percent}% · cooldown ${st.limits.cooldown_days}d</small></div>
      <div class="card kpi"><span>Local files</span><small>${files}</small></div>`;
    const sel = $("#plan-sample");
    if (st.samples && st.samples.length) sel.innerHTML = st.samples.map((n) => `<option ${n === "grant_search_campaign.example.json" ? "selected" : ""}>${esc(n)}</option>`).join("");
    if (st.last_ping) renderPing(st.last_ping);
  }

  function renderPing(p) {
    $("#ping-result").innerHTML = p.ok
      ? `<div class="banner ok">✓ Connected read-only: ${esc(p.meta.descriptive_name)} (${esc(p.meta.customer_id)}) · ${esc(p.meta.currency_code)} · ${esc(p.meta.time_zone)}</div>`
      : `<div class="banner bad">✗ ${esc(p.error)}</div>`;
  }
  $("#run-ping").addEventListener("click", async () => { $("#ping-result").textContent = "Pinging…"; renderPing(await api("POST", "/api/ping", {})); });
  $("#init-templates").addEventListener("click", async () => { const r = await api("POST", "/api/init", {}); fillAll(r.config); loadOverview(); });

  // ---------------------------------------------------------------- run
  $("#run-plan").addEventListener("click", async () => {
    const out = $("#plan-result");
    out.textContent = "Running…";
    const r = await api("POST", "/api/run/plan", { sample: $("#plan-sample").value });
    if (!r.ok) { out.innerHTML = `<div class="banner bad">${esc(JSON.stringify(r.errors || r.error))}</div>`; return; }
    const m = r.mission;
    const org = m.organization || {};
    out.innerHTML = `<div class="card" id="plan-card"><h3>Mission plan <span class="chip">read-only</span></h3>
      <p><b>North star:</b> <span id="plan-north-star">${esc(m.north_star)}</span></p>
      <p><b>Main offering:</b> <span id="plan-main-offering">${esc(org.main_offering || "—")}</span> <span class="chip ${org.type === "nonprofit" ? "y" : ""}">${esc(org.type || "business")}</span></p>
      <p><b>Competitor search seeds:</b></p><div class="chips" id="plan-seeds">${(org.competitor_search_seeds || []).map((s) => `<span class="chip">${esc(s)}</span>`).join("") || "<span class=hint>Add your organization to get seeds.</span>"}</div>
      <p><b>Policy gate:</b> ${esc(m.gate.state)} / risk ${esc(m.gate.risk)}</p>
      <table><tr><th>Stage</th><th>Agent</th><th>Recommendation</th></tr>
      ${m.recommendations.map((x) => `<tr><td>${esc(x.stage)}</td><td>${esc(x.agent)}</td><td><b>${esc(x.title)}</b><ul class="tight">${x.actions.slice(0, 4).map((a) => `<li>${esc(a)}</li>`).join("")}</ul></td></tr>`).join("")}
      </table></div>`;
  });

  $("#run-audit").addEventListener("click", async () => {
    const out = $("#audit-result");
    out.textContent = "Reading your account (read-only)…";
    const r = await api("POST", "/api/run/audit", {});
    if (!r.ok) { out.innerHTML = `<div class="banner bad">Audit failed: ${esc(r.error)}</div>`; return; }
    const e = r.executive;
    out.innerHTML = `<div class="card" id="audit-card"><h3>Audit written to <code>${esc(r.directory)}</code></h3>
      <div class="cards"><div class="card kpi"><span>Account</span><b>${esc(e.account)}</b></div>
      <div class="card kpi"><span>Spend MTD</span><b>$${esc(e.spend_mtd)}</b></div>
      <div class="card kpi"><span>Reported conv.</span><b>${esc(e.reported_conversions_mtd)}</b></div>
      <div class="card kpi"><span>Campaigns</span><b>${esc(e.campaign_count)}</b></div></div>
      <p class="hint">${(e.narrative || []).slice(-2).map(esc).join(" ")}</p></div>`;
  });

  // ---------------------------------------------------------------- reports / agents
  async function loadReports() {
    const r = await api("GET", "/api/reports");
    const d = r.latest.dashboard;
    if (!d && !r.latest.executive) { $("#reports-out").innerHTML = `<div class="card hint">No reports yet. Run the read-only audit first (Run tab).</div>`; return; }
    const kv = (o) => Object.entries(o || {}).filter(([, v]) => typeof v !== "object").map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
    $("#reports-out").innerHTML = `<div class="card"><h3>Latest: <code>${esc(r.latest.directory)}</code></h3>
      <h3>Efficiency dashboard</h3><table id="dashboard-table">${kv(d && (d.kpis || d))}</table>
      <h3>Executive summary</h3><table>${kv(r.latest.executive)}</table>
      <details><summary>Raw efficiency_dashboard.json</summary><pre class="code">${esc(JSON.stringify(d, null, 2))}</pre></details></div>
      <p class="hint">Days: ${r.days.map(esc).join(", ")}</p>`;
  }

  async function loadAgents() {
    const r = await api("GET", "/api/agents");
    $("#agents-out").innerHTML = `<p class="lead"><b id="agent-total">${r.counts.total}</b> agents: ${r.counts.platform} platform + ${r.counts.ops} Google Ads ops.</p>
      <div class="card"><h3>Google Ads ops (${r.counts.ops})</h3><table><tr><th>Agent</th><th>Stage</th></tr>${r.ops.map((a) => `<tr><td>${esc(a.class)}</td><td>${esc(a.stage)}</td></tr>`).join("")}</table></div>
      <div class="card"><h3>Platform (${r.counts.platform})</h3><table><tr><th>Agent</th><th>Layer</th><th>Mutation policy</th><th>What it does</th></tr>${r.platform.map((a) => `<tr><td>${esc(a.name)}</td><td>${esc(a.layer)}</td><td>${esc(a.mutates)}</td><td>${esc(a.description)}</td></tr>`).join("")}</table></div>`;
  }

  // ---------------------------------------------------------------- Google Tags
  $("#tags-detect").addEventListener("click", async () => {
    const r = await api("POST", "/api/gtm/parse", { text: $("#form-tags [name=text]").value });
    const p = r.parsed;
    const chips = [...p.gtm, ...p.ga4, ...p.ads].map((id) => `<span class="chip">${esc(id)}</span>`).join("");
    const labels = Object.entries(p.ads_labels).map(([aw, ls]) => `<span class="chip y">${esc(aw)} / ${ls.map(esc).join(", ")}</span>`).join("");
    $("#tags-detected").innerHTML = (chips || "") + labels + p.warnings.map((w) => `<span class="chip y">${esc(w)}</span>`).join("") + `<span class="hint">· ${esc(p.kind)}</span>`;
  });

  async function loadConnectInfo() {
    const r = await api("GET", "/api/gtm/connect-info");
    $("#gtm-sa-email").textContent = r.service_account_email || "(set the service-account key under Account & credentials)";
    if (r.saved && r.saved.account_id) {
      $("#gtm-account").innerHTML = `<option value="${esc(r.saved.account_id)}">${esc(r.saved.account_id)}</option>`;
      $("#gtm-container").innerHTML = `<option value="${esc(r.saved.container_id)}">${esc(r.saved.container_public_id || r.saved.container_id)}</option>`;
      $("#gtm-public").value = r.saved.container_public_id || "";
    }
  }

  let ACCOUNTS = [];
  function fillContainers() {
    const acc = ACCOUNTS.find((a) => a.account_id === $("#gtm-account").value);
    $("#gtm-container").innerHTML = (acc ? acc.containers : []).map((c) => `<option value="${esc(c.container_id)}" data-public="${esc(c.public_id)}">${esc(c.name)} (${esc(c.public_id)})</option>`).join("");
    syncPublic();
  }
  function syncPublic() { const o = $("#gtm-container").selectedOptions[0]; $("#gtm-public").value = o ? o.dataset.public || "" : ""; }
  $("#gtm-account").addEventListener("change", fillContainers);
  $("#gtm-container").addEventListener("change", syncPublic);
  $("#gtm-load-accounts").addEventListener("click", async () => {
    const r = await api("POST", "/api/gtm/accounts", {});
    if (!r.ok) { $("#msg-gtm_container").className = "msg bad"; $("#msg-gtm_container").textContent = r.error; return; }
    ACCOUNTS = r.accounts;
    $("#gtm-account").innerHTML = ACCOUNTS.map((a) => `<option value="${esc(a.account_id)}">${esc(a.name)} (${esc(a.account_id)})</option>`).join("");
    fillContainers();
  });

  function renderPlan(plan, connected) {
    const s = plan.summary;
    return `<p><span class="chip">${s.create} to create</span> <span class="chip y">${s.update} to update</span> <span class="chip">${s.unchanged} unchanged</span> ${connected ? "" : '<span class="hint">· not connected: diff against an empty container</span>'}</p>
      <table id="gtm-plan-table"><tr><th>Change</th><th>Type</th><th>Name</th><th>Why</th></tr>
      ${plan.items.map((i) => `<tr><td class="act-${i.action}">${esc(i.action)}</td><td>${esc(i.kind)} · ${esc(i.body.type)}</td><td>${esc(i.name)}</td><td>${esc(i.purpose)}</td></tr>`).join("")}</table>
      <p><b>Key events:</b> ${plan.key_events.map((e) => `<span class="chip ${e.role === "primary" ? "" : "y"}">${esc(e.event)}</span>`).join(" ")}</p>
      ${plan.notes.length ? `<ul class="tight hint">${plan.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`;
  }
  $("#gtm-plan").addEventListener("click", async () => {
    const r = await api("POST", "/api/gtm/plan", {});
    $("#gtm-plan-out").innerHTML = r.ok ? renderPlan(r.plan, r.connected) : `<div class="banner bad">${esc(r.error)}</div>`;
  });

  let ROLLBACK_ID = "";
  $("#gtm-apply").addEventListener("click", async () => {
    const out = $("#gtm-apply-out");
    out.textContent = "Working…";
    const r = await api("POST", "/api/gtm/apply", { confirm: $("#gtm-confirm").checked });
    if (!r.ok) { out.innerHTML = `<div class="banner bad">${esc(r.error)}</div>`; return; }
    if (r.dry_run) { out.innerHTML = `<div class="banner y" id="gtm-dry-run">Nothing was changed. ${esc(r.reason)}</div>`; return; }
    ROLLBACK_ID = r.rollback_version_id || "";
    $("#gtm-rollback").disabled = !ROLLBACK_ID;
    out.innerHTML = `<div class="banner ok" id="gtm-published">✓ Published GTM version ${esc(r.published_version_id)}. Previous version ${esc(ROLLBACK_ID || "none")} kept for rollback.</div>
      <ol class="tight hint">${r.steps.map((s) => `<li>${esc(s.call)} ${esc(s.name || s.path || s.version_id || "")}</li>`).join("")}</ol>`;
  });
  $("#gtm-rollback").addEventListener("click", async () => {
    const r = await api("POST", "/api/gtm/rollback", { confirm: $("#gtm-confirm").checked, version_id: ROLLBACK_ID });
    $("#gtm-apply-out").insertAdjacentHTML("beforeend", r.applied ? `<div class="banner ok">Rolled back: version ${esc(r.published_version_id)} is live again.</div>` : `<div class="banner y">${esc(r.reason || r.error)}</div>`);
  });

  $("#gtm-verify").addEventListener("click", async () => {
    const r = await api("POST", "/api/gtm/verify", { fetch: $("#gtm-fetch").checked });
    const cls = r.installed === true ? "ok" : r.installed === false ? "bad" : "y";
    $("#gtm-verify-out").innerHTML = `<div class="banner ${cls}" id="gtm-verify-status">${esc(r.message)}</div>` +
      (r.installed === true ? "" : `<details ${r.installed === false ? "open" : ""}><summary>One-time install snippet</summary><p class="hint">Paste the first part high in &lt;head&gt;, the second right after &lt;body&gt;:</p><pre class="code">${esc(r.install_snippet.head)}</pre><pre class="code">${esc(r.install_snippet.body)}</pre></details>`);
  });

  // ---------------------------------------------------------------- boot
  (async () => {
    const r = await api("GET", "/api/config");
    fillAll(r.config);
    const start = (location.hash || "#overview").slice(1);
    show($(`[data-page="${start}"]`) ? start : "overview");
  })();
})();
