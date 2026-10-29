/* Lead Engine — browser client. Same-origin API under /api. */
(function () {
  "use strict";

  const TOKEN_KEY = "lead_token";

  const state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    user: null,
    workspace: null,
    route: "dashboard",
    params: {},
    selection: new Set(),
    search: { page: 1, pageSize: 20, total: 0, items: [] },
    contactsPage: { page: 1, pageSize: 20 },
    enrichTab: "linkedin",
    enrichJob: null,
    pollTimer: null,
  };

  const TITLES = {
    dashboard: "Dashboard",
    prospector: "Prospector",
    contact: "Contact",
    lists: "Lists",
    enrich: "Enrich",
    verifier: "Verifier",
    campaigns: "Campaigns",
    campaign: "Campaign",
    api: "API & MCP",
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

  function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/).slice(0, 2);
    return parts.map((p) => p.charAt(0).toUpperCase()).join("") || "?";
  }

  function fmt(n) {
    if (n === null || n === undefined || n === "") return "--";
    const num = Number(n);
    if (Number.isNaN(num)) return esc(n);
    return num.toLocaleString();
  }

  function pct(used, limit) {
    if (!limit) return 0;
    return Math.max(0, Math.min(100, Math.round((Number(used) / Number(limit)) * 100)));
  }

  function toast(message, kind) {
    const host = $("#toasts");
    const el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.remove();
    }, 4200);
  }

  function setToken(token) {
    state.token = token || "";
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  }

  function showLogin(message) {
    stopPoll();
    $("#app").hidden = true;
    $("#login").hidden = false;
    $("#login-error").textContent = message || "";
    const originEl = $("#login-origin");
    if (originEl) originEl.textContent = "Engine: " + location.origin;
  }

  function showApp() {
    $("#login").hidden = true;
    $("#app").hidden = false;
  }

  async function api(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
    if (!isForm && opts.body && typeof opts.body !== "string" && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    if (state.token) headers.Authorization = "Bearer " + state.token;

    let res;
    try {
      res = await fetch(path, {
        method: opts.method || "GET",
        headers,
        body: isForm
          ? opts.body
          : opts.body && typeof opts.body !== "string"
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
    if (!type.includes("application/json")) {
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(text || "Request failed (" + res.status + ")");
      }
      return res;
    }

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && (data.detail || data.message);
      throw new Error(typeof detail === "string" ? detail : "Request failed (" + res.status + ")");
    }
    return data;
  }

  function stopPoll() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function paintCredits() {
    const ws = state.workspace || {};
    $("#side-credits").textContent = fmt(ws.credits);
    $("#side-verifier").textContent = fmt(ws.verifierCredits);
    $("#side-plan").textContent = (ws.plan || "plan") + " plan";
    const used = Number(ws.recipientsUsed || 0);
    const limit = Number(ws.recipientsLimit || 0);
    $("#side-bar").style.width = pct(used, limit) + "%";
    if (state.user) {
      $("#topbar-user").textContent = state.user.name || state.user.email || "";
    }
  }

  async function refreshMe() {
    const me = await api("/api/auth/me");
    state.user = me.user;
    state.workspace = me.workspace;
    paintCredits();
    return me;
  }

  function setActiveNav() {
    const key = state.route === "contact" ? "prospector" : state.route === "campaign" ? "campaigns" : state.route;
    $$("#nav a").forEach((a) => {
      a.classList.toggle("active", a.dataset.route === key);
    });
    $("#page-title").textContent = TITLES[state.route] || "Lead Engine";
  }

  function parseHash() {
    const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "");
    const [head, ...rest] = raw.split("/").filter(Boolean);
    const route = head || "dashboard";
    const id = rest[0] || "";
    return { route, id };
  }

  function go(hash) {
    if (location.hash === hash) render();
    else location.hash = hash;
  }

  async function render() {
    stopPoll();
    const parsed = parseHash();
    state.route = parsed.route;
    state.params = { id: parsed.id };
    if (!$("#app") || $("#app").hidden) return;
    setActiveNav();
    const view = $("#view");
    view.innerHTML = '<div class="panel loading-row">Loading...</div>';
    try {
      if (state.route === "dashboard") await renderDashboard(view);
      else if (state.route === "prospector") await renderProspector(view);
      else if (state.route === "contact") await renderContact(view, parsed.id);
      else if (state.route === "lists") await renderLists(view, parsed.id);
      else if (state.route === "enrich") await renderEnrich(view);
      else if (state.route === "verifier") await renderVerifier(view);
      else if (state.route === "campaigns") await renderCampaigns(view);
      else if (state.route === "campaign") await renderCampaign(view, parsed.id);
      else if (state.route === "api") await renderApi(view);
      else if (state.route === "settings") await renderSettings(view);
      else {
        view.innerHTML = '<div class="empty-state"><div class="empty-title">Unknown screen</div></div>';
      }
    } catch (err) {
      if (String(err.message) === "Not authenticated") return;
      view.innerHTML =
        '<div class="panel"><p class="form-error">' + esc(err.message || "Failed to load") + "</p></div>";
      toast(err.message || "Failed to load", "error");
    }
  }

  function badge(status) {
    const s = status || "unknown";
    return '<span class="badge ' + esc(s) + '">' + esc(s) + "</span>";
  }

  function scoreChip(score) {
    if (!score) return "";
    const cls = String(score).replace("+", "-plus");
    return '<span class="score-chip ' + esc(cls) + '">' + esc(score) + "</span>";
  }

  /* ---------------- Dashboard ---------------- */

  async function renderDashboard(view) {
    const [ws, contacts, folders, campaigns] = await Promise.all([
      api("/api/workspace"),
      api("/api/contacts?page=1&pageSize=8"),
      api("/api/folders"),
      api("/api/campaigns").catch(() => ({ items: [], unavailable: true })),
    ]);
    state.workspace = ws;
    paintCredits();

    const items = contacts.items || [];
    const revealed = items.filter((c) => c.revealed).length;
    const folderCount = (folders.items || []).length;
    const campaignCount = (campaigns.items || []).length;
    const quota = pct(ws.recipientsUsed, ws.recipientsLimit);

    const rows = items.length
      ? items
          .map((c) => {
            const name = c.name || [c.firstName, c.lastName].filter(Boolean).join(" ");
            const email = c.revealed ? c.workEmail || c.workEmailMasked : c.workEmailMasked;
            return (
              "<tr>" +
              '<td><div class="table-person"><span class="avatar-initials">' +
              esc(initials(name)) +
              '</span><div><div class="person-name">' +
              esc(name) +
              '</div><div class="person-sub">' +
              esc(c.title || "") +
              "</div></div></div></td>" +
              "<td>" +
              esc(c.companyName || "") +
              "</td>" +
              "<td>" +
              esc(c.location || "") +
              "</td>" +
              '<td class="mono">' +
              esc(email || "—") +
              "</td>" +
              "<td>" +
              (c.revealed ? badge(c.workEmailStatus || "verified") : '<span class="badge unknown">masked</span>') +
              "</td>" +
              '<td><button class="btn ghost sm" data-open="' +
              esc(c.id) +
              '">Open</button></td>' +
              "</tr>"
            );
          })
          .join("")
      : '<tr><td colspan="6" class="loading-row">No contacts yet.</td></tr>';

    view.innerHTML =
      '<div class="stat-grid">' +
      tile("Contacts", contacts.total, "in workspace") +
      tile("Revealed", revealed, "on this page") +
      tile("Folders", folderCount, "lists") +
      tile("Campaigns", campaigns.unavailable ? "—" : campaignCount, campaigns.unavailable ? "module not loaded" : "sequences") +
      "</div>" +
      '<div class="panel">' +
      '<div class="panel-header"><h4>Workspace</h4><span class="subtitle">' +
      esc(ws.name || "") +
      " · " +
      esc(ws.plan || "") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Reveal credits</span><span class="kv-val">' +
      fmt(ws.credits) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Verifier credits</span><span class="kv-val">' +
      fmt(ws.verifierCredits) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Seats</span><span class="kv-val">' +
      fmt(ws.seatsUsed) +
      " / " +
      fmt(ws.seats) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Recipient quota</span><span class="kv-val">' +
      fmt(ws.recipientsUsed) +
      " / " +
      fmt(ws.recipientsLimit) +
      "</span></div>" +
      '<div class="bar tall"><span style="width:' +
      quota +
      '%"></span></div>' +
      '<div class="subtitle" style="margin-top:6px">' +
      quota +
      "% of monthly recipient quota used</div>" +
      "</div>" +
      '<div class="panel" style="padding:0">' +
      '<div class="panel-header" style="padding:16px 16px 0"><h4>Recent contacts</h4></div>' +
      '<div class="table-wrap" style="border:none;border-radius:0"><table><thead><tr>' +
      "<th>Name</th><th>Company</th><th>Location</th><th>Work email</th><th>Status</th><th></th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div></div>";

    $$("[data-open]", view).forEach((btn) => {
      btn.addEventListener("click", () => go("#/contact/" + btn.dataset.open));
    });
  }

  function tile(label, value, sub) {
    return (
      '<div class="stat-tile"><div class="label">' +
      esc(label) +
      '</div><div class="value">' +
      (typeof value === "number" ? fmt(value) : esc(value)) +
      '</div><div class="sub">' +
      esc(sub) +
      "</div></div>"
    );
  }

  /* ---------------- Prospector ---------------- */

  async function renderProspector(view) {
    let suggestions = { titles: [], companies: [], industries: [], locations: [], headcounts: [], seniorities: [] };
    try {
      suggestions = await api("/api/search/suggestions");
    } catch (err) {
      toast(err.message, "error");
    }

    const f = state.search.filters || {};
    view.innerHTML =
      '<div class="panel">' +
      '<div class="panel-header"><div><h4>Find people</h4><p class="subtitle">Searching does not spend credits.</p></div></div>' +
      '<div class="form-grid">' +
      field("Title", "f-title", f.title, suggestions.titles) +
      field("Company", "f-company", f.company, suggestions.companies) +
      field("Industry", "f-industry", f.industry, suggestions.industries) +
      field("Location", "f-location", f.location, suggestions.locations) +
      selectField("Headcount", "f-headcount", f.headcount, suggestions.headcounts || []) +
      selectField("Seniority", "f-seniority", f.seniority, suggestions.seniorities || ["founder", "c-level", "vp", "head", "director", "manager", "ic"]) +
      '<label class="field-checkbox"><input type="checkbox" id="f-direct"' +
      (f.hasDirectEmail ? " checked" : "") +
      "> Has direct email</label>" +
      "</div>" +
      '<div class="field" style="margin-top:12px"><label for="f-prompt">Natural language</label>' +
      '<input class="input" id="f-prompt" placeholder="Heads of talent at fintech companies in London" value="' +
      esc(state.search.prompt || "") +
      '"></div>' +
      '<div class="btn-group" style="margin-top:14px">' +
      '<button class="btn primary" id="search-go">Search</button>' +
      '<button class="btn ghost" id="search-clear">Clear</button>' +
      '<button class="btn secondary" id="bulk-reveal" disabled>Reveal selected</button>' +
      "</div></div>" +
      '<div id="search-results"></div>';

    $("#search-go").addEventListener("click", () => runSearch(1));
    $("#search-clear").addEventListener("click", () => {
      state.search = { page: 1, pageSize: 20, total: 0, items: [], filters: {}, prompt: "" };
      state.selection.clear();
      render();
    });
    $("#f-prompt").addEventListener("keydown", (e) => {
      if (e.key === "Enter") runSearch(1);
    });
    $("#bulk-reveal").addEventListener("click", bulkReveal);

    if (state.search.items && state.search.items.length) paintSearchResults();
    else {
      $("#search-results").innerHTML =
        '<div class="empty-state"><div class="empty-title">Search the index</div>' +
        '<div class="empty-text">Filters and the prompt search the workspace contact index. Searching never spends credits — a reveal charges 1 credit only when an email is found.</div></div>';
    }
  }

  function field(label, id, value, list) {
    const listId = id + "-list";
    const options = (list || []).slice(0, 40).map((v) => "<option value=\"" + esc(v) + "\"></option>").join("");
    return (
      '<div class="field"><label for="' +
      id +
      '">' +
      esc(label) +
      '</label><input class="input" id="' +
      id +
      '" list="' +
      listId +
      '" value="' +
      esc(value || "") +
      '"><datalist id="' +
      listId +
      '">' +
      options +
      "</datalist></div>"
    );
  }

  function selectField(label, id, value, options) {
    const opts =
      '<option value="">Any</option>' +
      (options || [])
        .map((o) => '<option value="' + esc(o) + '"' + (o === value ? " selected" : "") + ">" + esc(o) + "</option>")
        .join("");
    return (
      '<div class="field"><label for="' +
      id +
      '">' +
      esc(label) +
      '</label><select class="select" id="' +
      id +
      '">' +
      opts +
      "</select></div>"
    );
  }

  function readFilters() {
    const val = (id) => {
      const el = $("#" + id);
      return el ? el.value.trim() : "";
    };
    const filters = {};
    if (val("f-title")) filters.title = val("f-title");
    if (val("f-company")) filters.company = val("f-company");
    if (val("f-industry")) filters.industry = val("f-industry");
    if (val("f-location")) filters.location = val("f-location");
    if (val("f-headcount")) filters.headcount = val("f-headcount");
    if (val("f-seniority")) filters.seniority = val("f-seniority");
    if ($("#f-direct") && $("#f-direct").checked) filters.hasDirectEmail = true;
    return { filters, prompt: val("f-prompt") };
  }

  async function runSearch(page) {
    const { filters, prompt } = readFilters();
    state.search.filters = filters;
    state.search.prompt = prompt;
    state.search.page = page || 1;
    state.selection.clear();
    const host = $("#search-results");
    if (host) host.innerHTML = '<div class="panel loading-row">Loading...</div>';
    try {
      const data = await api("/api/search", {
        method: "POST",
        body: {
          prompt: prompt || undefined,
          filters,
          page: state.search.page,
          pageSize: state.search.pageSize,
        },
      });
      state.search.total = data.total || 0;
      state.search.page = data.page || 1;
      state.search.pageSize = data.pageSize || 20;
      state.search.items = data.items || [];
      paintSearchResults();
    } catch (err) {
      if (host) host.innerHTML = '<div class="panel"><p class="form-error">' + esc(err.message) + "</p></div>";
      toast(err.message, "error");
    }
  }

  function paintSearchResults() {
    const host = $("#search-results");
    if (!host) return;
    const items = state.search.items || [];
    if (!items.length) {
      host.innerHTML =
        '<div class="empty-state"><div class="empty-title">No matches</div>' +
        '<div class="empty-text">Nothing matched those filters. Searching did not spend credits.</div></div>';
      return;
    }
    const rows = items
      .map((c) => {
        const name = c.name || [c.firstName, c.lastName].filter(Boolean).join(" ");
        const checked = state.selection.has(c.id) ? " checked" : "";
        return (
          "<tr>" +
          '<td><input type="checkbox" data-pick="' +
          esc(c.id) +
          '"' +
          checked +
          "></td>" +
          '<td><div class="table-person"><span class="avatar-initials">' +
          esc(initials(name)) +
          '</span><div><div class="person-name">' +
          esc(name) +
          '</div><div class="person-sub">' +
          esc(c.seniority || "") +
          "</div></div></div></td>" +
          "<td>" +
          esc(c.title || "") +
          "</td>" +
          "<td>" +
          esc(c.companyName || "") +
          "</td>" +
          "<td>" +
          esc(c.location || "") +
          "</td>" +
          '<td class="mono">' +
          esc(c.workEmailMasked || "—") +
          "</td>" +
          '<td class="mono">' +
          esc(c.directEmailMasked || "—") +
          "</td>" +
          "<td>" +
          (c.hasPhone ? "yes" : "—") +
          "</td>" +
          "<td>" +
          (c.revealed
            ? '<button class="btn ghost sm" data-open="' + esc(c.id) + '">View</button>'
            : '<button class="btn primary sm" data-reveal="' + esc(c.id) + '">Reveal</button>') +
          "</td></tr>"
        );
      })
      .join("");

    const pages = Math.max(1, Math.ceil(state.search.total / state.search.pageSize));
    host.innerHTML =
      '<div class="table-wrap"><table><thead><tr>' +
      '<th><input type="checkbox" id="pick-all" aria-label="Select page"></th>' +
      "<th>Name</th><th>Title</th><th>Company</th><th>Location</th>" +
      "<th>Work email</th><th>Direct email</th><th>Phone</th><th></th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div>" +
      '<div class="pagination"><span>' +
      fmt(state.search.total) +
      " people · page " +
      state.search.page +
      " of " +
      pages +
      " · search is free</span>" +
      '<div class="btn-group">' +
      '<button class="btn ghost sm" id="page-prev"' +
      (state.search.page <= 1 ? " disabled" : "") +
      ">Previous</button>" +
      '<button class="btn ghost sm" id="page-next"' +
      (state.search.page >= pages ? " disabled" : "") +
      ">Next</button></div></div>";

    const bulk = $("#bulk-reveal");
    const syncBulk = () => {
      if (bulk) bulk.disabled = state.selection.size === 0;
      if (bulk) bulk.textContent = "Reveal selected" + (state.selection.size ? " (" + state.selection.size + ")" : "");
    };
    syncBulk();

    $$("[data-pick]", host).forEach((box) => {
      box.addEventListener("change", () => {
        if (box.checked) state.selection.add(box.dataset.pick);
        else state.selection.delete(box.dataset.pick);
        syncBulk();
      });
    });
    const all = $("#pick-all");
    if (all) {
      all.addEventListener("change", () => {
        $$("[data-pick]", host).forEach((box) => {
          box.checked = all.checked;
          if (all.checked) state.selection.add(box.dataset.pick);
          else state.selection.delete(box.dataset.pick);
        });
        syncBulk();
      });
    }
    $$("[data-reveal]", host).forEach((btn) => {
      btn.addEventListener("click", () => revealOne(btn.dataset.reveal, btn));
    });
    $$("[data-open]", host).forEach((btn) => {
      btn.addEventListener("click", () => go("#/contact/" + btn.dataset.open));
    });
    $("#page-prev").addEventListener("click", () => runSearch(state.search.page - 1));
    $("#page-next").addEventListener("click", () => runSearch(state.search.page + 1));
  }

  async function revealOne(id, btn) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Revealing...";
    }
    try {
      const data = await api("/api/contacts/" + encodeURIComponent(id) + "/reveal", { method: "POST" });
      if (state.workspace && data.balance !== undefined) {
        state.workspace.credits = data.balance;
        paintCredits();
      }
      const spent = Number(data.creditsSpent || 0);
      const found = data.contact && (data.contact.workEmail || data.contact.directEmail);
      if (!spent && !found) toast("No email found, no credit used", "info");
      else if (!spent) toast("Already revealed — no credit used", "info");
      else toast("Revealed. 1 credit used.", "success");
      go("#/contact/" + id);
    } catch (err) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Reveal";
      }
      toast(err.message, "error");
    }
  }

  async function bulkReveal() {
    const ids = Array.from(state.selection);
    if (!ids.length) return;
    const btn = $("#bulk-reveal");
    if (btn) btn.disabled = true;
    try {
      const data = await api("/api/contacts/reveal-bulk", { method: "POST", body: { contactIds: ids } });
      if (state.workspace && data.balance !== undefined) {
        state.workspace.credits = data.balance;
        paintCredits();
      }
      const spent = Number(data.creditsSpent || 0);
      const misses = (data.results || []).filter((r) => r.ok && r.found === false).length;
      if (!spent) toast("No email found, no credit used", "info");
      else toast("Revealed " + spent + " with an email. " + spent + " credit" + (spent === 1 ? "" : "s") + " used." + (misses ? " " + misses + " had no email and were not charged." : ""), "success");
      state.selection.clear();
      await runSearch(state.search.page);
    } catch (err) {
      if (btn) btn.disabled = false;
      toast(err.message, "error");
    }
  }

  /* ---------------- Contact detail ---------------- */

  async function renderContact(view, id) {
    if (!id) {
      view.innerHTML = '<div class="empty-state"><div class="empty-title">No contact selected</div></div>';
      return;
    }
    const [contact, folders] = await Promise.all([
      api("/api/contacts/" + encodeURIComponent(id)),
      api("/api/folders"),
    ]);
    const name = contact.name || [contact.firstName, contact.lastName].filter(Boolean).join(" ");
    const folderOpts = (folders.items || [])
      .map((f) => '<option value="' + esc(f.id) + '">' + esc(f.name) + "</option>")
      .join("");

    const work = contact.revealed ? contact.workEmail : contact.workEmailMasked;
    const direct = contact.revealed ? contact.directEmail : contact.directEmailMasked;

    view.innerHTML =
      '<div class="view-header"><div><h3>' +
      esc(name) +
      "</h3><p>" +
      esc(contact.title || "") +
      (contact.companyName ? " · " + esc(contact.companyName) : "") +
      "</p></div>" +
      '<div class="btn-group">' +
      (contact.revealed
        ? ""
        : '<button class="btn primary" id="detail-reveal">Reveal</button>') +
      '<a class="btn ghost" href="#/prospector">Back to search</a></div></div>' +
      '<div class="detail-grid"><div class="detail-section">' +
      '<div class="panel"><div class="panel-header"><h4>Work email</h4>' +
      scoreChip(contact.workEmailScore) +
      "</div>" +
      '<div class="kv-row"><span class="kv-key">Address</span><span class="kv-val mono">' +
      esc(work || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Status</span><span class="kv-val">' +
      badge(contact.workEmailStatus || (contact.revealed ? "unavailable" : "unknown")) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Pattern</span><span class="kv-val mono">' +
      esc(contact.workEmailPattern || "—") +
      "</span></div></div>" +
      '<div class="panel"><div class="panel-header"><h4>Direct email</h4>' +
      scoreChip(contact.directEmailScore) +
      "</div>" +
      '<div class="kv-row"><span class="kv-key">Address</span><span class="kv-val mono">' +
      esc(direct || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Status</span><span class="kv-val">' +
      badge(contact.directEmailStatus || "unavailable") +
      "</span></div></div>" +
      '<div class="panel"><div class="panel-header"><h4>Phone</h4></div>' +
      '<div class="kv-row"><span class="kv-key">Number</span><span class="kv-val mono">' +
      esc(contact.phone || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Source</span><span class="kv-val">' +
      esc(contact.phoneSource || "—") +
      "</span></div></div></div>" +
      '<div class="detail-section">' +
      '<div class="panel"><div class="panel-header"><h4>Company</h4></div>' +
      '<div class="kv-row"><span class="kv-key">Name</span><span class="kv-val">' +
      esc(contact.companyName || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Domain</span><span class="kv-val mono">' +
      esc(contact.domain || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Industry</span><span class="kv-val">' +
      esc(contact.industry || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Headcount</span><span class="kv-val">' +
      esc(contact.headcount || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Location</span><span class="kv-val">' +
      esc(contact.location || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">LinkedIn</span><span class="kv-val">' +
      (contact.linkedinUrl
        ? '<a href="' + esc(contact.linkedinUrl) + '" target="_blank" rel="noopener">Profile</a>'
        : "—") +
      "</span></div></div>" +
      '<div class="panel"><div class="panel-header"><h4>Add to folder</h4></div>' +
      (folderOpts
        ? '<div class="btn-group"><select class="select" id="folder-pick">' +
          folderOpts +
          '</select><button class="btn primary" id="folder-add">Add</button></div>'
        : '<p class="subtitle">Create a list first.</p>') +
      "</div></div></div>";

    const revealBtn = $("#detail-reveal");
    if (revealBtn) revealBtn.addEventListener("click", () => revealOne(id, revealBtn));
    const addBtn = $("#folder-add");
    if (addBtn) {
      addBtn.addEventListener("click", async () => {
        const folderId = $("#folder-pick").value;
        try {
          const out = await api("/api/folders/" + encodeURIComponent(folderId) + "/contacts", {
            method: "POST",
            body: { contactIds: [id] },
          });
          toast(out.added ? "Added to folder" : "Already in that folder", "success");
        } catch (err) {
          toast(err.message, "error");
        }
      });
    }
  }

  /* ---------------- Lists ---------------- */

  async function renderLists(view, folderId) {
    const data = await api("/api/folders");
    const items = data.items || [];
    if (folderId) {
      await renderFolder(view, folderId, items);
      return;
    }
    const rows = items.length
      ? items
          .map(
            (f) =>
              "<tr>" +
              '<td><span class="swatch" style="background:' +
              esc(f.color || "#6ee7b7") +
              '"></span> ' +
              esc(f.name) +
              "</td>" +
              "<td>" +
              fmt(f.count) +
              "</td>" +
              "<td>" +
              esc((f.createdAt || "").slice(0, 10)) +
              "</td>" +
              '<td class="btn-group"><button class="btn ghost sm" data-open="' +
              esc(f.id) +
              '">Open</button>' +
              '<button class="btn danger sm" data-del="' +
              esc(f.id) +
              '">Delete</button></td></tr>'
          )
          .join("")
      : '<tr><td colspan="4" class="loading-row">No folders yet. Create one to start a list.</td></tr>';

    view.innerHTML =
      '<div class="view-header"><div><h3>Lists</h3><p>Folders of saved contacts.</p></div></div>' +
      '<div class="panel"><div class="form-grid" style="grid-template-columns:1fr 140px auto;align-items:end">' +
      '<div class="field"><label for="folder-name">Name</label><input class="input" id="folder-name" placeholder="Outbound Q4"></div>' +
      '<div class="field"><label for="folder-color">Color</label><input class="input" id="folder-color" type="color" value="#6ee7b7"></div>' +
      '<button class="btn primary" id="folder-create">Create folder</button></div></div>' +
      '<div class="table-wrap"><table><thead><tr><th>Folder</th><th>Contacts</th><th>Created</th><th></th></tr></thead><tbody>' +
      rows +
      "</tbody></table></div>";

    $("#folder-create").addEventListener("click", async () => {
      const name = $("#folder-name").value.trim();
      if (!name) {
        toast("Name is required", "error");
        return;
      }
      try {
        await api("/api/folders", {
          method: "POST",
          body: { name, color: $("#folder-color").value || "#6ee7b7" },
        });
        toast("Folder created", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    });
    $$("[data-open]", view).forEach((b) => b.addEventListener("click", () => go("#/lists/" + b.dataset.open)));
    $$("[data-del]", view).forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Delete this folder? Contacts stay in the workspace.")) return;
        try {
          await api("/api/folders/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
          toast("Folder deleted", "success");
          render();
        } catch (err) {
          toast(err.message, "error");
        }
      })
    );
  }

  async function renderFolder(view, folderId, folders) {
    const meta = (folders || []).find((f) => f.id === folderId);
    const data = await api("/api/contacts?folderId=" + encodeURIComponent(folderId) + "&pageSize=100");
    const items = data.items || [];
    const rows = items.length
      ? items
          .map((c) => {
            const name = c.name || [c.firstName, c.lastName].filter(Boolean).join(" ");
            const email = c.revealed ? c.workEmail || c.workEmailMasked : c.workEmailMasked;
            return (
              "<tr><td><div class=\"table-person\"><span class=\"avatar-initials\">" +
              esc(initials(name)) +
              '</span><div class="person-name">' +
              esc(name) +
              "</div></div></td><td>" +
              esc(c.title || "") +
              "</td><td>" +
              esc(c.companyName || "") +
              '</td><td class="mono">' +
              esc(email || "—") +
              "</td><td>" +
              '<div class="btn-group"><button class="btn ghost sm" data-open="' +
              esc(c.id) +
              '">Open</button>' +
              '<button class="btn danger sm" data-remove="' +
              esc(c.id) +
              '">Remove</button></div></td></tr>'
            );
          })
          .join("")
      : '<tr><td colspan="5" class="loading-row">This folder is empty.</td></tr>';

    view.innerHTML =
      '<div class="view-header"><div><h3>' +
      esc(meta ? meta.name : "Folder") +
      "</h3><p>" +
      fmt(items.length) +
      ' contacts</p></div><a class="btn ghost" href="#/lists">All lists</a></div>' +
      '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Title</th><th>Company</th><th>Email</th><th></th></tr></thead><tbody>' +
      rows +
      "</tbody></table></div>";

    $$("[data-open]", view).forEach((b) => b.addEventListener("click", () => go("#/contact/" + b.dataset.open)));
    $$("[data-remove]", view).forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(
            "/api/folders/" + encodeURIComponent(folderId) + "/contacts/" + encodeURIComponent(b.dataset.remove),
            { method: "DELETE" }
          );
          toast("Removed from folder", "success");
          render();
        } catch (err) {
          toast(err.message, "error");
        }
      })
    );
  }

  /* ---------------- Enrich ---------------- */

  async function renderEnrich(view) {
    const tab = state.enrichTab || "linkedin";
    view.innerHTML =
      '<div class="view-header"><div><h3>Enrich</h3><p>Turn LinkedIn URLs or a CSV into emails. A credit is spent only when an email is found.</p></div></div>' +
      '<div class="tabs"><button class="tab-btn' +
      (tab === "linkedin" ? " active" : "") +
      '" data-tab="linkedin">LinkedIn URLs</button>' +
      '<button class="tab-btn' +
      (tab === "csv" ? " active" : "") +
      '" data-tab="csv">CSV</button></div>' +
      '<div id="enrich-body"></div>' +
      '<div id="job-panel"></div>';

    $$("[data-tab]", view).forEach((b) =>
      b.addEventListener("click", () => {
        state.enrichTab = b.dataset.tab;
        render();
      })
    );
    if (tab === "csv") paintCsv();
    else paintLinkedIn();
    if (state.enrichJob) paintJob(state.enrichJob);
  }

  function paintLinkedIn() {
    $("#enrich-body").innerHTML =
      '<div class="panel"><div class="field"><label for="li-urls">One LinkedIn profile URL per line</label>' +
      '<textarea class="textarea" id="li-urls" rows="8" placeholder="https://www.linkedin.com/in/..."></textarea></div>' +
      '<div class="btn-group" style="margin-top:12px"><button class="btn primary" id="li-go">Start enrichment</button></div>' +
      '<p id="li-error" class="form-error"></p></div>';
    $("#li-go").addEventListener("click", async () => {
      const urls = $("#li-urls")
        .value.split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (!urls.length) {
        $("#li-error").textContent = "Paste at least one URL.";
        return;
      }
      $("#li-error").textContent = "";
      try {
        const job = await api("/api/enrich/linkedin", { method: "POST", body: { urls } });
        state.enrichJob = job;
        toast("Job queued", "success");
        paintJob(job);
        pollJob(job.id);
      } catch (err) {
        $("#li-error").textContent = err.message;
        toast(err.message, "error");
      }
    });
  }

  function paintCsv() {
    $("#enrich-body").innerHTML =
      '<div class="panel"><div class="form-grid">' +
      '<div class="field"><label for="csv-mode">Mode</label><select class="select" id="csv-mode">' +
      '<option value="url">LinkedIn URL column</option>' +
      '<option value="lead">First name, last name, company</option>' +
      '<option value="email">Email column (reverse lookup)</option></select></div>' +
      '<div class="field"><label for="csv-file">CSV file</label><input class="input" id="csv-file" type="file" accept=".csv,text/csv"></div></div>' +
      '<div class="btn-group" style="margin-top:12px">' +
      '<button class="btn primary" id="csv-go">Upload and enrich</button>' +
      '<a class="btn ghost" id="csv-template" href="/api/csv/template?mode=url">Download template</a></div>' +
      '<p id="csv-error" class="form-error"></p></div>';
    $("#csv-mode").addEventListener("change", () => {
      $("#csv-template").href = "/api/csv/template?mode=" + encodeURIComponent($("#csv-mode").value);
    });
    $("#csv-go").addEventListener("click", async () => {
      const file = $("#csv-file").files && $("#csv-file").files[0];
      if (!file) {
        $("#csv-error").textContent = "Choose a CSV file.";
        return;
      }
      $("#csv-error").textContent = "";
      const body = new FormData();
      body.append("file", file);
      body.append("mode", $("#csv-mode").value);
      try {
        const job = await api("/api/csv/enrich", { method: "POST", body });
        state.enrichJob = job;
        toast("CSV job queued", "success");
        paintJob(job);
        pollJob(job.id);
      } catch (err) {
        $("#csv-error").textContent = err.message;
        toast(err.message, "error");
      }
    });
  }

  function paintJob(job) {
    const host = $("#job-panel");
    if (!host || !job) return;
    const total = Number(job.total || 0);
    const processed = Number(job.processed || 0);
    const width = total ? Math.round((processed / total) * 100) : job.status === "done" ? 100 : 8;
    const done = job.status === "done" || job.status === "failed";
    host.innerHTML =
      '<div class="panel"><div class="panel-header"><h4>Job ' +
      esc((job.id || "").slice(0, 8)) +
      "</h4>" +
      badge(job.status) +
      "</div>" +
      '<div class="bar tall"><span style="width:' +
      width +
      '%"></span></div>' +
      '<div class="kv-row"><span class="kv-key">Progress</span><span class="kv-val">' +
      fmt(processed) +
      " / " +
      fmt(total) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Found</span><span class="kv-val">' +
      fmt(job.found) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Credits spent</span><span class="kv-val">' +
      fmt(job.creditsSpent) +
      "</span></div>" +
      (job.error ? '<p class="form-error">' + esc(job.error) + "</p>" : "") +
      (done && (job.downloadUrl || job.status === "done")
        ? '<div class="btn-group" style="margin-top:12px"><button class="btn secondary" id="job-download">Download CSV</button></div>'
        : "") +
      "</div>";
    const dl = $("#job-download");
    if (dl) {
      dl.addEventListener("click", () => downloadJob(job.id));
    }
  }

  async function pollJob(id) {
    stopPoll();
    const tick = async () => {
      if (state.route !== "enrich") return;
      try {
        const job = await api("/api/jobs/" + encodeURIComponent(id));
        state.enrichJob = job;
        paintJob(job);
        if (job.status === "done" || job.status === "failed") {
          if (state.workspace) {
            try {
              await refreshMe();
            } catch (e) {
              /* ignore */
            }
          }
          toast(job.status === "done" ? "Enrichment finished. " + fmt(job.found) + " found, " + fmt(job.creditsSpent) + " credits spent." : "Job failed", job.status === "done" ? "success" : "error");
          return;
        }
        state.pollTimer = setTimeout(tick, 1500);
      } catch (err) {
        toast(err.message, "error");
      }
    };
    state.pollTimer = setTimeout(tick, 800);
  }

  async function downloadJob(id) {
    try {
      const res = await api("/api/jobs/" + encodeURIComponent(id) + "/download");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "lead-engine-" + id.slice(0, 8) + ".csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  /* ---------------- Verifier ---------------- */

  async function renderVerifier(view) {
    view.innerHTML =
      '<div class="view-header"><div><h3>Verifier</h3><p>Check up to 100 addresses. Each check uses the verifier credit pool, not reveal credits.</p></div></div>' +
      '<div class="panel"><div class="field"><label for="v-emails">Emails, one per line</label>' +
      '<textarea class="textarea" id="v-emails" rows="8" placeholder="maya.okafor@northvale.io"></textarea></div>' +
      '<div class="btn-group" style="margin-top:12px"><button class="btn primary" id="v-go">Verify</button>' +
      '<span class="subtitle" id="v-balance">Verifier credits: ' +
      fmt(state.workspace && state.workspace.verifierCredits) +
      "</span></div>" +
      '<p id="v-error" class="form-error"></p></div>' +
      '<div id="v-results"></div>';

    $("#v-go").addEventListener("click", async () => {
      const emails = $("#v-emails")
        .value.split(/[\s,;]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (!emails.length) {
        $("#v-error").textContent = "Paste at least one email.";
        return;
      }
      if (emails.length > 100) {
        $("#v-error").textContent = "Maximum 100 emails per check.";
        return;
      }
      $("#v-error").textContent = "";
      $("#v-results").innerHTML = '<div class="panel loading-row">Loading...</div>';
      try {
        const data = await api("/api/verify", { method: "POST", body: { emails } });
        if (state.workspace && data.verifierBalance !== undefined) {
          state.workspace.verifierCredits = data.verifierBalance;
          paintCredits();
          $("#v-balance").textContent = "Verifier credits: " + fmt(data.verifierBalance);
        }
        const rows = (data.items || [])
          .map(
            (item) =>
              "<tr><td class=\"mono\">" +
              esc(item.email) +
              "</td><td>" +
              badge(item.result) +
              "</td><td>" +
              (item.score ? scoreChip(item.score) : "—") +
              '</td><td class="mono">' +
              esc(item.mxHost || "—") +
              "</td><td>" +
              esc(item.reason || item.note || "") +
              "</td></tr>"
          )
          .join("");
        $("#v-results").innerHTML =
          '<div class="panel" style="margin-bottom:12px">Verifier credits spent: <strong>' +
          fmt(data.creditsSpent) +
          "</strong> · balance " +
          fmt(data.verifierBalance) +
          "</div>" +
          '<div class="table-wrap"><table><thead><tr><th>Email</th><th>Result</th><th>Score</th><th>MX host</th><th>Reason</th></tr></thead><tbody>' +
          (rows || '<tr><td colspan="5" class="loading-row">No results.</td></tr>') +
          "</tbody></table></div>";
        toast("Verified " + (data.items || []).length + ". " + fmt(data.creditsSpent) + " verifier credits spent.", "success");
      } catch (err) {
        $("#v-results").innerHTML = "";
        $("#v-error").textContent = err.message;
        toast(err.message, "error");
      }
    });
  }

  /* ---------------- Campaigns ---------------- */

  async function renderCampaigns(view) {
    let data;
    try {
      data = await api("/api/campaigns");
    } catch (err) {
      view.innerHTML =
        '<div class="empty-state"><div class="empty-title">Campaigns unavailable</div>' +
        '<div class="empty-text">' +
        esc(err.message) +
        "</div></div>";
      return;
    }
    const items = data.items || [];
    const rows = items.length
      ? items
          .map((c) => {
            const sent = Number(c.sent || 0);
            const openRate = sent ? Math.round((Number(c.opened || 0) / sent) * 100) : 0;
            const clickRate = sent ? Math.round((Number(c.clicked || 0) / sent) * 100) : 0;
            return (
              "<tr><td>" +
              esc(c.name) +
              "</td><td>" +
              badge(c.status || "draft") +
              "</td><td>" +
              fmt(c.stepCount) +
              "</td><td>" +
              fmt(c.recipients) +
              "</td><td>" +
              fmt(c.sent) +
              "</td><td>" +
              openRate +
              "%</td><td>" +
              clickRate +
              '%</td><td><button class="btn ghost sm" data-open="' +
              esc(c.id) +
              '">Open</button></td></tr>'
            );
          })
          .join("")
      : '<tr><td colspan="8" class="loading-row">No campaigns yet.</td></tr>';

    view.innerHTML =
      '<div class="view-header"><div><h3>Campaigns</h3><p>Sequences are simulated unless SMTP is configured. Send reports queued and simulated counts honestly.</p></div></div>' +
      '<div class="panel"><div class="form-grid" style="grid-template-columns:1fr 1fr 1fr auto;align-items:end">' +
      '<div class="field"><label for="c-name">Name</label><input class="input" id="c-name" placeholder="Talent outbound"></div>' +
      '<div class="field"><label for="c-from">From email</label><input class="input" id="c-from" placeholder="you@company.com"></div>' +
      '<div class="field"><label for="c-from-name">From name</label><input class="input" id="c-from-name" placeholder="Alex"></div>' +
      '<button class="btn primary" id="c-create">Create</button></div>' +
      '<p id="c-error" class="form-error"></p></div>' +
      '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Status</th><th>Steps</th><th>Recipients</th><th>Sent</th><th>Open</th><th>Click</th><th></th></tr></thead><tbody>' +
      rows +
      "</tbody></table></div>";

    $("#c-create").addEventListener("click", async () => {
      const name = $("#c-name").value.trim();
      if (!name) {
        $("#c-error").textContent = "Name is required.";
        return;
      }
      try {
        const created = await api("/api/campaigns", {
          method: "POST",
          body: {
            name,
            fromEmail: $("#c-from").value.trim() || undefined,
            fromName: $("#c-from-name").value.trim() || undefined,
          },
        });
        toast("Campaign created", "success");
        go("#/campaign/" + (created.id || ""));
      } catch (err) {
        $("#c-error").textContent = err.message;
        toast(err.message, "error");
      }
    });
    $$("[data-open]", view).forEach((b) => b.addEventListener("click", () => go("#/campaign/" + b.dataset.open)));
  }

  async function renderCampaign(view, id) {
    if (!id) {
      view.innerHTML = '<div class="empty-state"><div class="empty-title">Missing campaign</div></div>';
      return;
    }
    const [detail, contacts] = await Promise.all([
      api("/api/campaigns/" + encodeURIComponent(id)),
      api("/api/contacts?page=1&pageSize=50"),
    ]);
    const campaign = detail.campaign || detail;
    const steps = detail.steps || [];
    const stats = detail.stats || {};
    const revealed = (contacts.items || []).filter((c) => c.revealed && (c.workEmail || c.directEmail));

    const stepRows = steps.length
      ? steps
          .map(
            (s, i) =>
              '<div class="panel step"><div class="panel-header"><h4>Step ' +
              (s.position || i + 1) +
              '</h4><button class="btn danger sm" data-step="' +
              esc(s.id) +
              '">Remove</button></div>' +
              '<div class="kv-row"><span class="kv-key">Subject</span><span class="kv-val">' +
              esc(s.subject || "") +
              "</span></div>" +
              '<div class="kv-row"><span class="kv-key">Delay</span><span class="kv-val">' +
              fmt(s.delayDays || 0) +
              " days</span></div>" +
              '<pre class="code-box">' +
              esc(s.body || "") +
              "</pre></div>"
          )
          .join("")
      : '<div class="empty-state"><div class="empty-text">No steps yet. Add up to 10.</div></div>';

    const contactOpts = revealed.length
      ? revealed
          .map((c) => {
            const name = c.name || [c.firstName, c.lastName].filter(Boolean).join(" ");
            return (
              '<label class="field-checkbox" style="padding-top:4px"><input type="checkbox" data-recipient="' +
              esc(c.id) +
              '"> ' +
              esc(name) +
              " · " +
              esc(c.workEmail || c.directEmail) +
              "</label>"
            );
          })
          .join("")
      : '<p class="subtitle">Reveal a contact with an email before adding recipients.</p>';

    view.innerHTML =
      '<div class="view-header"><div><h3>' +
      esc(campaign.name || "Campaign") +
      "</h3><p>" +
      badge(campaign.status || "draft") +
      " · from " +
      esc(campaign.fromEmail || "not set") +
      "</p></div>" +
      '<div class="btn-group"><button class="btn primary" id="camp-send">Send</button>' +
      '<button class="btn danger" id="camp-delete">Delete</button>' +
      '<a class="btn ghost" href="#/campaigns">All campaigns</a></div></div>' +
      '<div class="stat-grid">' +
      tile("Recipients", stats.recipients || 0, "added") +
      tile("Sent", stats.sent || 0, "messages") +
      tile("Opened", stats.opened || 0, "opens") +
      tile("Clicked", stats.clicked || 0, "clicks") +
      "</div>" +
      '<div id="send-result"></div>' +
      "<h4>Steps (" +
      steps.length +
      "/10)</h4>" +
      stepRows +
      (steps.length < 10
        ? '<div class="panel"><div class="panel-header"><h4>Add step</h4></div>' +
          '<div class="form-grid"><div class="field"><label for="step-subject">Subject</label><input class="input" id="step-subject"></div>' +
          '<div class="field"><label for="step-delay">Delay days</label><input class="input" id="step-delay" type="number" min="0" value="0"></div></div>' +
          '<div class="field" style="margin-top:10px"><label for="step-body">Body</label><textarea class="textarea" id="step-body" rows="5"></textarea></div>' +
          '<button class="btn primary" id="step-add" style="margin-top:12px">Add step</button>' +
          '<p id="step-error" class="form-error"></p></div>'
        : "") +
      '<div class="panel"><div class="panel-header"><h4>Add recipients</h4><span class="subtitle">Revealed contacts with an email</span></div>' +
      contactOpts +
      '<button class="btn secondary" id="recip-add" style="margin-top:12px">Add selected</button></div>';

    const addStep = $("#step-add");
    if (addStep) {
      addStep.addEventListener("click", async () => {
        const subject = $("#step-subject").value.trim();
        const body = $("#step-body").value.trim();
        if (!subject || !body) {
          $("#step-error").textContent = "Subject and body are required.";
          return;
        }
        try {
          await api("/api/campaigns/" + encodeURIComponent(id) + "/steps", {
            method: "POST",
            body: { subject, body, delayDays: Number($("#step-delay").value || 0) },
          });
          toast("Step added", "success");
          render();
        } catch (err) {
          $("#step-error").textContent = err.message;
          toast(err.message, "error");
        }
      });
    }
    $$("[data-step]", view).forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(
            "/api/campaigns/" + encodeURIComponent(id) + "/steps/" + encodeURIComponent(b.dataset.step),
            { method: "DELETE" }
          );
          toast("Step removed", "success");
          render();
        } catch (err) {
          toast(err.message, "error");
        }
      })
    );
    $("#recip-add").addEventListener("click", async () => {
      const ids = $$("[data-recipient]", view)
        .filter((el) => el.checked)
        .map((el) => el.dataset.recipient);
      if (!ids.length) {
        toast("Select at least one contact", "error");
        return;
      }
      try {
        const out = await api("/api/campaigns/" + encodeURIComponent(id) + "/recipients", {
          method: "POST",
          body: { contactIds: ids },
        });
        toast("Added " + fmt(out.added) + " recipients", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    });
    $("#camp-send").addEventListener("click", async () => {
      const btn = $("#camp-send");
      btn.disabled = true;
      try {
        const out = await api("/api/campaigns/" + encodeURIComponent(id) + "/send", { method: "POST" });
        const queued = out.queued;
        const simulated = out.simulated;
        $("#send-result").innerHTML =
          '<div class="panel">Send result: <strong>' +
          fmt(queued) +
          "</strong> queued" +
          (simulated === true || simulated === "true"
            ? " · simulated (nothing was emailed)"
            : simulated === false
              ? " · sent via SMTP"
              : "") +
          ".</div>";
        toast(
          simulated ? "Queued " + fmt(queued) + ". Simulated — no email was sent." : "Queued " + fmt(queued) + ".",
          "success"
        );
        await refreshMe().catch(() => {});
      } catch (err) {
        toast(err.message, "error");
        $("#send-result").innerHTML = '<p class="form-error">' + esc(err.message) + "</p>";
      } finally {
        btn.disabled = false;
      }
    });
    $("#camp-delete").addEventListener("click", async () => {
      if (!confirm("Delete this campaign?")) return;
      try {
        await api("/api/campaigns/" + encodeURIComponent(id), { method: "DELETE" });
        toast("Campaign deleted", "success");
        go("#/campaigns");
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  /* ---------------- API & MCP ---------------- */

  async function renderApi(view) {
    const data = await api("/api/api-keys");
    const items = data.items || [];
    const primary = items[0];
    const rows = items.length
      ? items
          .map(
            (k) =>
              "<tr><td>" +
              esc(k.label || "default") +
              '</td><td class="mono">' +
              esc(k.key) +
              '</td><td>' +
              fmt(k.callsToday) +
              " / " +
              fmt(k.callsLimit) +
              "</td><td>" +
              esc((k.createdAt || "").slice(0, 10)) +
              '</td><td><div class="btn-group"><button class="btn ghost sm" data-copy-key="' +
              esc(k.key) +
              '">Copy</button><button class="btn danger sm" data-del-key="' +
              esc(k.id) +
              '">Delete</button></div></td></tr>'
          )
          .join("")
      : '<tr><td colspan="5" class="loading-row">No API keys yet.</td></tr>';

    const key = primary ? primary.key : "YOUR_API_KEY";
    const origin = location.origin;
    const curl =
      "curl -s " +
      origin +
      "/api/search \\\n  -H \"Authorization: Bearer " +
      key +
      "\" \\\n  -H \"Content-Type: application/json\" \\\n  -d '{\"filters\":{\"title\":\"Head of Talent\"},\"page\":1}'";
    const mcp = JSON.stringify(
      {
        mcpServers: {
          "lead-engine": {
            url: origin + "/mcp",
            headers: { Authorization: "Bearer " + key },
          },
        },
      },
      null,
      2
    );
    const endpoints = [
      "POST /api/auth/login",
      "POST /api/auth/logout",
      "GET /api/auth/me",
      "POST /api/auth/signup",
      "GET /api/workspace",
      "GET /api/credits/ledger",
      "POST /api/search",
      "GET /api/search/suggestions",
      "POST /api/contacts/{id}/reveal",
      "POST /api/contacts/reveal-bulk",
      "GET /api/contacts",
      "POST /api/contacts",
      "DELETE /api/contacts/{id}",
      "POST /api/contacts/import",
      "GET /api/folders",
      "POST /api/folders",
      "PATCH /api/folders/{id}",
      "DELETE /api/folders/{id}",
      "POST /api/folders/{id}/contacts",
      "DELETE /api/folders/{id}/contacts/{contactId}",
      "POST /api/enrich/linkedin",
      "POST /api/enrich/identity",
      "POST /api/enrich/reverse-email",
      "GET /api/jobs",
      "GET /api/jobs/{id}",
      "GET /api/jobs/{id}/download",
      "POST /api/csv/enrich",
      "GET /api/csv/template",
      "POST /api/verify",
      "GET /api/campaigns",
      "POST /api/campaigns",
      "GET /api/campaigns/{id}",
      "PATCH /api/campaigns/{id}",
      "DELETE /api/campaigns/{id}",
      "POST /api/campaigns/{id}/steps",
      "DELETE /api/campaigns/{id}/steps/{stepId}",
      "POST /api/campaigns/{id}/recipients",
      "POST /api/campaigns/{id}/send",
      "GET /api/campaigns/{id}/events",
      "GET /api/api-keys",
      "POST /api/api-keys",
      "DELETE /api/api-keys/{id}",
      "POST /mcp",
      "GET /health",
    ];

    view.innerHTML =
      '<div class="view-header"><div><h3>API & MCP</h3><p>Machine clients send the key as Authorization: Bearer or x-api-key.</p></div>' +
      '<div class="btn-group"><input class="input" id="key-label" placeholder="Label" value="default">' +
      '<button class="btn primary" id="key-create">Create key</button></div></div>' +
      '<div class="panel"><div class="panel-header"><h4>Workspace key</h4>' +
      (primary ? '<button class="btn ghost sm" id="copy-primary">Copy</button>' : "") +
      '</div><div class="code-box" id="primary-key">' +
      esc(primary ? primary.key : "No key yet") +
      "</div></div>" +
      '<div class="table-wrap"><table><thead><tr><th>Label</th><th>Key</th><th>Calls today</th><th>Created</th><th></th></tr></thead><tbody>' +
      rows +
      "</tbody></table></div>" +
      '<div class="panel"><div class="panel-header"><h4>curl</h4><button class="btn ghost sm" data-copy="curl">Copy</button></div>' +
      '<pre class="code-box" id="snip-curl">' +
      esc(curl) +
      "</pre></div>" +
      '<div class="panel"><div class="panel-header"><h4>MCP client</h4><button class="btn ghost sm" data-copy="mcp">Copy</button></div>' +
      '<p class="subtitle" style="margin-bottom:8px">POST /mcp speaks JSON-RPC 2.0. Tools: search_prospects, reveal_contact, enrich_linkedin, verify_email, list_folders, check_credits.</p>' +
      '<pre class="code-box" id="snip-mcp">' +
      esc(mcp) +
      "</pre></div>" +
      '<div class="panel"><div class="panel-header"><h4>Endpoints</h4></div><ul class="endpoint-list">' +
      endpoints.map((e) => "<li><code>" + esc(e) + "</code></li>").join("") +
      "</ul></div>";

    const copyText = async (text) => {
      try {
        await navigator.clipboard.writeText(text);
        toast("Copied", "success");
      } catch (err) {
        toast("Copy failed", "error");
      }
    };
    const primaryBtn = $("#copy-primary");
    if (primaryBtn && primary) primaryBtn.addEventListener("click", () => copyText(primary.key));
    $$("[data-copy-key]", view).forEach((b) => b.addEventListener("click", () => copyText(b.dataset.copyKey)));
    $$("[data-copy]", view).forEach((b) =>
      b.addEventListener("click", () => copyText($("#snip-" + b.dataset.copy).textContent))
    );
    $("#key-create").addEventListener("click", async () => {
      try {
        await api("/api/api-keys", { method: "POST", body: { label: $("#key-label").value.trim() || "default" } });
        toast("Key created", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    });
    $$("[data-del-key]", view).forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Delete this API key?")) return;
        try {
          await api("/api/api-keys/" + encodeURIComponent(b.dataset.delKey), { method: "DELETE" });
          toast("Key deleted", "success");
          render();
        } catch (err) {
          toast(err.message, "error");
        }
      })
    );
  }

  /* ---------------- Settings ---------------- */

  async function renderSettings(view) {
    const [me, ledger] = await Promise.all([
      refreshMe(),
      api("/api/credits/ledger?limit=20").catch(() => ({ items: [] })),
    ]);
    const u = me.user || {};
    const ws = me.workspace || {};
    const events = (ledger.items || [])
      .map(
        (e) =>
          "<tr><td>" +
          esc((e.createdAt || "").replace("T", " ").slice(0, 16)) +
          "</td><td>" +
          esc(e.pool) +
          "</td><td>" +
          esc(e.reason) +
          "</td><td>" +
          fmt(e.delta) +
          "</td><td>" +
          fmt(e.balanceAfter) +
          "</td></tr>"
      )
      .join("");

    view.innerHTML =
      '<div class="view-header"><div><h3>Settings</h3><p>Account and workspace limits.</p></div>' +
      '<button class="btn danger" id="sign-out">Sign out</button></div>' +
      '<div class="detail-grid"><div class="panel"><div class="panel-header"><h4>Account</h4></div>' +
      '<div class="kv-row"><span class="kv-key">Name</span><span class="kv-val">' +
      esc(u.name || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Email</span><span class="kv-val">' +
      esc(u.email || "—") +
      "</span></div></div>" +
      '<div class="panel"><div class="panel-header"><h4>Workspace</h4></div>' +
      '<div class="kv-row"><span class="kv-key">Name</span><span class="kv-val">' +
      esc(ws.name || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Plan</span><span class="kv-val">' +
      esc(ws.plan || "—") +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Credits</span><span class="kv-val">' +
      fmt(ws.credits) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Verifier credits</span><span class="kv-val">' +
      fmt(ws.verifierCredits) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Seats</span><span class="kv-val">' +
      fmt(ws.seatsUsed) +
      " / " +
      fmt(ws.seats) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">Recipients</span><span class="kv-val">' +
      fmt(ws.recipientsUsed) +
      " / " +
      fmt(ws.recipientsLimit) +
      "</span></div>" +
      '<div class="kv-row"><span class="kv-key">API calls / day</span><span class="kv-val">' +
      fmt(ws.apiCallsLimit) +
      "</span></div></div></div>" +
      '<div class="panel" style="padding:0"><div class="panel-header" style="padding:16px 16px 0"><h4>Credit ledger</h4></div>' +
      '<div class="table-wrap" style="border:none"><table><thead><tr><th>When</th><th>Pool</th><th>Reason</th><th>Delta</th><th>Balance</th></tr></thead><tbody>' +
      (events || '<tr><td colspan="5" class="loading-row">No credit events yet.</td></tr>') +
      "</tbody></table></div></div>";

    $("#sign-out").addEventListener("click", async () => {
      try {
        await api("/api/auth/logout", { method: "POST" });
      } catch (err) {
        /* still sign out locally */
      }
      setToken("");
      state.user = null;
      state.workspace = null;
      showLogin("");
    });
  }

  /* ---------------- Boot ---------------- */

  async function boot() {
    if (!state.token) {
      showLogin("");
      return;
    }
    showApp();
    try {
      await refreshMe();
      if (!location.hash) location.hash = "#/dashboard";
      await render();
    } catch (err) {
      if (String(err.message) !== "Not authenticated") {
        setToken("");
        showLogin(err.message || "Could not load the session.");
      }
    }
  }

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#login-submit");
    const errEl = $("#login-error");
    errEl.textContent = "";
    btn.disabled = true;
    try {
      const data = await api("/api/auth/login", {
        method: "POST",
        body: {
          email: $("#login-email").value.trim(),
          password: $("#login-password").value,
        },
      });
      setToken(data.token);
      state.user = data.user;
      state.workspace = data.workspace;
      showApp();
      paintCredits();
      if (!location.hash || location.hash === "#/" || location.hash === "#") location.hash = "#/dashboard";
      await render();
    } catch (err) {
      const msg = String((err && err.message) || "Sign in failed");
      if (/network|failed to fetch|load failed/i.test(msg)) {
        errEl.textContent =
          "Cannot reach the API at " + location.origin +
          ". Start the server with: uv run python scripts/run_server.py";
      } else if (/invalid email or password|401/i.test(msg)) {
        errEl.textContent =
          "Wrong email or password. If you changed it, reset it with scripts/set_password.py.";
      } else if (/disabled|403/i.test(msg)) {
        errEl.textContent = "This account is disabled. Re-enable it with scripts/set_password.py --email " +
          $("login-email").value.trim() + " --password <new>";
      } else {
        errEl.textContent = msg;
      }
    } finally {
      btn.disabled = false;
    }
  });

  $("#nav-toggle").addEventListener("click", () => {
    $(".sidebar").classList.toggle("open");
  });
  $("#nav").addEventListener("click", (e) => {
    if (e.target.closest("a")) $(".sidebar").classList.remove("open");
  });

  window.addEventListener("hashchange", () => {
    if (state.token && !$("#app").hidden) render();
  });

  boot();
})();
