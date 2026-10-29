/* Mail Engine — browser client. Same-origin API under /api. */
(function () {
  "use strict";

  const TOKEN_KEY = "mail_token";

  const state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    user: null,
    workspace: null,
    route: "dashboard",
    params: {},
    inboxUnread: 0,
  };

  const TITLES = {
    dashboard: "Dashboard",
    campaigns: "Campaigns",
    campaign: "Campaign",
    leads: "Leads",
    inbox: "Inbox",
    mailboxes: "Mailboxes & Warmup",
    settings: "Settings",
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmt(n) {
    if (n === null || n === undefined || n === "") return "—";
    const num = Number(n);
    if (Number.isNaN(num)) return esc(String(n));
    return num.toLocaleString();
  }

  function pct(n) {
    if (n === null || n === undefined || n === "") return "—";
    const num = Number(n);
    if (Number.isNaN(num)) return "—";
    return num.toLocaleString(undefined, { maximumFractionDigits: 1 }) + "%";
  }

  function shortDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return esc(String(iso));
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function dayLabel(iso) {
    if (!iso) return "";
    const parts = String(iso).split("-");
    if (parts.length >= 3) return parts[1] + "/" + parts[2];
    return String(iso);
  }

  function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/).slice(0, 2);
    const out = parts.map((p) => p.charAt(0).toUpperCase()).join("");
    return out || "?";
  }

  function toast(message, kind) {
    const host = $("#toasts");
    if (!host) return;
    const el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function setToken(token) {
    state.token = token || "";
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  }

  function showLogin(message) {
    $("#app").hidden = true;
    $("#login").hidden = false;
    const err = $("#login-error");
    if (err) err.textContent = message || "";
  }

  function showApp() {
    $("#login").hidden = true;
    $("#app").hidden = false;
  }

  async function api(path, options) {
    const opts = options || {};
    const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    if (opts.body && typeof opts.body !== "string" && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    if (state.token) headers.Authorization = "Bearer " + state.token;

    let res;
    try {
      res = await fetch(path, {
        method: opts.method || "GET",
        headers,
        body:
          opts.body && typeof opts.body !== "string"
            ? JSON.stringify(opts.body)
            : opts.body || undefined,
      });
    } catch (err) {
      throw new Error("Network error: " + (err && err.message ? err.message : "request failed"));
    }

    if (res.status === 401 && !String(path).includes("/api/auth/login")) {
      setToken("");
      state.user = null;
      state.workspace = null;
      showLogin("Session expired. Sign in again.");
      throw new Error("Not authenticated");
    }

    const type = res.headers.get("content-type") || "";
    let data = null;
    if (type.includes("application/json")) {
      data = await res.json().catch(() => ({}));
    } else if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || "Request failed (" + res.status + ")");
    }

    if (!res.ok) {
      const detail = data && (data.detail || data.message);
      const msg =
        typeof detail === "string"
          ? detail
          : detail
            ? JSON.stringify(detail)
            : "Request failed (" + res.status + ")";
      const error = new Error(msg);
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function loadingHTML(label) {
    return (
      '<div class="loading-state"><div class="spinner"></div><span>' +
      esc(label || "Loading…") +
      "</span></div>"
    );
  }

  function emptyHTML(title, text) {
    return (
      '<div class="empty-state"><div class="empty-title">' +
      esc(title) +
      "</div>" +
      (text ? '<div class="empty-text">' + esc(text) + "</div>" : "") +
      "</div>"
    );
  }

  function badge(status) {
    const key = String(status || "unknown").toLowerCase().replace(/\s+/g, "_");
    return '<span class="badge ' + esc(key) + '">' + esc(status || "unknown") + "</span>";
  }

  function scoreBar(score) {
    const n = Number(score);
    const v = Number.isNaN(n) ? 0 : Math.max(0, Math.min(100, n));
    const tone = v >= 80 ? "high" : v >= 50 ? "mid" : "low";
    return (
      '<div class="score-bar-wrap"><div class="score-bar-bg"><div class="score-bar-fill ' +
      tone +
      '" style="width:' +
      v +
      '%"></div></div><span class="score-val">' +
      (Number.isNaN(n) ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: 1 })) +
      "</span></div>"
    );
  }

  /* ---------- chrome ---------- */

  function paintChrome() {
    const ws = state.workspace || {};
    const stats = ws.stats || {};
    const nameEl = $("#side-ws-name");
    if (nameEl) nameEl.textContent = ws.name || "Workspace";
    const sentEl = $("#side-sent-today");
    if (sentEl) sentEl.textContent = fmt(stats.sentToday || 0);
    const limitEl = $("#side-daily-limit");
    if (limitEl) limitEl.textContent = fmt(ws.dailyLimit || 0);
    const bar = $("#side-quota-bar");
    if (bar) {
      const limit = Number(ws.dailyLimit || 0);
      const used = Number(stats.sentToday || 0);
      const width = limit ? Math.max(0, Math.min(100, (used / limit) * 100)) : 0;
      bar.style.width = width + "%";
    }
    const planEl = $("#side-plan");
    if (planEl) planEl.textContent = ws.plan || "plan";
    const domainEl = $("#side-domain");
    if (domainEl) domainEl.textContent = ws.sendingDomain || "";
    if (state.user) {
      const userEl = $("#topbar-user");
      if (userEl) userEl.textContent = state.user.email || state.user.name || "";
      const avatar = $("#user-avatar");
      if (avatar) avatar.textContent = initials(state.user.name || state.user.email);
    }
    const unread = $("#nav-unread-badge");
    if (unread) {
      if (state.inboxUnread > 0) {
        unread.hidden = false;
        unread.textContent = fmt(state.inboxUnread);
      } else {
        unread.hidden = true;
      }
    }
  }

  async function refreshWorkspace() {
    try {
      const data = await api("/api/workspace");
      state.workspace = data.workspace || data;
      paintChrome();
    } catch (err) {
      if (err.message !== "Not authenticated") toast(err.message, "error");
    }
  }

  function setActiveNav() {
    const key = state.route === "campaign" ? "campaigns" : state.route;
    $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
    const title = $("#page-title");
    if (title) title.textContent = TITLES[state.route] || "Mail Engine";
  }

  function parseHash() {
    const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "");
    const parts = raw.split("/").filter(Boolean);
    return { route: parts[0] || "dashboard", id: parts[1] || "" };
  }

  function go(hash) {
    if (location.hash === hash) render();
    else location.hash = hash;
  }

  /* ---------- routing ---------- */

  const ROUTES = {
    dashboard: renderDashboard,
    campaigns: renderCampaigns,
    campaign: renderCampaign,
    leads: renderLeads,
    inbox: renderInbox,
    mailboxes: renderMailboxes,
    settings: renderSettings,
  };

  async function render() {
    const parsed = parseHash();
    state.route = parsed.route;
    state.params = { id: parsed.id };
    if ($("#app").hidden) return;
    if (!ROUTES[state.route]) {
      state.route = "dashboard";
      location.hash = "#/dashboard";
      return;
    }
    setActiveNav();
    const view = $("#view");
    view.innerHTML = loadingHTML();
    try {
      await ROUTES[state.route](view);
    } catch (err) {
      if (err.message === "Not authenticated") return;
      view.innerHTML = emptyHTML("Something went wrong", err.message);
      toast(err.message, "error");
    }
  }

  /* ---------- dashboard ---------- */

  async function renderDashboard(view) {
    const [overview, wsData] = await Promise.all([
      api("/api/analytics/overview"),
      api("/api/workspace"),
    ]);
    state.workspace = wsData.workspace || wsData;
    const inbox = overview.inbox || {};
    state.inboxUnread = Number(inbox.unread || 0);
    paintChrome();

    const totals = overview.totals || {};
    const daily = Array.isArray(overview.daily) ? overview.daily : [];
    const campaigns = Array.isArray(overview.campaigns) ? overview.campaigns : [];
    const ws = state.workspace || {};

    const maxSent = daily.reduce((m, d) => Math.max(m, Number(d.sent || 0)), 0) || 1;

    const bars = daily.length
      ? daily
          .map((d) => {
            const sent = Number(d.sent || 0);
            const h = Math.max(2, Math.round((sent / maxSent) * 100));
            return (
              '<div class="chart-bar-col"><div class="chart-bar" style="height:' +
              h +
              '%"><span class="chart-bar-tooltip">' +
              esc(d.day) +
              " · sent " +
              fmt(sent) +
              " · opened " +
              fmt(d.opened) +
              " · replied " +
              fmt(d.replied) +
              "</span></div><span class=\"chart-label\">" +
              esc(dayLabel(d.day)) +
              "</span></div>"
            );
          })
          .join("")
      : "";

    const rows = campaigns.length
      ? campaigns
          .map(
            (c) =>
              "<tr class=\"clickable\" data-action=\"open-campaign\" data-id=\"" +
              esc(c.id) +
              '"><td><div class="cell-primary">' +
              esc(c.name) +
              "</div></td><td>" +
              badge(c.status) +
              '</td><td class="cell-mono">' +
              fmt(c.sent) +
              '</td><td class="cell-mono">' +
              fmt(c.opened) +
              '</td><td class="cell-mono">' +
              pct(c.openRate) +
              '</td><td class="cell-mono">' +
              fmt(c.clicked) +
              '</td><td class="cell-mono">' +
              fmt(c.replied) +
              '</td><td class="cell-mono">' +
              pct(c.replyRate) +
              "</td></tr>"
          )
          .join("")
      : '<tr><td colspan="8">' + emptyHTML("No campaigns yet", "Create one from the Campaigns screen.") + "</td></tr>";

    view.innerHTML =
      '<div class="view-header"><div><h3>' +
      esc(ws.name || "Workspace") +
      '</h3><p class="muted">' +
      esc(ws.sendingDomain || "") +
      " · " +
      esc(ws.timezone || "") +
      " · plan " +
      esc(ws.plan || "") +
      "</p></div>" +
      '<div class="btn-group"><span class="badge">' +
      fmt((ws.stats || {}).mailboxes) +
      ' mailboxes</span><span class="badge">' +
      fmt((ws.stats || {}).leads) +
      ' leads</span><span class="badge">' +
      fmt((ws.stats || {}).replies) +
      " replies</span></div></div>" +
      '<div class="stat-grid">' +
      statTile("Sent", fmt(totals.sent), "opened " + fmt(totals.opened), "accent-border") +
      statTile("Open rate", pct(totals.openRate), "clicked " + fmt(totals.clicked), "secondary-border") +
      statTile("Click rate", pct(totals.clickRate), "bounced " + fmt(totals.bounced), "warning-border") +
      statTile("Reply rate", pct(totals.replyRate), fmt(totals.replied) + " replies", "accent-border") +
      statTile("Unread", fmt(inbox.unread), "interested " + fmt(inbox.interested), "danger-border") +
      "</div>" +
      '<section class="panel"><div class="panel-header"><h4>Daily sends</h4><span class="subtitle">' +
      fmt(daily.length) +
      ' days</span></div>' +
      (bars
        ? '<div class="chart-container">' + bars + "</div>"
        : emptyHTML("No send history yet", "Sends will appear here once a campaign runs.")) +
      "</section>" +
      '<section class="panel" style="padding:0"><div class="panel-header" style="padding:16px 16px 0"><h4>Campaign performance</h4></div>' +
      '<div class="table-wrap" style="border:none;border-radius:0"><table><thead><tr>' +
      "<th>Campaign</th><th>Status</th><th>Sent</th><th>Opened</th><th>Open rate</th><th>Clicked</th><th>Replied</th><th>Reply rate</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div></section>";

    view.addEventListener("click", (e) => {
      const row = e.target.closest("[data-action='open-campaign']");
      if (row) go("#/campaign/" + row.dataset.id);
    });
  }

  function statTile(label, value, sub, border) {
    return (
      '<div class="stat-tile ' +
      esc(border || "") +
      '"><div class="label">' +
      esc(label) +
      '</div><div class="value">' +
      value +
      '</div><div class="sub">' +
      esc(sub) +
      "</div></div>"
    );
  }

  /* ---------- campaigns ---------- */

  async function renderCampaigns(view) {
    const data = await api("/api/campaigns");
    const items = Array.isArray(data.items) ? data.items : [];

    const rows = items.length
      ? items
          .map((c) => {
            const s = c.stats || {};
            return (
              "<tr class=\"clickable\" data-action=\"open-campaign\" data-id=\"" +
              esc(c.id) +
              '"><td><div class="cell-primary">' +
              esc(c.name) +
              '</div><div class="cell-sub">' +
              esc(c.subject || "No subject") +
              "</div></td><td>" +
              badge(c.status) +
              '</td><td class="cell-mono">' +
              fmt(s.enrolled) +
              '</td><td class="cell-mono">' +
              fmt(s.sent) +
              '</td><td class="cell-mono">' +
              pct(s.openRate) +
              '</td><td class="cell-mono">' +
              pct(s.clickRate) +
              '</td><td class="cell-mono">' +
              pct(s.replyRate) +
              '</td><td class="cell-mono">' +
              fmt(s.steps) +
              "</td></tr>"
            );
          })
          .join("")
      : '<tr><td colspan="8">' +
        emptyHTML("No campaigns yet", "Create your first sequence with the form above.") +
        "</td></tr>";

    view.innerHTML =
      '<section class="panel"><div class="panel-header"><div><h4>New campaign</h4><div class="subtitle">Starts as a draft. Activate it from the detail screen.</div></div></div>' +
      '<form id="new-campaign" class="form-grid">' +
      field("Name", '<input class="input" name="name" required placeholder="Q4 outreach">') +
      field("Subject", '<input class="input" name="subject" placeholder="Quick question, {{first_name}}">') +
      field("Daily limit", '<input class="input" name="dailyLimit" type="number" min="1" value="100">') +
      '<div class="field" style="grid-column:1/-1"><label>Body</label><textarea class="textarea" name="body" rows="4" placeholder="Hi {{first_name}},"></textarea></div>' +
      '<div><button type="submit" class="btn primary">Create campaign</button></div>' +
      "</form></section>" +
      '<div class="view-header"><h3>Campaigns <span class="muted">(' +
      fmt(data.total != null ? data.total : items.length) +
      ')</span></h3></div>' +
      '<div class="table-wrap"><table><thead><tr><th>Campaign</th><th>Status</th><th>Enrolled</th><th>Sent</th><th>Open</th><th>Click</th><th>Reply</th><th>Steps</th></tr></thead><tbody>' +
      rows +
      "</tbody></table></div>";

    $("#new-campaign", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        const body = {
          name: String(fd.get("name") || "").trim(),
          subject: String(fd.get("subject") || ""),
          body: String(fd.get("body") || ""),
          dailyLimit: Number(fd.get("dailyLimit")) || 100,
        };
        const created = await api("/api/campaigns", { method: "POST", body });
        toast("Campaign created", "success");
        const id = created.campaign && created.campaign.id;
        if (id) go("#/campaign/" + id);
        else render();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    view.addEventListener("click", (e) => {
      const row = e.target.closest("[data-action='open-campaign']");
      if (row) go("#/campaign/" + row.dataset.id);
    });
  }

  function field(label, control) {
    return '<div class="field"><label>' + esc(label) + "</label>" + control + "</div>";
  }

  /* ---------- campaign detail ---------- */

  async function renderCampaign(view) {
    const id = state.params.id;
    if (!id) {
      go("#/campaigns");
      return;
    }
    const [detail, stepsData, sendsData] = await Promise.all([
      api("/api/campaigns/" + encodeURIComponent(id)),
      api("/api/campaigns/" + encodeURIComponent(id) + "/steps"),
      api("/api/sends?campaignId=" + encodeURIComponent(id) + "&limit=25"),
    ]);
    const c = detail.campaign || {};
    const stats = detail.stats || c.stats || {};
    const steps = Array.isArray(stepsData.items) ? stepsData.items : [];
    const sends = Array.isArray(sendsData.items) ? sendsData.items : [];

    const stepCards = steps.length
      ? steps
          .map(
            (s) =>
              '<div class="step-card"><div class="step-card-header"><span class="step-order">Step ' +
              fmt(s.stepOrder) +
              '</span><button type="button" class="btn danger sm" data-action="delete-step" data-id="' +
              esc(s.id) +
              '">Delete</button></div><div class="step-subject">' +
              esc(s.subject || "(no subject)") +
              '</div><div class="step-meta">delay ' +
              fmt(s.delayDays) +
              ' day(s)</div><div class="step-body-preview">' +
              esc(s.body || "") +
              "</div></div>"
          )
          .join("")
      : emptyHTML("No steps yet", "The campaign subject and body are step 0. Add follow-ups below.");

    const sendRows = sends.length
      ? sends
          .map(
            (s) =>
              "<tr><td class=\"cell-mono\">" +
              esc(s.to || s.toEmail || "") +
              "</td><td>" +
              esc(s.subject || "") +
              '</td><td class="cell-mono">' +
              fmt(s.step != null ? s.step : s.stepOrder) +
              "</td><td>" +
              badge(s.status) +
              '</td><td class="cell-mono">' +
              esc(shortDate(s.sentAt || s.sent_at)) +
              "</td></tr>"
          )
          .join("")
      : '<tr><td colspan="5">' + emptyHTML("Nothing sent yet", "Enroll leads, then send the next batch.") + "</td></tr>";

    view.innerHTML =
      '<div class="view-header"><div><h3>' +
      esc(c.name || "Campaign") +
      "</h3><p>" +
      badge(c.status) +
      ' <span class="muted">daily limit ' +
      fmt(c.dailyLimit) +
      "</span></p></div>" +
      '<div class="btn-group">' +
      '<button type="button" class="btn primary" data-action="activate"' +
      (c.status === "active" ? " disabled" : "") +
      ">Activate</button>" +
      '<button type="button" class="btn warning" data-action="pause"' +
      (c.status === "paused" || c.status === "draft" ? " disabled" : "") +
      ">Pause</button>" +
      '<button type="button" class="btn ghost" data-action="back">All campaigns</button>' +
      "</div></div>" +
      '<div class="stat-grid">' +
      statTile("Enrolled", fmt(stats.enrolled), "steps " + fmt(stats.steps), "") +
      statTile("Sent", fmt(stats.sent), "bounced " + fmt(stats.bounced), "") +
      statTile("Open rate", pct(stats.openRate), "opened " + fmt(stats.opened), "") +
      statTile("Reply rate", pct(stats.replyRate), "replied " + fmt(stats.replied), "") +
      "</div>" +
      '<div class="detail-layout"><div style="display:flex;flex-direction:column;gap:16px">' +
      '<section class="panel"><div class="panel-header"><h4>Message</h4></div>' +
      '<form id="edit-campaign" class="form-grid">' +
      field("Name", '<input class="input" name="name" required value="' + esc(c.name || "") + '">') +
      field("Subject", '<input class="input" name="subject" value="' + esc(c.subject || "") + '">') +
      '<div class="field" style="grid-column:1/-1"><label>Body</label><textarea class="textarea" name="body" rows="8">' +
      esc(c.body || "") +
      "</textarea></div>" +
      '<div class="btn-group"><button type="submit" class="btn primary">Save changes</button>' +
      '<button type="button" class="btn secondary" data-action="preview">Preview</button></div>' +
      "</form>" +
      '<div id="preview-host"></div></section>' +
      '<section class="panel"><div class="panel-header"><div><h4>Sequence steps</h4><div class="subtitle">' +
      fmt(steps.length) +
      " follow-up(s)</div></div></div>" +
      '<div class="steps-list" id="steps-list">' +
      stepCards +
      "</div>" +
      '<form id="add-step" class="form-grid mt-4">' +
      field("Order", '<input class="input" name="stepOrder" type="number" min="1" value="' + (steps.length + 1) + '">') +
      field("Delay (days)", '<input class="input" name="delayDays" type="number" min="0" value="3">') +
      '<div class="field" style="grid-column:1/-1"><label>Subject</label><input class="input" name="subject" required placeholder="Following up"></div>' +
      '<div class="field" style="grid-column:1/-1"><label>Body</label><textarea class="textarea" name="body" rows="3" required></textarea></div>' +
      '<div><button type="submit" class="btn secondary">Add step</button></div>' +
      "</form></section></div>" +
      '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<section class="panel"><div class="panel-header"><h4>Send</h4></div>' +
      '<p class="muted mb-2">Enroll every lead in the workspace, then dispatch the next due batch. Sending is simulated — nothing leaves this machine.</p>' +
      '<div class="btn-group"><button type="button" class="btn primary" data-action="enroll">Enroll all leads</button>' +
      '<button type="button" class="btn secondary" data-action="send-next">Send next 10</button></div>' +
      '<div id="send-report" class="mt-2"></div></section>' +
      '<section class="panel" style="padding:0"><div class="panel-header" style="padding:16px 16px 0"><h4>Recently sent</h4><span class="subtitle">' +
      fmt(sendsData.total != null ? sendsData.total : sends.length) +
      '</span></div><div class="table-wrap" style="border:none"><table><thead><tr><th>To</th><th>Subject</th><th>Step</th><th>Status</th><th>When</th></tr></thead><tbody>' +
      sendRows +
      "</tbody></table></div></section></div></div>";

    const reload = () => render();

    $("#edit-campaign", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        await api("/api/campaigns/" + encodeURIComponent(id), {
          method: "PATCH",
          body: {
            name: String(fd.get("name") || "").trim(),
            subject: String(fd.get("subject") || ""),
            body: String(fd.get("body") || ""),
          },
        });
        toast("Campaign saved", "success");
        reload();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    $("#add-step", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        await api("/api/campaigns/" + encodeURIComponent(id) + "/steps", {
          method: "POST",
          body: {
            stepOrder: Number(fd.get("stepOrder")) || undefined,
            delayDays: Number(fd.get("delayDays")) || 0,
            subject: String(fd.get("subject") || ""),
            body: String(fd.get("body") || ""),
          },
        });
        toast("Step added", "success");
        reload();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    view.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-action]");
      if (!btn || btn.disabled) return;
      const action = btn.dataset.action;
      try {
        if (action === "back") {
          go("#/campaigns");
        } else if (action === "activate" || action === "pause") {
          btn.disabled = true;
          await api("/api/campaigns/" + encodeURIComponent(id) + "/" + action, { method: "POST" });
          toast(action === "activate" ? "Campaign activated" : "Campaign paused", "success");
          reload();
        } else if (action === "delete-step") {
          if (!window.confirm("Delete this step?")) return;
          btn.disabled = true;
          await api("/api/steps/" + encodeURIComponent(btn.dataset.id), { method: "DELETE" });
          toast("Step deleted", "success");
          reload();
        } else if (action === "preview") {
          btn.disabled = true;
          const preview = await api("/api/campaigns/" + encodeURIComponent(id) + "/preview", {
            method: "POST",
            body: {},
          });
          paintPreview(preview);
          btn.disabled = false;
        } else if (action === "enroll") {
          btn.disabled = true;
          const res = await api("/api/campaigns/" + encodeURIComponent(id) + "/enroll", {
            method: "POST",
            body: { all: true },
          });
          const reasons = res.reasons || {};
          toast(
            "Enrolled " + fmt(res.enrolled) + ", skipped " + fmt(res.skipped),
            "success"
          );
          const host = $("#send-report", view);
          if (host) {
            host.innerHTML =
              '<div class="preview-panel"><div>Enrolled <strong>' +
              fmt(res.enrolled) +
              "</strong> · skipped <strong>" +
              fmt(res.skipped) +
              "</strong></div><div class=\"muted\">suppressed " +
              fmt(reasons.suppressed) +
              " · already enrolled " +
              fmt(reasons.already_enrolled) +
              "</div></div>";
          }
          btn.disabled = false;
        } else if (action === "send-next") {
          btn.disabled = true;
          const res = await api("/api/campaigns/" + encodeURIComponent(id) + "/send-next", {
            method: "POST",
            body: { limit: 10 },
          });
          toast("Sent " + fmt(res.sent) + " · blocked " + fmt(res.blocked), res.blocked ? "warning" : "success");
          const host = $("#send-report", view);
          const skipped = res.skipped || {};
          if (host) {
            host.innerHTML =
              '<div class="preview-panel"><div>Sent <strong>' +
              fmt(res.sent) +
              "</strong> · blocked <strong>" +
              fmt(res.blocked) +
              "</strong> · finished <strong>" +
              fmt(res.finished) +
              "</strong></div><div class=\"muted\">skipped — suppressed " +
              fmt(skipped.suppressed) +
              " · paused " +
              fmt(skipped.paused) +
              "</div></div>";
          }
          btn.disabled = false;
        }
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    function paintPreview(preview) {
      const host = $("#preview-host", view);
      if (!host) return;
      const score = preview.subjectScore || {};
      const issues = Array.isArray(score.issues) ? score.issues : [];
      const unknown = Array.isArray(preview.unknownTags) ? preview.unknownTags : [];
      host.innerHTML =
        '<div class="preview-panel mt-4"><div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.05em">Rendered preview</div>' +
        '<div class="preview-subject">' +
        esc(preview.subject || "(empty subject)") +
        '</div><div class="preview-body">' +
        esc(preview.body || "") +
        '</div><div class="score-box"><span class="muted">Subject score</span><span class="score-num">' +
        fmt(score.score) +
        "</span></div>" +
        (issues.length
          ? '<div class="score-issues">' + issues.map((i) => esc(i)).join("<br>") + "</div>"
          : "") +
        (unknown.length
          ? '<div class="score-issues">Unknown tags: ' + unknown.map((t) => esc(t)).join(", ") + "</div>"
          : "") +
        "</div>";
    }
  }

  /* ---------- leads ---------- */

  async function renderLeads(view) {
    const params = new URLSearchParams();
    const filter = state.leadFilter || { status: "", q: "" };
    if (filter.status) params.set("status", filter.status);
    if (filter.q) params.set("q", filter.q);
    params.set("limit", "100");
    const data = await api("/api/leads?" + params.toString());
    const items = Array.isArray(data.items) ? data.items : [];

    const rows = items.length
      ? items
          .map(
            (l) =>
              "<tr><td><div class=\"cell-primary\">" +
              esc(l.email) +
              '</div><div class="cell-sub">' +
              esc([l.firstName, l.lastName].filter(Boolean).join(" ")) +
              "</div></td><td>" +
              esc(l.company || "—") +
              '</td><td class="cell-sub">' +
              esc(l.title || "—") +
              "</td><td>" +
              badge(l.status) +
              '</td><td class="cell-mono">' +
              esc(l.source || "") +
              '</td><td class="text-right"><button type="button" class="btn danger sm" data-action="delete-lead" data-id="' +
              esc(l.id) +
              '">Delete</button></td></tr>'
          )
          .join("")
      : '<tr><td colspan="6">' +
        emptyHTML("No leads match", filter.q || filter.status ? "Try clearing the filter." : "Add a lead or paste a CSV.") +
        "</td></tr>";

    view.innerHTML =
      '<section class="panel"><div class="panel-header"><div><h4>Add a lead</h4></div></div>' +
      '<form id="add-lead" class="form-row">' +
      '<div class="field"><label>Email</label><input class="input" name="email" type="email" required placeholder="ada@example.com"></div>' +
      '<div class="field"><label>First name</label><input class="input" name="firstName"></div>' +
      '<div class="field"><label>Last name</label><input class="input" name="lastName"></div>' +
      '<div class="field"><label>Company</label><input class="input" name="company"></div>' +
      '<div class="field"><label>Title</label><input class="input" name="title"></div>' +
      '<button type="submit" class="btn primary">Add lead</button></form></section>' +
      '<section class="panel"><div class="panel-header"><div><h4>Import CSV</h4><div class="subtitle">Header row: email, first_name, last_name, company, title</div></div></div>' +
      '<form id="import-csv"><textarea class="textarea" name="csv" rows="5" placeholder="email,first_name,last_name,company,title"></textarea>' +
      '<div class="mt-2"><button type="submit" class="btn secondary">Import</button></div>' +
      '<div id="import-report"></div></form></section>' +
      '<div class="filters-bar"><input class="input" id="lead-search" type="search" placeholder="Search name, email, company" value="' +
      esc(filter.q) +
      '"><select class="select" id="lead-status">' +
      statusOptions(filter.status) +
      '</select><span class="muted">' +
      fmt(data.total != null ? data.total : items.length) +
      " leads</span></div>" +
      '<div class="table-wrap"><table><thead><tr><th>Lead</th><th>Company</th><th>Title</th><th>Status</th><th>Source</th><th></th></tr></thead><tbody>' +
      rows +
      "</tbody></table></div>";

    $("#add-lead", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        const body = {};
        ["email", "firstName", "lastName", "company", "title"].forEach((k) => {
          const v = String(fd.get(k) || "").trim();
          if (v) body[k] = v;
        });
        await api("/api/leads", { method: "POST", body });
        toast("Lead added", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    $("#import-csv", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const csv = String(new FormData(e.target).get("csv") || "").trim();
      if (!csv) {
        toast("Paste some CSV first", "warning");
        return;
      }
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        const res = await api("/api/leads/import", { method: "POST", body: { csv } });
        const errors = Array.isArray(res.errors) ? res.errors : [];
        toast("Imported " + fmt(res.imported) + ", skipped " + fmt(res.skipped), "success");
        const host = $("#import-report", view);
        if (host) {
          host.innerHTML =
            '<div class="preview-panel mt-2"><div>Imported <strong>' +
            fmt(res.imported) +
            "</strong> · skipped <strong>" +
            fmt(res.skipped) +
            "</strong></div>" +
            (errors.length
              ? '<div class="score-issues">' + errors.slice(0, 8).map((x) => esc(x)).join("<br>") + "</div>"
              : "") +
            "</div>";
        }
        btn.disabled = false;
        state.leadFilter = filter;
        setTimeout(() => {
          const keep = state.leadFilter;
          render().then(() => {
            state.leadFilter = keep;
          });
        }, 900);
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    let searchTimer = null;
    $("#lead-search", view).addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      const value = e.target.value;
      searchTimer = setTimeout(() => {
        state.leadFilter = { status: filter.status, q: value.trim() };
        render();
      }, 300);
    });
    $("#lead-status", view).addEventListener("change", (e) => {
      state.leadFilter = { status: e.target.value, q: filter.q };
      render();
    });

    view.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-action='delete-lead']");
      if (!btn) return;
      if (!window.confirm("Delete this lead?")) return;
      btn.disabled = true;
      try {
        await api("/api/leads/" + encodeURIComponent(btn.dataset.id), { method: "DELETE" });
        toast("Lead deleted", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });
  }

  function statusOptions(current) {
    const opts = ["", "new", "contacted", "opened", "replied", "bounced", "unsubscribed"];
    return opts
      .map((o) => {
        const label = o || "All statuses";
        return (
          '<option value="' +
          esc(o) +
          '"' +
          (o === current ? " selected" : "") +
          ">" +
          esc(label) +
          "</option>"
        );
      })
      .join("");
  }

  /* ---------- inbox ---------- */

  async function renderInbox(view) {
    const data = await api("/api/inbox");
    const items = Array.isArray(data.items) ? data.items : [];
    state.inboxUnread = Number(data.unread || 0);
    paintChrome();

    if (!state.inboxOpen || !items.some((m) => m.id === state.inboxOpen)) {
      state.inboxOpen = items.length ? items[0].id : "";
    }

    const list = items.length
      ? items
          .map((m) => {
            const active = m.id === state.inboxOpen ? " active" : "";
            const unread = m.isRead ? "" : " unread";
            return (
              '<div class="inbox-item' +
              active +
              unread +
              '" data-action="open-message" data-id="' +
              esc(m.id) +
              '"><div class="inbox-item-top"><span class="inbox-from">' +
              esc(m.fromName || m.fromEmail || "") +
              '</span><span class="inbox-time">' +
              esc(shortDate(m.receivedAt)) +
              '</span></div><div class="inbox-subject">' +
              esc(m.subject || "(no subject)") +
              '</div><div class="inbox-item-top"><span class="inbox-preview">' +
              esc(m.fromEmail || "") +
              "</span>" +
              badge(m.classification) +
              "</div></div>"
            );
          })
          .join("")
      : emptyHTML("Inbox is empty", "Replies land here when a lead writes back.");

    view.innerHTML =
      '<div class="view-header"><div><h3>Inbox</h3><p class="muted">' +
      fmt(data.unread) +
      " unread · " +
      fmt(data.total != null ? data.total : items.length) +
      " messages</p></div></div>" +
      '<div class="inbox-layout"><div class="inbox-list" id="inbox-list">' +
      list +
      '</div><div class="inbox-detail" id="inbox-detail">' +
      emptyHTML("Select a message", "Choose a thread on the left.") +
      "</div></div>";

    async function openMessage(id, mark) {
      const msg = items.find((m) => m.id === id);
      if (!msg) return;
      state.inboxOpen = id;
      $$(".inbox-item", view).forEach((el) => el.classList.toggle("active", el.dataset.id === id));
      const host = $("#inbox-detail", view);
      host.innerHTML = messageDetail(msg);
      bindReply(host, msg);
      if (mark && !msg.isRead) {
        try {
          await api("/api/inbox/" + encodeURIComponent(id), {
            method: "PATCH",
            body: { isRead: true },
          });
          msg.isRead = true;
          const row = view.querySelector('.inbox-item[data-id="' + cssEscape(id) + '"]');
          if (row) row.classList.remove("unread");
          state.inboxUnread = Math.max(0, state.inboxUnread - 1);
          paintChrome();
        } catch (err) {
          toast(err.message, "error");
        }
      }
    }

    if (state.inboxOpen) openMessage(state.inboxOpen, false);

    $("#inbox-list", view).addEventListener("click", (e) => {
      const item = e.target.closest("[data-action='open-message']");
      if (item) openMessage(item.dataset.id, true);
    });
  }

  function cssEscape(value) {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/"/g, "");
  }

  function messageDetail(m) {
    return (
      '<div class="inbox-detail-header"><div class="inbox-detail-subject">' +
      esc(m.subject || "(no subject)") +
      '</div><div class="inbox-detail-meta"><span>' +
      esc(m.fromName || "") +
      " &lt;" +
      esc(m.fromEmail || "") +
      "&gt;</span><span>" +
      badge(m.classification) +
      " " +
      esc(shortDate(m.receivedAt)) +
      "</span></div></div>" +
      '<div class="inbox-detail-body">' +
      esc(m.body || "") +
      "</div>" +
      '<form class="reply-box" id="reply-form"><label class="muted" style="font-size:12px">Reply</label>' +
      '<textarea class="textarea" name="body" rows="4" required placeholder="Write a reply…"></textarea>' +
      '<div><button type="submit" class="btn primary">Send reply</button></div></form>'
    );
  }

  function bindReply(host, msg) {
    const form = $("#reply-form", host);
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = String(new FormData(form).get("body") || "").trim();
      if (!body) return;
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        await api("/api/inbox/" + encodeURIComponent(msg.id) + "/reply", {
          method: "POST",
          body: { body },
        });
        toast("Reply sent", "success");
        form.reset();
        btn.disabled = false;
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });
  }

  /* ---------- mailboxes ---------- */

  async function renderMailboxes(view) {
    const [boxes, warmup] = await Promise.all([api("/api/mailboxes"), api("/api/warmup")]);
    const items = Array.isArray(boxes.items) ? boxes.items : [];
    const warmItems = Array.isArray(warmup.items) ? warmup.items : [];

    const cards = items.length
      ? items
          .map(
            (m) =>
              '<article class="mailbox-card"><div class="mailbox-card-header"><div><div class="mailbox-email">' +
              esc(m.email) +
              '</div><div class="mailbox-name">' +
              esc(m.displayName || m.provider || "") +
              "</div></div>" +
              badge(m.status) +
              '</div><div><div class="muted" style="font-size:11px;margin-bottom:4px">WARMUP SCORE</div>' +
              scoreBar(m.warmupScore) +
              '</div><div class="mailbox-stats"><div class="mailbox-stat"><span class="lbl">Sent today</span><span class="val">' +
              fmt(m.sentToday) +
              " / " +
              fmt(m.dailyLimit) +
              '</span></div><div class="mailbox-stat"><span class="lbl">Remaining</span><span class="val">' +
              fmt(m.remainingToday) +
              '</span></div><div class="mailbox-stat"><span class="lbl">Provider</span><span class="val">' +
              esc(m.provider || "—") +
              '</span></div><div class="mailbox-stat"><span class="lbl">Warmup</span><span class="val">' +
              (m.warmupEnabled ? "on" : "off") +
              "</span></div></div></article>"
          )
          .join("")
      : emptyHTML("No mailboxes yet", "Add a sending identity to start.");

    const warmRows = warmItems.length
      ? warmItems
          .map((w) => {
            const days = Array.isArray(w.days) ? w.days : [];
            const today = days.length ? days[days.length - 1] : {};
            return (
              "<tr><td><div class=\"cell-primary\">" +
              esc(w.email || "") +
              "</div></td><td>" +
              scoreBar(w.warmupScore) +
              '</td><td class="cell-mono">' +
              fmt(today.sent) +
              '</td><td class="cell-mono">' +
              fmt(today.received) +
              '</td><td class="cell-mono">' +
              fmt(today.landedInbox) +
              '</td><td class="cell-mono">' +
              fmt(today.spam) +
              "</td><td>" +
              fmt(days.length) +
              " days</td></tr>"
            );
          })
          .join("")
      : '<tr><td colspan="7">' + emptyHTML("No warmup history", "Run warmup to record today's day.") + "</td></tr>";

    view.innerHTML =
      '<section class="panel"><div class="panel-header"><div><h4>Add mailbox</h4><div class="subtitle">Simulated provider — used as a sending identity only.</div></div></div>' +
      '<form id="add-mailbox" class="form-row">' +
      '<div class="field"><label>Email</label><input class="input" name="email" type="email" required></div>' +
      '<div class="field"><label>Display name</label><input class="input" name="displayName"></div>' +
      '<div class="field"><label>Daily limit</label><input class="input" name="dailyLimit" type="number" min="1" value="50"></div>' +
      '<button type="submit" class="btn primary">Add mailbox</button></form></section>' +
      '<div class="mailbox-grid">' +
      cards +
      "</div>" +
      '<section class="panel" style="padding:0"><div class="panel-header" style="padding:16px 16px 0"><div><h4>Warmup</h4><div class="subtitle">Latest recorded day per mailbox</div></div>' +
      '<button type="button" class="btn secondary" data-action="run-warmup">Run warmup</button></div>' +
      '<div id="warmup-report" style="padding:0 16px"></div>' +
      '<div class="table-wrap" style="border:none"><table><thead><tr><th>Mailbox</th><th>Score</th><th>Sent</th><th>Received</th><th>Inbox</th><th>Spam</th><th>History</th></tr></thead><tbody>' +
      warmRows +
      "</tbody></table></div></section>";

    $("#add-mailbox", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        const body = { email: String(fd.get("email") || "").trim() };
        const name = String(fd.get("displayName") || "").trim();
        if (name) body.displayName = name;
        const limit = Number(fd.get("dailyLimit"));
        if (limit) body.dailyLimit = limit;
        await api("/api/mailboxes", { method: "POST", body });
        toast("Mailbox added", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    view.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-action='run-warmup']");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await api("/api/warmup/run", { method: "POST" });
        toast(
          "Warmup ran · " + fmt(res.mailboxes) + " mailboxes · avg " + fmt(res.avgScore),
          "success"
        );
        const host = $("#warmup-report", view);
        if (host) {
          host.innerHTML =
            '<div class="preview-panel" style="margin:12px 0">Mailboxes <strong>' +
            fmt(res.mailboxes) +
            "</strong> · days created <strong>" +
            fmt(res.daysCreated) +
            "</strong> · average score <strong>" +
            fmt(res.avgScore) +
            "</strong></div>";
        }
        btn.disabled = false;
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });
  }

  /* ---------- settings ---------- */

  async function renderSettings(view) {
    const [wsData, supp] = await Promise.all([api("/api/workspace"), api("/api/suppressions")]);
    const ws = wsData.workspace || wsData;
    state.workspace = ws;
    paintChrome();
    const items = Array.isArray(supp.items) ? supp.items : [];

    const rows = items.length
      ? items
          .map(
            (s) =>
              "<tr><td class=\"cell-mono\">" +
              esc(s.email) +
              "</td><td>" +
              badge(s.reason || "manual") +
              '</td><td class="cell-mono">' +
              esc(shortDate(s.createdAt)) +
              '</td><td class="text-right"><button type="button" class="btn danger sm" data-action="remove-suppression" data-id="' +
              esc(s.id) +
              '">Remove</button></td></tr>'
          )
          .join("")
      : '<tr><td colspan="4">' +
        emptyHTML("No suppressions", "Suppressed addresses are skipped at send time.") +
        "</td></tr>";

    view.innerHTML =
      '<section class="panel"><div class="panel-header"><div><h4>Workspace</h4><div class="subtitle">' +
      esc(ws.name || "") +
      " · " +
      esc(ws.plan || "") +
      " · " +
      esc(ws.timezone || "") +
      "</div></div></div>" +
      '<form id="ws-form" class="form-grid">' +
      field("Daily send limit", '<input class="input" name="dailyLimit" type="number" min="1" value="' + esc(ws.dailyLimit != null ? ws.dailyLimit : "") + '">') +
      field("Sending domain", '<input class="input" name="sendingDomain" value="' + esc(ws.sendingDomain || "") + '">') +
      '<label class="field-checkbox"><input type="checkbox" name="warmupEnabled"' +
      (ws.warmupEnabled ? " checked" : "") +
      "> Warmup enabled</label>" +
      '<div><button type="submit" class="btn primary">Save workspace</button></div></form></section>' +
      '<section class="panel"><div class="panel-header"><div><h4>Suppressions</h4><div class="subtitle">' +
      fmt(supp.total != null ? supp.total : items.length) +
      " addresses will never be mailed</div></div></div>" +
      '<form id="add-suppression" class="form-row">' +
      '<div class="field"><label>Email</label><input class="input" name="email" type="email" required></div>' +
      '<div class="field"><label>Reason</label><select class="select" name="reason"><option value="manual">manual</option><option value="unsubscribe">unsubscribe</option><option value="bounce">bounce</option><option value="complaint">complaint</option></select></div>' +
      '<button type="submit" class="btn secondary">Suppress</button></form>' +
      '<div class="table-wrap mt-4"><table><thead><tr><th>Email</th><th>Reason</th><th>Added</th><th></th></tr></thead><tbody>' +
      rows +
      "</tbody></table></div></section>";

    $("#ws-form", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        const res = await api("/api/workspace", {
          method: "PATCH",
          body: {
            dailyLimit: Number(fd.get("dailyLimit")) || undefined,
            sendingDomain: String(fd.get("sendingDomain") || "").trim(),
            warmupEnabled: fd.get("warmupEnabled") === "on",
          },
        });
        state.workspace = res.workspace || state.workspace;
        paintChrome();
        toast("Workspace updated", "success");
        btn.disabled = false;
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    $("#add-suppression", view).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        await api("/api/suppressions", {
          method: "POST",
          body: {
            email: String(fd.get("email") || "").trim(),
            reason: String(fd.get("reason") || "manual"),
          },
        });
        toast("Address suppressed", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    view.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-action='remove-suppression']");
      if (!btn) return;
      btn.disabled = true;
      try {
        await api("/api/suppressions/" + encodeURIComponent(btn.dataset.id), { method: "DELETE" });
        toast("Suppression removed", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });
  }

  /* ---------- auth + boot ---------- */

  async function login(email, password) {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: { email, password },
    });
    setToken(data.token);
    state.user = data.user || null;
    state.workspace = data.workspace || null;
    return data;
  }

  async function boot() {
    if (!state.token) {
      showLogin("");
      return;
    }
    try {
      const me = await api("/api/auth/me");
      state.user = me.user || null;
      state.workspace = me.workspace || null;
      showApp();
      paintChrome();
      if (!location.hash) location.hash = "#/dashboard";
      render();
    } catch (err) {
      setToken("");
      showLogin(err.message === "Not authenticated" ? "" : "");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const form = $("#login-form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#login-submit");
      const errEl = $("#login-error");
      errEl.textContent = "";
      btn.disabled = true;
      try {
        await login($("#login-email").value.trim(), $("#login-password").value);
        showApp();
        paintChrome();
        if (!location.hash || location.hash === "#/" || location.hash === "#") {
          location.hash = "#/dashboard";
        }
        render();
        toast("Signed in", "success");
      } catch (err) {
        errEl.textContent = err.message || "Sign in failed";
      } finally {
        btn.disabled = false;
      }
    });

    $("#logout-btn").addEventListener("click", async () => {
      try {
        await api("/api/auth/logout", { method: "POST" });
      } catch (err) {
        /* session is cleared locally regardless */
      }
      setToken("");
      state.user = null;
      state.workspace = null;
      showLogin("");
      toast("Signed out", "info");
    });

    $("#nav-toggle").addEventListener("click", () => {
      $("#sidebar").classList.toggle("open");
    });
    $("#nav").addEventListener("click", () => {
      $("#sidebar").classList.remove("open");
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        $("#sidebar").classList.remove("open");
        const modal = $("#modal-container");
        if (modal) modal.innerHTML = "";
      }
    });

    window.addEventListener("hashchange", () => {
      if (!$("#app").hidden) render();
    });

    boot();
  });
})();
