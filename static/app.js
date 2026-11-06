"use strict";

const view = document.getElementById("view");
const state = { meta: null, health: null, session: { reviewer: false, reviewer_setup: false }, cleanups: [] };

// ---------- helpers ----------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    if (options?.method && options.method !== "GET") throw err;
    response = await fetch(path, options);  // a host can drop an idle connection, and a read is safe to repeat
  }
  if (!response.ok) {
    let detail = response.statusText;
    let errors = null;
    try {
      const body = await response.json();
      errors = body.detail?.errors || null;
      detail = body.detail?.message || (typeof body.detail === "string" ? body.detail : detail);
    } catch { /* keep the status text */ }
    const error = new Error(detail);
    error.status = response.status;
    error.errors = errors;
    throw error;
  }
  return response.json();
}

const jsonBody = (data) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const loadingHtml = (text = "Loading") => `<div class="loading" role="status">${icon("spinner", 18)}<span>${esc(text)}</span></div>`;

function setBusy(form, busy, label) {
  form.querySelectorAll("input, select, textarea, button").forEach((el) => {
    if (busy) { el.dataset.wasDisabled = el.disabled; el.disabled = true; } else { el.disabled = el.dataset.wasDisabled === "true"; }
  });
  const button = form.querySelector('button[type="submit"]');
  if (!button) return;
  if (busy) { button.dataset.label = button.innerHTML; button.innerHTML = `${icon("spinner", 14)}${esc(label)}`; }
  else if (button.dataset.label) button.innerHTML = button.dataset.label;
}

function toast(text) {
  document.querySelector(".toast")?.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.textContent = text;
  document.body.append(el);
  setTimeout(() => el.remove(), 4000);
}

function timeAgo(iso) {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} hr ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
const seconds = (ms) => `${(ms / 1000).toFixed(1)}s`;

const svg = (path, size = 16, extra = "") => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${path}</svg>`;
const ICON_PATHS = {
  pass: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  note: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  fix: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  review: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  block: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  circle: '<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>',
  spinner: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  doc: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
};
const icon = (name, size = 16) => svg(ICON_PATHS[name], size, name === "spinner" ? 'class="spin"' : "");

const RESULT_LABELS = { note: "Note", fix: "Vendor to fix", review: "Needs a person", block: "Blocking" };
const RANK = ["pass", "note", "fix", "review", "block"];
const worst = (checks) => checks.reduce((w, c) => (RANK.indexOf(c.result) > RANK.indexOf(w) ? c.result : w), "pass");

function statusPill(status, waitingOn, large = false) {
  const size = large ? 18 : 14;
  const cls = large ? " large" : "";
  if (status === "approved") return `<span class="pill approved${cls}">${icon("pass", size)}Approved</span>`;
  if (status === "rejected") return `<span class="pill rejected${cls}">${icon("block", size)}Rejected</span>`;
  if (status === "pending" && waitingOn === "vendor") return `<span class="pill pending-vendor${cls}">${icon("clock", size)}Pending: waiting on vendor</span>`;
  if (status === "pending") return `<span class="pill pending-internal${cls}">${icon("review", size)}Pending: our team to review</span>`;
  return `<span class="pill running${cls}">${icon("spinner", size)}Running</span>`;
}

function nextStep(c) {
  if (c.status === "approved") return "Added to the vendor master";
  if (c.status === "rejected") return "Escalated to procurement";
  if (c.status === "running") return "Checks in progress";
  return c.waiting_on === "vendor" ? "Vendor has been asked to resubmit" : "Reviewer needs to check it";
}

const COUNTRY_FLAGS = { IN: "India", US: "US", GB: "UK" };

function checkRow(check) {
  const tag = RESULT_LABELS[check.result] ? `<span class="check-result ${check.result}">${RESULT_LABELS[check.result]}</span>` : "";
  return `<div class="check">
    <span class="check-icon ${check.result}">${icon(check.result, 15)}</span>
    <div><span class="check-name">${esc(check.name)}</span>${tag}<div class="check-detail">${esc(check.detail)}</div></div>
  </div>`;
}

function messageCard(message) {
  if (!message) return "";
  const items = message.items?.length ? `<ol>${message.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ol>` : "";
  return `<div class="message">
    <div class="message-subject">${esc(message.subject)}</div>
    <p>${esc(message.greeting)}</p>
    <p>${esc(message.intro)}</p>
    ${items}
    <p>${esc(message.closing)}</p>
  </div>`;
}

const DOC_FIELD_LABELS = {
  doc_type: "Document type", legal_name: "Name", tax_id: "Tax ID", account_holder: "Account holder",
  account_number: "Account number", bank_code: "Bank code", bank_name: "Bank", issue_date: "Date", legibility: "Legibility",
};
const DOC_TYPE_LABELS = {
  gst_certificate: "GST certificate", w9: "Form W-9", vat_certificate: "VAT certificate", cancelled_cheque: "Cancelled cheque",
  bank_letter: "Bank letter", bank_statement: "Bank statement", company_registration: "Company registration", invoice: "Invoice", other: "Something else",
};

function readerBadge(read) {
  if (read.reused) return `<span class="badge">Not replaced, reading kept from last attempt</span>`;
  if (read.reader === "gemini" || read.reader === "claude") {
    const name = read.reader === "gemini" ? "Gemini" : "Claude";
    return `<span class="badge ai" title="${esc(read.model || "")}">Read by ${name} in ${seconds(read.ms)}</span>`;
  }
  if (read.reader === "text_parser") return `<span class="badge warn">Read by backup text parser</span>`;
  return `<span class="badge warn">Couldn't be read</span>`;
}

function docFields(fields) {
  if (!fields) return "";
  const rows = Object.entries(DOC_FIELD_LABELS)
    .filter(([key]) => fields[key])
    .map(([key, label]) => `<dt>${label}</dt><dd>${esc(key === "doc_type" ? DOC_TYPE_LABELS[fields[key]] || fields[key] : fields[key])}</dd>`)
    .join("");
  return `<dl class="fields">${rows}</dl>`;
}

// ---------- dashboard ----------

const TILES = [
  { key: "all", label: "Applications", stat: "total", match: () => true },
  { key: "approved", label: "Approved", stat: "approved", icon: "pass", cls: "ic-approved", match: (c) => c.status === "approved" },
  { key: "vendor", label: "Waiting on vendor", stat: "pending_vendor", icon: "clock", cls: "ic-vendor", match: (c) => c.status === "pending" && c.waiting_on === "vendor" },
  { key: "internal", label: "Our team to review", stat: "pending_internal", icon: "review", cls: "ic-internal", match: (c) => c.status === "pending" && c.waiting_on !== "vendor" },
  { key: "rejected", label: "Rejected", stat: "rejected", icon: "block", cls: "ic-rejected", match: (c) => c.status === "rejected" },
];

async function renderDashboard() {
  let filter = "all";
  let search = "";
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Dashboard</h1><p class="sub" id="dash-sub">Every vendor application, its status, and what happens next.</p></div>
      <a class="btn primary" href="#/review/new">New test application</a>
    </div>
    <div class="tiles" id="tiles"></div>
    <div class="dash">
      <section class="card">
        <div class="card-head"><h2>Applications</h2><input class="search" id="search" type="search" placeholder="Search vendors" aria-label="Search vendors"></div>
        <div class="table-wrap" id="cases"></div>
      </section>
      <aside class="stack">
        <section class="card">
          <div class="card-head"><h2>Run a prepared case</h2></div>
          <div id="scenarios">${state.meta.scenarios.map((s) => `
            <button class="scenario" data-key="${esc(s.key)}">
              <span class="scenario-tag">${esc(s.tag)}</span>
              <div class="scenario-title">${esc(s.title)}</div>
              <div class="scenario-shows">${esc(s.shows)}</div>
            </button>`).join("")}</div>
        </section>
        <button class="btn ghost" id="reset" style="width:100%">Reset demo data</button>
      </aside>
    </div>`;

  let latest = { stats: null, cases: [] };

  function paint() {
    const { stats, cases } = latest;
    if (!stats) return;
    document.getElementById("tiles").innerHTML = TILES.map((t) => `
      <button class="tile ${filter === t.key ? "active" : ""}" data-filter="${t.key}" aria-pressed="${filter === t.key}">
        <span class="tile-label">${t.icon ? `<span class="${t.cls}">${icon(t.icon, 14)}</span>` : ""}${t.label}</span>
        <div class="tile-value">${stats[t.stat]}</div>
      </button>`).join("");
    document.getElementById("dash-sub").textContent = stats.runs
      ? `${stats.runs} run${stats.runs === 1 ? "" : "s"} so far, averaging ${seconds(stats.avg_run_ms)} each. ${stats.vendors_on_file} vendors on file.`
      : `${stats.vendors_on_file} vendors on file. Run a prepared case or start a new application.`;
    const match = TILES.find((t) => t.key === filter).match;
    const rows = cases.filter(match).filter((c) => (c.vendor_name || "").toLowerCase().includes(search));
    document.getElementById("cases").innerHTML = rows.length ? `
      <table>
        <thead><tr><th>Vendor</th><th>Status</th><th class="hide-sm">Next step</th><th class="hide-sm">Attempts</th><th>Updated</th></tr></thead>
        <tbody>${rows.map((c) => `
          <tr class="link" data-case="${esc(c.id)}" data-run="${c.latest_run_id}" data-status="${esc(c.status)}">
            <td><a class="name" href="#/review/case/${esc(c.id)}">${esc(c.vendor_name)}</a><div class="small muted">${esc(c.id)}${COUNTRY_FLAGS[c.country] ? ` · ${COUNTRY_FLAGS[c.country]}` : ""}</div></td>
            <td>${statusPill(c.status, c.waiting_on)}</td>
            <td class="hide-sm small">${nextStep(c)}</td>
            <td class="hide-sm">${c.version}</td>
            <td class="small muted" style="white-space:nowrap">${timeAgo(c.updated_at)}</td>
          </tr>`).join("")}</tbody>
      </table>` : `<div class="empty">${cases.length ? "No applications match." : "No applications yet. Run a prepared case to see the process work."}</div>`;
  }

  async function refresh() {
    try {
      const [stats, cases] = await Promise.all([api("/api/stats"), api("/api/cases")]);
      latest = { stats, cases };
      paint();
    } catch { /* the next poll will try again */ }
  }

  document.getElementById("tiles").addEventListener("click", (e) => {
    const tile = e.target.closest("[data-filter]");
    if (tile) { filter = tile.dataset.filter; paint(); }
  });
  document.getElementById("search").addEventListener("input", (e) => { search = e.target.value.toLowerCase(); paint(); });
  document.getElementById("cases").addEventListener("click", (e) => {
    const row = e.target.closest("tr.link");
    if (!row || e.target.closest("a")) return;
    location.hash = row.dataset.status === "running" ? `#/review/run/${row.dataset.run}` : `#/review/case/${row.dataset.case}`;
  });
  document.getElementById("scenarios").addEventListener("click", async (e) => {
    const button = e.target.closest("[data-key]");
    if (!button) return;
    button.disabled = true;
    try {
      const started = await api(`/api/samples/${button.dataset.key}/run`, { method: "POST" });
      location.hash = `#/review/run/${started.run_id}`;
    } catch (err) { toast(err.message); button.disabled = false; }
  });
  document.getElementById("reset").addEventListener("click", async () => {
    if (!confirm("Clear every application and restore the starting vendor list?")) return;
    await api("/api/reset", { method: "POST" });
    toast("Demo data reset.");
    refresh();
  });

  await refresh();
  const poll = setInterval(refresh, 3000);
  state.cleanups.push(() => clearInterval(poll));
}

// ---------- live run view ----------

async function renderRun(runId) {
  const stages = Object.fromEntries(state.meta.stages.map((s) => [s.key, { ...s, phase: "waiting", checks: [], docs: {}, ms: null, open: null }]));
  const first = await api(`/api/runs/${runId}`);
  const run = first.run;
  let caseInfo = await api(`/api/cases/${run.case_id}`);
  let finished = false;
  let decision = null;
  let message = null;
  let endInfo = null;
  const startedAt = new Date(run.started_at).getTime();

  view.innerHTML = `
    <div class="page-head">
      <div>
        <div class="small muted"><a href="#/review">Dashboard</a> / <a href="#/review/case/${esc(run.case_id)}">${esc(run.case_id)}</a></div>
        <h1>${esc(run.submission.legal_name || "Unnamed vendor")}</h1>
        <p class="sub">Attempt ${run.version}. <span id="elapsed"></span></p>
      </div>
    </div>
    <div class="run-layout">
      <section class="card" id="stages" aria-label="Stages"></section>
      <aside class="decision-col"><section class="card" id="decision" aria-live="polite"></section></aside>
    </div>`;

  const stagesEl = document.getElementById("stages");
  stagesEl.innerHTML = state.meta.stages.map((s) => `<div class="stage waiting" id="stage-${s.key}"></div>`).join("");

  function stageOpen(stage) {
    if (stage.open !== null) return stage.open;
    if (stage.phase === "running") return true;
    if (stage.phase === "done") return RANK.indexOf(worst(stage.checks)) >= RANK.indexOf("fix") || Object.keys(stage.docs).length > 0;
    return false;
  }

  function paintStage(key) {
    const stage = stages[key];
    const el = document.getElementById(`stage-${key}`);
    if (!el) return;
    el.className = `stage ${stage.phase}`;
    const verdict = stage.phase === "done" ? worst(stage.checks) : null;
    const dot = stage.phase === "running" ? icon("spinner", 15) : stage.phase === "done" ? icon(verdict, 15) : icon("circle", 15);
    const counts = {};
    stage.checks.forEach((c) => { counts[c.result] = (counts[c.result] || 0) + 1; });
    const summary = stage.phase === "done"
      ? [counts.block && `${counts.block} blocking`, counts.review && `${counts.review} for a person`, counts.fix && `${counts.fix} to fix`, counts.pass && `${counts.pass} passed`, counts.note && `${counts.note} note${counts.note > 1 ? "s" : ""}`].filter(Boolean).join(" · ")
      : stage.phase === "running" ? "Working" : "";
    const docCards = Object.values(stage.docs).map((d) => `
      <div class="doc-card">
        <div class="doc-head">
          <span class="doc-name">${esc(d.label)}: ${esc(d.filename)}</span>
          ${d.reading ? `<span class="badge ai">${icon("spinner", 12)}${state.health?.ai_configured ? `${esc(state.health.ai_name)} is reading` : "Reading"}</span>` : readerBadge(d)}
        </div>
        ${docFields(d.fields)}
      </div>`).join("");
    const body = stageOpen(stage) && (docCards || stage.checks.length)
      ? `<div class="stage-body">${docCards}${stage.checks.map(checkRow).join("")}</div>` : "";
    el.innerHTML = `
      <button class="stage-head" aria-expanded="${stageOpen(stage)}" data-stage="${key}" ${stage.phase === "waiting" ? "disabled" : ""}>
        <span class="stage-dot ${verdict || ""}">${dot}</span>
        <span><div class="stage-title">${esc(stage.title)}</div><div class="stage-q">${esc(stage.question)}</div></span>
        <span class="stage-meta">${summary}${stage.ms != null ? `<div class="muted small">${seconds(stage.ms)}</div>` : ""}</span>
      </button>${body}`;
  }

  stagesEl.addEventListener("click", (e) => {
    const head = e.target.closest("[data-stage]");
    if (!head || head.disabled) return;
    const stage = stages[head.dataset.stage];
    stage.open = !stageOpen(stage);
    paintStage(head.dataset.stage);
  });

  function paintDecision() {
    const el = document.getElementById("decision");
    if (!finished) {
      const current = state.meta.stages.findIndex((s) => stages[s.key].phase !== "done");
      el.innerHTML = `<div class="decision-top"><span class="section-label">Status</span>${statusPill("running", null, true)}</div>
        <div class="waiting-note">Stage ${Math.min(current + 1, state.meta.stages.length)} of ${state.meta.stages.length}: ${esc(state.meta.stages[Math.max(current, 0)].title)}</div>`;
      return;
    }
    const status = endInfo.status;
    const waitingOn = endInfo.waiting_on;
    const counts = decision?.counts || {};
    const checks = Object.values(stages).flatMap((s) => s.checks);
    const issues = checks.filter((c) => ["block", "review", "fix"].includes(c.result)).sort((a, b) => RANK.indexOf(b.result) - RANK.indexOf(a.result));
    const notes = checks.filter((c) => c.result === "note");
    const scenario = state.meta.scenarios.find((s) => s.key === caseInfo.case.scenario);
    const summaryText = {
      approved: "No issues found, so this vendor has been added to the vendor master.",
      rejected: "A blocking risk was found. The vendor sees a neutral message and the case is escalated.",
    }[status] || (waitingOn === "vendor" ? "The vendor can fix what's listed, and the process runs again when they resubmit."
      : "The vendor has been told it's under review. A person needs to look at this.");
    const internalOnly = status === "rejected" || (status === "pending" && waitingOn !== "vendor");
    el.innerHTML = `
      <div class="decision-top">
        ${statusPill(status, waitingOn, true)}
        <p class="decision-reason">${summaryText}</p>
        ${endInfo.error ? `<p class="error-box" style="margin:8px 0 0">The process hit an unexpected error, so it was sent to a person: ${esc(endInfo.error)}</p>` : ""}
        <div class="count-row">
          <span><b>${counts.pass || 0}</b> passed</span>
          ${counts.note ? `<span><b>${counts.note}</b> note${counts.note > 1 ? "s" : ""}</span>` : ""}
          ${counts.fix ? `<span><b>${counts.fix}</b> to fix</span>` : ""}
          ${counts.review ? `<span><b>${counts.review}</b> for a person</span>` : ""}
          ${counts.block ? `<span><b>${counts.block}</b> blocking</span>` : ""}
          ${endInfo.duration_ms ? `<span class="muted">in ${seconds(endInfo.duration_ms)}</span>` : ""}
        </div>
      </div>
      ${issues.length ? `<div class="panel-section"><div class="section-label">Why</div>
        <ul class="reasons">${issues.map((c) => `<li><span class="check-icon ${c.result}">${icon(c.result, 15)}</span><span>${esc(c.detail)}</span></li>`).join("")}</ul></div>` : ""}
      ${notes.length ? `<div class="panel-section"><div class="section-label">Worth knowing</div>
        <ul class="reasons">${notes.map((c) => `<li><span class="check-icon note">${icon("note", 15)}</span><span>${esc(c.detail)}</span></li>`).join("")}</ul></div>` : ""}
      ${message ? `<div class="panel-section">
        <div class="section-label"><span>What the vendor sees</span>${message.intro_source === "ai" ? `<span class="badge ai">Opening by ${esc(message.intro_by || "AI")}, list from rules</span>` : `<span class="badge">From template</span>`}</div>
        ${messageCard(message)}
        ${internalOnly ? `<p class="small muted" style="margin-top:8px">The reasons above stay internal. The AI never sees them, so it can't reveal them.</p>` : ""}
      </div>` : ""}
      <div class="panel-section actions">
        ${status === "pending" && waitingOn === "vendor" && scenario?.fix_label ? `<button class="btn primary" id="prepared-fix">${esc(scenario.fix_label)}</button>` : ""}
        ${status === "pending" && waitingOn === "vendor" ? `<a class="btn" href="#/review/vendor/${esc(run.case_id)}">Open the vendor's view</a>` : ""}
        <a class="btn" href="#/review/case/${esc(run.case_id)}">Open case and audit trail</a>
      </div>`;
    document.getElementById("prepared-fix")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        const started = await api(`/api/cases/${run.case_id}/resubmit-sample`, { method: "POST" });
        location.hash = `#/review/run/${started.run_id}`;
      } catch (err) { toast(err.message); e.target.disabled = false; }
    });
  }

  let lastEventId = 0;

  function apply(event) {
    if (event.id <= lastEventId) return;  // a reconnect or the backup poll can replay events
    lastEventId = event.id;
    const { kind, stage: key, payload } = event;
    if (kind === "stage_start") { stages[key].phase = "running"; paintStage(key); paintDecision(); }
    else if (kind === "stage_end") { stages[key].phase = "done"; stages[key].ms = payload.ms; paintStage(key); paintDecision(); }
    else if (kind === "check") { stages[key].checks.push(payload); paintStage(key); }
    else if (kind === "doc_reading") { stages[key].docs[payload.slot] = { ...payload, reading: true }; paintStage(key); }
    else if (kind === "doc_read") { stages[key].docs[payload.slot] = payload; paintStage(key); }
    else if (kind === "decision") { decision = payload; }
    else if (kind === "message") { message = payload; }
    else if (kind === "run_end") { endInfo = payload; }
  }

  async function finish() {
    finished = true;
    const latest = await api(`/api/runs/${runId}`);
    decision = latest.run.decision || decision;
    message = latest.run.message || message;
    caseInfo = await api(`/api/cases/${run.case_id}`);
    document.getElementById("elapsed").textContent = endInfo?.duration_ms ? `Finished in ${seconds(endInfo.duration_ms)}.` : "Finished.";
    paintDecision();
  }

  state.meta.stages.forEach((s) => paintStage(s.key));
  paintDecision();

  if (run.state !== "running") {
    first.events.forEach(apply);
    Object.values(stages).forEach((s) => paintStage(s.key));
    endInfo = endInfo || { status: run.status, waiting_on: run.waiting_on, duration_ms: run.duration_ms };
    await finish();
    return;
  }

  const timer = setInterval(() => {
    const el = document.getElementById("elapsed");
    if (el) el.textContent = `Running for ${seconds(Date.now() - startedAt)}.`;
  }, 100);
  const source = new EventSource(`/api/runs/${runId}/stream`);
  // Some hosts buffer live streams, so a slow poll keeps the view moving regardless.
  const backupPoll = setInterval(async () => {
    try { handle((await api(`/api/runs/${runId}`)).events); } catch { /* the next poll will try again */ }
  }, 3000);
  const stop = () => { clearInterval(timer); clearInterval(backupPoll); source.close(); };
  state.cleanups.push(stop);
  function handle(events) {
    if (finished) return;
    events.forEach(apply);
    if (endInfo) { stop(); finish(); }
  }
  source.onmessage = (e) => handle([JSON.parse(e.data)]);
}

// ---------- case page ----------

async function renderCase(caseId) {
  const data = await api(`/api/cases/${caseId}`);
  const { case: c, runs, documents, audit } = data;
  const latest = runs[runs.length - 1];
  const checks = latest.checks || [];
  const issues = checks.filter((x) => ["block", "review", "fix"].includes(x.result)).sort((a, b) => RANK.indexOf(b.result) - RANK.indexOf(a.result));
  const notes = checks.filter((x) => x.result === "note");
  const scenario = state.meta.scenarios.find((s) => s.key === c.scenario);
  const overruled = audit.filter((a) => a.actor === "reviewer").pop();
  const sub = latest.submission;
  const country = state.meta.countries[sub.country];
  const waitingOnVendor = c.status === "pending" && c.waiting_on === "vendor";
  const summaryRows = [
    ["Legal name", sub.legal_name], ["Entity type", sub.entity_type], ["Country", country?.name],
    ["Address", [sub.address, sub.city, sub.state, sub.postal_code].filter(Boolean).join(", ")],
    ["Contact", [sub.contact_name, sub.contact_email].filter(Boolean).join(", ")],
    [country?.tax_label || "Tax ID", sub.tax_id], ["PAN", sub.pan], ["Account holder", sub.account_holder], ["Bank", sub.bank_name],
    ["Account number", sub.account_number], [country?.bank_code_label || "Bank code", sub.bank_code],
  ].filter(([, value]) => value);

  view.innerHTML = `
    <div class="page-head">
      <div>
        <div class="small muted"><a href="#/review">Dashboard</a> / ${esc(c.id)}</div>
        <h1>${esc(c.vendor_name)}</h1>
        <div class="row" style="margin-top:6px">${statusPill(c.status, c.waiting_on)}${overruled ? `<span class="badge">Decided by a reviewer</span>` : ""}</div>
      </div>
      <div class="row">
        ${c.status === "pending" && c.waiting_on === "vendor" && scenario?.fix_label ? `<button class="btn primary" id="prepared-fix">${esc(scenario.fix_label)}</button>` : ""}
        ${c.status === "pending" && c.waiting_on === "vendor" ? `<a class="btn" href="#/review/vendor/${esc(c.id)}">Vendor's view</a>` : ""}
        <a class="btn" href="#/review/run/${latest.id}">Replay latest run</a>
      </div>
    </div>
    <div class="case-layout">
      <div class="stack">
        ${overruled ? `<section class="card"><div class="card-head"><h2>Reviewer's decision</h2></div><div class="card-body"><b>${esc(overruled.action)}.</b> ${esc(overruled.detail)}</div></section>` : ""}
        <section class="card">
          <div class="card-head"><h2>Why the process reached this status</h2></div>
          <div class="card-body">
            ${issues.length ? issues.map(checkRow).join("") : `<p>All ${checks.length} checks passed or were only worth noting.</p>`}
            ${notes.map(checkRow).join("")}
          </div>
        </section>
        <section class="card">
          <div class="card-head"><h2>Message to the vendor</h2><span class="muted small">Attempt ${latest.version}</span></div>
          <div class="card-body">${messageCard(c.message_override || latest.message)}</div>
        </section>
        <section class="card">
          <details><summary>All ${checks.length} checks from attempt ${latest.version}</summary><div class="inner">${checks.map(checkRow).join("")}</div></details>
        </section>
        <section class="card">
          <details><summary>What the vendor submitted</summary><div class="inner">
            <dl class="fields">${summaryRows.map(([k, value]) => `<dt>${esc(k)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>
          </div></details>
        </section>
      </div>
      <aside class="stack">
        <section class="card">
          <div class="card-head"><h2>Reviewer decision</h2></div>
          <div class="card-body">
            <label class="small muted" for="review-note">The process's decision stands unless you overrule it. A reason is required and is logged.</label>
            <textarea id="review-note" placeholder="For example: confirmed the new account by phone" style="margin-top:6px"></textarea>
            <div class="row" style="margin-top:8px">
              <button class="btn good" data-review="approve" ${waitingOnVendor ? "disabled" : ""}>${icon("pass", 14)}Approve</button>
              <button class="btn danger" data-review="reject">${icon("block", 14)}Reject</button>
            </div>
            ${waitingOnVendor ? `<p class="small muted" style="margin-top:8px">Approve opens once the vendor has corrected what they were asked to. You can still reject.</p>` : ""}
          </div>
        </section>
        ${c.vendor_token ? `<section class="card">
          <div class="card-head"><h2>Vendor's private link</h2></div>
          <div class="card-body">
            <div class="link-box"><input readonly aria-label="Vendor's private link" value="${esc(vendorLink(c.id, c.vendor_token))}"><button class="btn" data-copy>Copy</button></div>
            <p class="small muted" style="margin-top:6px">The vendor got this when they applied. It opens only their own application.</p>
          </div>
        </section>` : ""}
        <section class="card">
          <div class="card-head"><h2>Attempts</h2></div>
          ${runs.map((r) => `<div class="attempt">
            <div><a href="#/review/run/${r.id}"><b>Attempt ${r.version}</b></a><div class="small muted">${timeAgo(r.started_at)}${r.duration_ms ? ` · ${seconds(r.duration_ms)}` : ""}</div></div>
            ${statusPill(r.status || "running", r.waiting_on)}
          </div>`).join("")}
        </section>
        <section class="card">
          <div class="card-head"><h2>Documents</h2></div>
          ${documents.length ? documents.map((d) => `<div class="attempt">
            <a href="/api/documents/${d.id}" target="_blank" rel="noopener" class="row" style="flex-wrap:nowrap;min-width:0">${icon("doc", 15)}<span style="overflow-wrap:anywhere">${esc(d.filename)}</span></a>
            <span class="small muted">Attempt ${d.version}</span>
          </div>`).join("") : `<div class="card-body muted">No documents uploaded.</div>`}
        </section>
        <section class="card">
          <div class="card-head"><h2>Audit trail</h2></div>
          ${audit.map((a) => `<div class="audit-item">
            <div class="spread"><span class="actor">${esc(a.actor)}</span><span class="muted" style="font-size:11.5px">${new Date(a.ts).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span></div>
            <div class="audit-what">${esc(a.action)}</div>
            ${a.detail ? `<div class="muted" style="overflow-wrap:anywhere">${esc(a.detail)}</div>` : ""}
          </div>`).join("")}
        </section>
      </aside>
    </div>`;

  document.getElementById("prepared-fix")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const started = await api(`/api/cases/${c.id}/resubmit-sample`, { method: "POST" });
      location.hash = `#/review/run/${started.run_id}`;
    } catch (err) { toast(err.message); e.target.disabled = false; }
  });
  view.querySelector("[data-copy]")?.addEventListener("click", (e) => copyLink(e.target.previousElementSibling.value));
  view.querySelectorAll("[data-review]").forEach((button) => button.addEventListener("click", async () => {
    const note = document.getElementById("review-note").value;
    if (!note.trim()) { toast("Add a reason first. Overruling the process is logged."); document.getElementById("review-note").focus(); return; }
    try {
      await api(`/api/cases/${c.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: button.dataset.review, note }) });
      renderCase(caseId);
    } catch (err) { toast(err.message); }
  }));
}

// ---------- application form ----------

const FORM_FIELDS = ["legal_name", "entity_type", "owner_name", "country", "state", "address", "city", "postal_code", "contact_name", "contact_email", "contact_phone", "tax_id", "pan", "account_holder", "bank_name", "account_number", "bank_code"];
const DOC_SLOTS = ["tax_certificate", "bank_proof"];
const PLACEHOLDERS = {
  IN: { tax_id: "27AAPFU0939F1ZV", pan: "AAPFU0939F", bank_code: "HDFC0001234", account_number: "9 to 18 digits", postal_code: "382445", contact_phone: "+91 98250 11223" },
  US: { tax_id: "12-3456789", bank_code: "9 digits", account_number: "4 to 17 digits", postal_code: "94102", contact_phone: "+1 415 555 0142" },
  GB: { tax_id: "GB123456782", bank_code: "GB82WEST12345698765432", postal_code: "RG1 8AA", contact_phone: "+44 118 496 0123" },
};

function formHtml() {
  const field = (name, label, attrs = "", wide = false, hint = "") => `<div class="field ${wide ? "wide" : ""}" data-field="${name}"><label for="f-${name}">${label}</label><input id="f-${name}" name="${name}" autocomplete="off" ${attrs}>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
  const fileField = (name, label, labelId, hintId) => `<div class="field wide" data-field="${name}"><label for="f-${name}" ${labelId ? `id="${labelId}"` : ""}>${label}</label><input class="file-input" id="f-${name}" name="${name}" type="file" accept=".pdf,.png,.jpg,.jpeg"><div class="hint" id="${hintId}">PDF, PNG or JPEG, up to 10 MB.</div></div>`;
  return `
    <section class="card form-section"><div class="card-head"><h2>Company</h2></div><div class="card-body field-grid">
      ${field("legal_name", "Legal company name", "", true, "Exactly as it appears on your tax registration.")}
      <div class="field" data-field="country"><label for="f-country">Country</label><select id="f-country" name="country">${Object.entries(state.meta.countries).map(([k, c]) => `<option value="${k}">${esc(c.name)}</option>`).join("")}</select></div>
      <div class="field" data-field="entity_type"><label for="f-entity_type">Entity type</label><select id="f-entity_type" name="entity_type"></select></div>
      ${field("owner_name", "Owner name", "", true, "Sole proprietors can be paid into an account in the owner's own name.")}
      ${field("address", "Registered address", "", true)}
      ${field("city", "City")}
      <div class="field" data-field="state"><label for="f-state">State</label><input id="f-state" name="state" list="state-list" autocomplete="off"><datalist id="state-list"></datalist></div>
      ${field("postal_code", "Postal code")}
    </div></section>
    <section class="card form-section"><div class="card-head"><h2>Contact</h2></div><div class="card-body field-grid">
      ${field("contact_name", "Name")}
      ${field("contact_email", "Email", 'type="text" inputmode="email"')}
      ${field("contact_phone", "Phone", 'inputmode="tel"')}
    </div></section>
    <section class="card form-section"><div class="card-head"><h2>Tax</h2></div><div class="card-body field-grid">
      ${field("tax_id", "Tax ID", 'autocapitalize="characters"')}
      ${field("pan", "PAN", 'autocapitalize="characters"')}
      ${fileField("tax_certificate", "Tax document", "tax-doc-label", "tax-doc-current")}
    </div></section>
    <section class="card form-section"><div class="card-head"><h2>Bank</h2></div><div class="card-body field-grid">
      ${field("account_holder", "Account holder name", "", true, "The name the bank account is held in.")}
      ${field("bank_name", "Bank name")}
      ${field("account_number", "Account number", 'inputmode="numeric"')}
      ${field("bank_code", "Bank code", 'autocapitalize="characters"')}
      ${fileField("bank_proof", "Cancelled cheque or bank letter", "", "bank-doc-current")}
    </div></section>`;
}

function wireForm(root) {
  const value = (n) => root.querySelector(`[name="${n}"]`);
  const show = (n, on) => { root.querySelector(`[data-field="${n}"]`).style.display = on ? "" : "none"; };
  const touched = new Set();
  let docsOnFile = new Set();
  let showAll = false;
  let latestCheck = 0;
  let timer = null;

  function sync() {
    const country = value("country").value;
    const info = state.meta.countries[country];
    const entity = value("entity_type");
    const current = entity.value;
    const types = state.meta.entity_types[country];
    entity.innerHTML = types.map((t) => `<option>${esc(t)}</option>`).join("");
    if (types.includes(current)) entity.value = current;
    root.querySelector('[data-field="tax_id"] label').textContent = info.tax_label;
    root.querySelector('[data-field="bank_code"] label').textContent = info.bank_code_label;
    root.querySelector("#tax-doc-label").textContent = info.tax_doc_name[0].toUpperCase() + info.tax_doc_name.slice(1);
    root.querySelector("#state-list").innerHTML = (country === "US" ? state.meta.us_states : state.meta.in_states).map((s) => `<option value="${esc(s)}">`).join("");
    Object.entries(PLACEHOLDERS[country]).forEach(([n, text]) => { value(n).placeholder = text; });
    show("pan", country === "IN");
    show("state", country !== "GB");
    show("account_number", country !== "GB");
    syncOwner();
  }
  function syncOwner() { show("owner_name", value("entity_type").value === "Sole proprietor"); }

  function read() {
    const out = {};
    FORM_FIELDS.forEach((n) => { const el = value(n); const visible = el && el.closest("[data-field]").style.display !== "none"; out[n] = visible ? el.value.trim() : ""; });
    return out;
  }
  const docsPresent = () => DOC_SLOTS.filter((s) => value(s).files.length || docsOnFile.has(s));

  function paint(errors) {
    root.querySelectorAll("[data-field]").forEach((el) => {
      const name = el.dataset.field;
      const message = (showAll || touched.has(name)) && el.style.display !== "none" ? errors[name] : null;
      el.classList.toggle("invalid", Boolean(message));
      el.querySelector(".field-error")?.remove();
      const input = el.querySelector("input, select");
      if (message) {
        el.insertAdjacentHTML("beforeend", `<div class="field-error" id="err-${name}">${esc(message)}</div>`);
        input?.setAttribute("aria-invalid", "true");
        input?.setAttribute("aria-describedby", `err-${name}`);
      } else {
        input?.removeAttribute("aria-invalid");
        input?.removeAttribute("aria-describedby");
      }
    });
  }

  async function check() {
    const ticket = ++latestCheck;
    const { errors } = await api("/api/validate", jsonBody({ fields: read(), docs: docsPresent() }));
    if (ticket === latestCheck) paint(errors);  // a slower, older answer mustn't overwrite a newer one
    return errors;
  }
  function checkSoon() {
    clearTimeout(timer);
    timer = setTimeout(() => check().catch(() => { /* the submit check will catch anything missed */ }), 350);
  }

  value("country").addEventListener("change", sync);
  value("entity_type").addEventListener("change", syncOwner);
  root.addEventListener("focusout", (e) => { const f = e.target.closest("[data-field]"); if (f && e.target.value) { touched.add(f.dataset.field); checkSoon(); } });
  root.addEventListener("change", (e) => { const f = e.target.closest("[data-field]"); if (f) { touched.add(f.dataset.field); checkSoon(); } });
  root.addEventListener("input", (e) => { const f = e.target.closest("[data-field]"); if (f && (showAll || f.classList.contains("invalid"))) checkSoon(); });
  sync();

  return {
    sync,
    fill(sub) {
      value("country").value = sub.country || "IN";
      sync();
      FORM_FIELDS.forEach((n) => { if (value(n)) value(n).value = sub[n] || ""; });
      syncOwner();
    },
    setDocsOnFile(slots) { docsOnFile = new Set(slots); },
    clearErrors() { touched.clear(); showAll = false; paint({}); },
    async errorsNow() {
      try { return (await api("/api/validate", jsonBody({ fields: read(), docs: docsPresent() }))).errors; }
      catch { return {}; }  // can't reach the checker: the server checks again when the form is sent
    },
    // Everything wrong at once, with a summary that links to each field. Returns true when the form is fine.
    showErrors(errors, errorBox) {
      showAll = true;
      paint(errors);
      const order = [...root.querySelectorAll("[data-field]")].filter((el) => el.style.display !== "none").map((el) => el.dataset.field);
      const names = order.filter((n) => errors[n]);  // in the order they appear on the page
      if (!names.length) { errorBox.innerHTML = ""; return true; }
      const label = (n) => root.querySelector(`[data-field="${n}"] label`)?.textContent || n;
      errorBox.innerHTML = `<div class="error-box" role="alert"><b>${names.length === 1 ? "1 detail needs" : `${names.length} details need`} correcting before you can send this.</b>
        <ul>${names.map((n) => `<li><a href="#" data-goto="${n}">${esc(label(n))}</a>: ${esc(errors[n])}</li>`).join("")}</ul></div>`;
      errorBox.querySelectorAll("[data-goto]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); value(a.dataset.goto)?.focus(); }));
      value(names[0])?.focus();
      return false;
    },
    body() {
      const body = new FormData();
      body.append("fields", JSON.stringify(read()));
      DOC_SLOTS.forEach((slot) => { const file = value(slot).files[0]; if (file) body.append(slot, file); });
      return body;
    },
  };
}

async function attachSampleFile(input, path) {
  input.value = "";
  if (!path) return;
  const blob = await (await fetch(`/samples/${encodeURIComponent(path)}`)).blob();
  const transfer = new DataTransfer();
  transfer.items.add(new File([blob], path, { type: blob.type || "application/pdf" }));
  input.files = transfer.files;
}

// Shared by the vendor's form and the reviewer's test form: check, then send, with a visible busy state throughout.
function onSubmit(root, form, errorBox, send) {
  root.addEventListener("submit", async (e) => {
    e.preventDefault();
    setBusy(root, true, "Checking your details");
    const errors = await form.errorsNow();
    setBusy(root, false);
    if (!form.showErrors(errors, errorBox)) return;
    setBusy(root, true, "Uploading documents");
    try {
      await send();
    } catch (err) {
      setBusy(root, false);
      if (err.errors) form.showErrors(err.errors, errorBox);
      else errorBox.innerHTML = `<div class="error-box" role="alert">${esc(err.message)}</div>`;
      errorBox.scrollIntoView({ block: "start" });
    }
  });
}

function renderApply() {
  view.innerHTML = `
    <div class="form-layout">
      <div class="page-head"><div><h1>Apply to become a vendor</h1>
        <p class="sub">Fill in every field and attach both documents. Each answer is checked as you go, and the form won't send until they're all in the right format.</p></div></div>
      <div id="form-error"></div>
      <form id="application" novalidate>${formHtml()}
        <div class="form-foot"><button class="btn primary" type="submit">Submit application</button></div>
      </form>
    </div>`;
  const root = document.getElementById("application");
  const form = wireForm(root);
  onSubmit(root, form, document.getElementById("form-error"), async () => {
    const started = await api("/api/submissions", { method: "POST", body: form.body() });
    location.hash = `#/status/${started.case_id}/${started.vendor_token}`;
  });
}

function renderNew() {
  view.innerHTML = `
    <div class="form-layout">
      <div class="page-head"><div>
        <div class="small muted"><a href="#/review">Dashboard</a> / Test application</div>
        <h1>New test application</h1><p class="sub">The same form vendors see, with prepared cases to start from. You'll watch the run live.</p>
      </div></div>
      <section class="card form-section"><div class="card-body fill-bar">
        <label for="fill" class="small muted">Start from a prepared case, then change anything:</label>
        <select id="fill"><option value="">Blank form</option>${state.meta.scenarios.map((s) => `<option value="${esc(s.key)}">${esc(s.tag)}: ${esc(s.title)}</option>`).join("")}</select>
      </div></section>
      <div id="form-error"></div>
      <form id="application" novalidate>${formHtml()}
        <div class="form-foot"><a class="btn ghost" href="#/review">Cancel</a><button class="btn primary" type="submit">Submit and watch the run</button></div>
      </form>
    </div>`;
  const root = document.getElementById("application");
  const form = wireForm(root);
  document.getElementById("fill").addEventListener("change", async (e) => {
    const scenario = state.meta.scenarios.find((s) => s.key === e.target.value);
    root.reset();
    form.clearErrors();
    document.getElementById("form-error").innerHTML = "";
    form.sync();
    if (!scenario) return;
    form.fill(scenario.submission);
    await attachSampleFile(root.querySelector('[name="tax_certificate"]'), scenario.documents.tax_certificate);
    await attachSampleFile(root.querySelector('[name="bank_proof"]'), scenario.documents.bank_proof);
  });
  onSubmit(root, form, document.getElementById("form-error"), async () => {
    const body = form.body();
    const prepared = document.getElementById("fill").value;
    if (prepared) body.append("prepared", prepared);
    const started = await api("/api/submissions", { method: "POST", body });
    location.hash = `#/review/run/${started.run_id}`;
  });
}

// ---------- the vendor's own application ----------

const VENDOR_STEPS = {
  intake: "Receiving your application", completeness: "Checking nothing is missing", format: "Checking your tax and bank details",
  documents: "Reading your documents", cross: "Comparing your documents with your answers", risk: "Running standard compliance checks",
  decision: "Preparing our reply",
};

const vendorLink = (caseId, token) => `${location.origin}${location.pathname}#/status/${caseId}/${token}`;

async function copyLink(text) {
  try { await navigator.clipboard.writeText(text); toast("Link copied."); } catch { toast("Select the link and copy it."); }
}

function vendorPill(status, waitingOn) {
  if (status === "running") return `<span class="pill running large">${icon("spinner", 18)}Being checked</span>`;
  if (status === "approved") return `<span class="pill approved large">${icon("pass", 18)}Approved</span>`;
  if (status === "rejected") return `<span class="pill rejected large">${icon("block", 18)}Not approved</span>`;
  if (waitingOn === "vendor") return `<span class="pill pending-vendor large">${icon("fix", 18)}Action needed</span>`;
  return `<span class="pill pending-internal large">${icon("clock", 18)}Under review</span>`;
}

function progressCard(progress) {
  const done = new Set(progress?.done || []);
  const keys = Object.keys(VENDOR_STEPS);
  const current = keys.find((k) => !done.has(k));
  const percent = Math.round((done.size / keys.length) * 100);
  return `<section class="card form-section" aria-live="polite"><div class="card-body">
    <h2>We're checking your application</h2>
    <p class="small muted" style="margin-top:2px">This usually takes under a minute. This page updates by itself.</p>
    <div class="progress-bar" role="progressbar" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100" aria-label="Progress"><span style="width:${Math.max(percent, 4)}%"></span></div>
    <ul class="steps">${keys.map((k) => {
      const phase = done.has(k) ? "done" : k === current ? "current" : "waiting";
      const mark = phase === "done" ? icon("pass", 15) : phase === "current" ? icon("spinner", 15) : icon("circle", 15);
      return `<li class="${phase}"><span class="step-icon">${mark}</span>${esc(VENDOR_STEPS[k])}</li>`;
    }).join("")}</ul>
  </div></section>`;
}

async function renderVendor(caseId, token) {
  const headers = token ? { "X-Vendor-Token": token } : {};
  const load = () => api(`/api/vendor/${caseId}`, { headers });
  let data = await load();
  const canResubmit = data.status === "pending" && data.waiting_on === "vendor";
  view.innerHTML = `
    <div class="form-layout">
      <div class="page-head"><div>
        <div class="small muted">${token ? "Your application" : `<a href="#/review/case/${esc(caseId)}">${esc(caseId)}</a> / What the vendor sees`}, reference ${esc(data.case_id)}</div>
        <h1>${esc(data.submission.legal_name || "Your application")}</h1>
        <div class="row" style="margin-top:8px">${vendorPill(data.status, data.waiting_on)}</div>
      </div></div>
      ${token ? `<section class="card form-section"><div class="card-body">
        <div class="small" style="margin-bottom:6px"><b>Keep this link.</b> It's the only way back to your application, and anyone with it can see it.</div>
        <div class="link-box"><input readonly aria-label="Your private link" value="${esc(vendorLink(caseId, token))}"><button class="btn" data-copy>Copy</button></div>
      </div></section>` : `<div class="notice">You're seeing this as a reviewer. The vendor sees the same page through their private link.</div>`}
      <div id="vendor-body">${data.status === "running" ? progressCard(data.progress) : ""}</div>
      ${data.status !== "running" ? `<section class="card form-section"><div class="card-head"><h2>Latest message</h2></div><div class="card-body">${messageCard(data.message)}</div></section>` : ""}
      ${canResubmit ? `
      <div class="page-head" style="margin:24px 0 12px"><div><h2>Correct your application</h2><p class="sub small">Change what the message asks for. Documents you don't replace stay on file.</p></div></div>
      <div id="form-error"></div>
      <form id="application" novalidate>${formHtml()}
        <div class="form-foot"><button class="btn primary" type="submit">Resubmit application</button></div>
      </form>` : ""}
    </div>`;
  view.querySelector("[data-copy]")?.addEventListener("click", (e) => copyLink(e.target.previousElementSibling.value));

  if (data.status === "running") {
    const poll = setInterval(async () => {
      try { data = await load(); } catch { return; }
      if (data.status !== "running") { clearInterval(poll); route(); return; }
      document.getElementById("vendor-body").innerHTML = progressCard(data.progress);
    }, 1500);
    state.cleanups.push(() => clearInterval(poll));
    return;
  }
  if (!canResubmit) return;
  const root = document.getElementById("application");
  const form = wireForm(root);
  form.fill(data.submission);
  form.setDocsOnFile(Object.keys(data.documents));
  const current = (id, slot) => { document.getElementById(id).textContent = data.documents[slot] ? `On file: ${data.documents[slot].filename}. Leave empty to keep it.` : "Nothing on file yet."; };
  current("tax-doc-current", "tax_certificate");
  current("bank-doc-current", "bank_proof");
  onSubmit(root, form, document.getElementById("form-error"), async () => {
    const started = await api(`/api/cases/${caseId}/resubmit`, { method: "POST", body: form.body(), headers });
    if (token) { window.scrollTo(0, 0); route(); } else location.hash = `#/review/run/${started.run_id}`;
  });
}

// ---------- reviewer sign-in ----------

function renderLogin() {
  if (!state.session.reviewer_setup) {
    view.innerHTML = `<div class="empty"><p>The reviewer console isn't set up on this server.</p><p class="small" style="margin-top:8px">Set REVIEWER_PASSCODE in the host's environment, then restart it.</p></div>`;
    return;
  }
  view.innerHTML = `
    <section class="card login"><div class="card-head"><h2>Reviewer sign-in</h2></div>
      <form class="card-body" id="login" novalidate>
        <p class="small muted">The dashboard, every application and the approve and reject buttons are for the procurement team. Vendors only see their own application, through the link they get when they apply.</p>
        <label for="passcode" class="small" style="display:block;margin-top:12px;font-weight:500">Passcode</label>
        <input id="passcode" type="password" autocomplete="current-password" required>
        <div id="login-error" class="field-error" role="alert"></div>
        <div class="form-foot" style="margin-top:12px"><a class="btn ghost" href="#/">Vendor portal</a><button class="btn primary" type="submit">Sign in</button></div>
      </form>
    </section>`;
  const formEl = document.getElementById("login");
  document.getElementById("passcode").focus();
  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const passcode = document.getElementById("passcode").value;
    if (!passcode) { document.getElementById("login-error").textContent = "Enter the passcode."; return; }
    setBusy(formEl, true, "Signing in");
    try {
      await api("/api/reviewer/login", jsonBody({ passcode }));
      await loadSession();
      route();
    } catch (err) {
      setBusy(formEl, false);
      document.getElementById("login-error").textContent = err.message;
      document.getElementById("passcode").select();
    }
  });
}

// ---------- routing ----------

async function loadSession() {
  [state.meta, state.session] = await Promise.all([api("/api/meta"), api("/api/session")]);
}

function paintChrome(area) {
  const reviewer = state.session.reviewer;
  const link = (href, label, key) => `<a href="${href}" class="${area === key ? "active" : ""}">${label}</a>`;
  document.getElementById("nav").innerHTML = reviewer
    ? link("#/review", "Dashboard", "dashboard") + link("#/review/new", "Test application", "new") + link("#/", "Vendor portal", "apply")
    : link("#/", "Apply", "apply");
  const ai = state.health.ai_configured ? `<span class="badge ai">AI reading on: ${esc(state.health.ai_name)}</span>` : `<span class="badge warn">AI off: backup parser only</span>`;
  const account = document.getElementById("account");
  account.innerHTML = reviewer
    ? `${ai}<button class="btn ghost" id="sign-out">Sign out</button>`
    : `<a class="btn ghost" href="#/review">Reviewer sign-in</a>`;
  document.getElementById("sign-out")?.addEventListener("click", async () => {
    await api("/api/reviewer/logout", { method: "POST" });
    await loadSession();
    location.hash = "#/";
    route();
  });
}

async function route() {
  state.cleanups.splice(0).forEach((stop) => stop());
  const [page = "", sub, id] = location.hash.replace(/^#\/?/, "").split("/");
  const area = page === "review" ? (sub === "new" ? "new" : sub ? "" : "dashboard") : page === "" ? "apply" : "";
  paintChrome(area);
  window.scrollTo(0, 0);
  view.innerHTML = loadingHtml();
  try {
    if (page === "") renderApply();
    else if (page === "status" && sub && id) await renderVendor(sub, id);
    else if (page === "review" && !state.session.reviewer) renderLogin();
    else if (page === "review" && !sub) await renderDashboard();
    else if (page === "review" && sub === "new") renderNew();
    else if (page === "review" && sub === "run") await renderRun(id);
    else if (page === "review" && sub === "case") await renderCase(id);
    else if (page === "review" && sub === "vendor") await renderVendor(id, null);
    else view.innerHTML = `<div class="empty"><p>This page doesn't exist.</p><p style="margin-top:10px"><a href="#/">Go to the vendor portal</a></p></div>`;
  } catch (err) {
    if (err.status === 401) { await loadSession(); paintChrome(area); renderLogin(); return; }
    const back = page === "review" ? `<a href="#/review">Back to the dashboard</a>` : `<a href="#/">Go to the vendor portal</a>`;
    view.innerHTML = `<div class="empty"><p>${esc(err.message)}</p><p style="margin-top:10px">${back}</p></div>`;
  }
}

async function start() {
  // A free host can still be waking up, or restarting, when the page loads. Keep trying for about two minutes.
  for (let attempt = 1; ; attempt++) {
    try {
      [state.health] = await Promise.all([api("/api/health"), loadSession()]);
      break;
    } catch (err) {
      if (attempt >= 24) {
        view.innerHTML = `<div class="empty"><p>The demo host isn't answering (${esc(err.message)}).</p><p style="margin-top:10px"><a href="">Reload the page</a></p></div>`;
        return;
      }
      view.innerHTML = `<div class="empty" role="status"><p>The demo host is waking up. This can take about a minute.</p><p class="small muted" style="margin-top:8px">Trying again automatically (attempt ${attempt}).</p></div>`;
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
  }
  window.addEventListener("hashchange", route);
  route();
}

start();
