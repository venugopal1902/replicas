/* Blotato Engine — single-page console.
   Same-origin /v2 client. API key lives in localStorage as blotato_key. */

(function () {
  "use strict";

  var KEY_STORE = "blotato_key";
  var TITLES = {
    dashboard: "Dashboard",
    compose: "Compose",
    queue: "Queue",
    inbox: "Inbox",
    automations: "Automations",
    visuals: "Visuals",
    sources: "Sources",
    analytics: "Analytics",
    settings: "Settings"
  };

  var state = {
    key: localStorage.getItem(KEY_STORE) || "",
    route: "dashboard",
    credits: null,
    accounts: [],
    compose: { text: "", media: [], schedule: false, when: "", selected: {} },
    inboxTab: "comments",
    threadId: null,
    metric: "views",
    source: null
  };

  /* ------------------------------------------------------------------ utils */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtNum(n) {
    var v = Number(n) || 0;
    if (v >= 1000000) return (v / 1000000).toFixed(1) + "M";
    if (v >= 1000) return (v / 1000).toFixed(1) + "k";
    return String(v);
  }

  function fmtWhen(iso) {
    if (!iso) return "--";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function clip(text, n) {
    var s = String(text || "").replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n - 1) + "..." : s;
  }

  function errMessage(err) {
    if (!err) return "Request failed";
    if (typeof err === "string") return err;
    if (err.detail) {
      if (typeof err.detail === "string") return err.detail;
      if (err.detail.message) return err.detail.message;
    }
    return err.message || "Request failed";
  }

  function toast(kind, message) {
    var host = $("#toasts");
    var node = document.createElement("div");
    node.className = "toast " + (kind || "info");
    node.innerHTML = "<span>" + esc(message) + "</span>";
    host.appendChild(node);
    setTimeout(function () {
      node.style.opacity = "0";
      setTimeout(function () { node.remove(); }, 200);
    }, 4200);
  }

  function badge(status) {
    var s = String(status || "draft").toLowerCase();
    return '<span class="badge badge-' + esc(s) + '">' + esc(s) + "</span>";
  }

  function pill(platform, active) {
    return '<span class="pill' + (active ? " active" : "") + '"><span class="dot"></span>' + esc(platform) + "</span>";
  }

  function accountById(id) {
    for (var i = 0; i < state.accounts.length; i++) {
      if (String(state.accounts[i].id) === String(id)) return state.accounts[i];
    }
    return null;
  }

  function accountLabel(id) {
    var a = accountById(id);
    if (!a) return id ? String(id).slice(0, 8) : "--";
    return a.name || a.username || a.platform;
  }

  /* ------------------------------------------------------------------- api */

  function api(method, path, body) {
    var headers = { "blotato-api-key": state.key };
    var opts = { method: method, headers: headers };
    if (body !== undefined && body !== null) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (e) { data = { message: text }; }
        }
        if (!res.ok) {
          var err = new Error(errMessage(data) || ("HTTP " + res.status));
          err.status = res.status;
          err.body = data;
          throw err;
        }
        return data;
      });
    });
  }

  function uploadFile(file) {
    var form = new FormData();
    form.append("file", file);
    return fetch("/v2/media/upload", {
      method: "POST",
      headers: { "blotato-api-key": state.key },
      body: form
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) throw new Error(errMessage(data));
        return data;
      });
    });
  }

  function loadAccounts() {
    return api("GET", "/v2/users/me/accounts").then(function (data) {
      state.accounts = (data && data.accounts) || [];
      return state.accounts;
    });
  }

  /* ----------------------------------------------------------------- shell */

  function showGate(message) {
    $("#shell").hidden = true;
    $("#gate").hidden = false;
    var box = $("#gate-error");
    if (message) {
      box.hidden = false;
      box.textContent = message;
    } else {
      box.hidden = true;
    }
    $("#gate-key").value = state.key || "";
    $("#gate-key").focus();
  }

  function showShell() {
    $("#gate").hidden = true;
    $("#shell").hidden = false;
  }

  function paintPlan() {
    var widget = $("#plan-widget");
    var c = state.credits;
    if (!c) {
      widget.innerHTML = '<div class="plan-name">--</div><div class="plan-credits muted">not loaded</div>';
      $("#who").textContent = "";
      return;
    }
    var balance = c.isUnlimited ? "unlimited" : fmtNum(c.balance) + " credits";
    widget.innerHTML =
      '<div class="plan-name">' + esc(c.plan || "plan") + "</div>" +
      '<div class="plan-credits muted">' + esc(balance) + "</div>";
    $("#who").textContent = c.clientName || "";
  }

  function refreshCredits() {
    return api("GET", "/v2/credits").then(function (data) {
      state.credits = data;
      paintPlan();
      return data;
    }).catch(function () { /* footer stays stale; screens toast their own errors */ });
  }

  function setRoute(name) {
    if (!TITLES[name]) name = "dashboard";
    state.route = name;
    if (location.hash !== "#" + name) location.hash = name;
    $("#view-title").textContent = TITLES[name];
    $all("#nav a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-route") === name);
    });
    var view = $("#view");
    view.innerHTML = "";
    var renderers = {
      dashboard: renderDashboard,
      compose: renderCompose,
      queue: renderQueue,
      inbox: renderInbox,
      automations: renderAutomations,
      visuals: renderVisuals,
      sources: renderSources,
      analytics: renderAnalytics,
      settings: renderSettings
    };
    try {
      renderers[name](view);
    } catch (err) {
      view.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
    }
  }

  function boot() {
    if (!state.key) { showGate(); return; }
    showShell();
    api("GET", "/v2/credits").then(function (data) {
      state.credits = data;
      paintPlan();
      return loadAccounts();
    }).then(function () {
      setRoute((location.hash || "#dashboard").slice(1));
    }).catch(function (err) {
      if (err.status === 401) {
        showGate("That key was rejected. " + err.message);
        return;
      }
      showShell();
      toast("error", err.message);
      setRoute((location.hash || "#dashboard").slice(1));
    });
  }

  $("#gate-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var value = $("#gate-key").value.trim();
    if (!value) return;
    state.key = value;
    localStorage.setItem(KEY_STORE, value);
    boot();
  });

  $("#signout").addEventListener("click", function () {
    state.key = "";
    state.credits = null;
    localStorage.removeItem(KEY_STORE);
    showGate();
  });

  window.addEventListener("hashchange", function () {
    if ($("#shell").hidden) return;
    var next = (location.hash || "#dashboard").slice(1);
    if (next !== state.route) setRoute(next);
  });

  /* ------------------------------------------------------------- dashboard */

  function renderDashboard(view) {
    view.innerHTML =
      '<div class="grid-4" id="dash-stats">' + skeletonTiles(4) + "</div>" +
      '<div class="grid-2">' +
        '<section class="card"><div class="card-header"><div class="card-title">Plan</div></div><div id="dash-plan">Loading...</div></section>' +
        '<section class="card"><div class="card-header"><div class="card-title">Accounts</div><a class="btn ghost sm" href="#settings">Manage</a></div><div id="dash-accounts">Loading...</div></section>' +
      "</div>" +
      '<section class="card"><div class="card-header"><div class="card-title">Recent posts</div><a class="btn ghost sm" href="#compose">Compose</a></div><div id="dash-posts">Loading...</div></section>';

    Promise.all([
      api("GET", "/v2/credits"),
      api("GET", "/v2/analytics"),
      loadAccounts(),
      api("GET", "/v2/posts?limit=50")
    ]).then(function (parts) {
      var credits = parts[0];
      var analytics = parts[1] || {};
      var posts = (parts[3] && parts[3].items) || [];
      state.credits = credits;
      paintPlan();
      var ov = analytics.overview || {};
      var scheduled = posts.filter(function (p) { return p.status === "scheduled"; }).length;
      $("#dash-stats").innerHTML =
        stat("Published", fmtNum(ov.totalPublished), "posts live") +
        stat("Scheduled", fmtNum(ov.totalScheduled != null ? ov.totalScheduled : scheduled), "waiting in queue") +
        stat("Total views", fmtNum(ov.totalViews), "across snapshots") +
        stat("Engagement", (parseFloat(ov.averageEngagementRate) || 0).toFixed(1) + "%", "likes + comments + shares / reach");

      var balance = credits.isUnlimited ? "Unlimited" : fmtNum(credits.balance);
      $("#dash-plan").innerHTML =
        '<div class="stat-value">' + esc(balance) + "</div>" +
        '<div class="stat-meta">' + esc(credits.plan) + " plan · " + esc(credits.clientName || "") +
        " · " + fmtNum(credits.rateLimitPerHour) + " req/hr</div>";

      var accHost = $("#dash-accounts");
      if (!state.accounts.length) {
        accHost.innerHTML = '<p class="empty">No accounts yet. Add one in Settings.</p>';
      } else {
        accHost.innerHTML = '<div class="flex-col" style="gap:8px">' + state.accounts.map(function (a) {
          return '<div class="flex-between"><div><strong>' + esc(a.name || a.username || a.platform) +
            '</strong><div class="muted" style="font-size:12px">' + esc(a.username || "") + "</div></div>" +
            pill(a.platform, a.isActive) + "</div>";
        }).join("") + "</div>";
      }

      var postHost = $("#dash-posts");
      if (!posts.length) {
        postHost.innerHTML = '<p class="empty">No posts yet. Publish something from Compose.</p>';
        return;
      }
      postHost.innerHTML = postsTable(posts.slice(0, 8));
    }).catch(function (err) {
      $("#dash-stats").innerHTML = "";
      $("#dash-posts").innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function stat(label, value, meta) {
    return '<div class="stat-tile"><div class="stat-label">' + esc(label) +
      '</div><div class="stat-value">' + esc(value) +
      '</div><div class="stat-meta">' + esc(meta) + "</div></div>";
  }

  function skeletonTiles(n) {
    var html = "";
    for (var i = 0; i < n; i++) html += '<div class="stat-tile"><div class="stat-label">Loading...</div><div class="stat-value">--</div></div>';
    return html;
  }

  function postsTable(items) {
    return '<div class="table-wrap"><table><thead><tr><th>Status</th><th>Platform</th><th>Text</th><th>When</th><th></th></tr></thead><tbody>' +
      items.map(function (p) {
        var when = p.publishedAt || p.scheduledTime || p.createdAt;
        var link = p.url ? '<a class="btn ghost sm" href="' + esc(p.url) + '" target="_blank" rel="noopener">Open</a>' : "";
        return "<tr><td>" + badge(p.status) + "</td><td>" + pill(p.platform) +
          "</td><td>" + esc(clip(p.text, 90)) + "</td><td class=\"muted\">" + esc(fmtWhen(when)) +
          "</td><td>" + link + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  /* --------------------------------------------------------------- compose */

  function renderCompose(view) {
    view.innerHTML =
      '<div class="grid-2">' +
        '<section class="card flex-col">' +
          '<div class="card-title">Post</div>' +
          '<label class="field"><span>Text</span><textarea id="compose-text" rows="8" placeholder="Write the post..."></textarea>' +
          '<div id="char-meter" class="char-meter"><span id="char-count">0</span><span>characters</span></div></label>' +
          '<label class="field"><span>Media URLs</span>' +
            '<div class="flex-row"><input id="media-url" type="text" placeholder="https://..."><button id="media-add" class="btn" type="button">Add URL</button></div>' +
            '<div class="flex-row" style="margin-top:8px"><input id="media-file" type="file" accept="image/*,video/*"><button id="media-upload" class="btn" type="button">Upload file</button></div>' +
          "</label>" +
          '<div id="media-chips" class="media-chips"></div>' +
          '<label class="flex-row"><input id="schedule-toggle" type="checkbox"> <span>Schedule for later</span></label>' +
          '<div id="schedule-row" class="flex-row" hidden>' +
            '<input id="schedule-when" type="datetime-local">' +
            '<button id="next-slot" class="btn sm" type="button">Next free slot</button>' +
          "</div>" +
          '<button id="publish" class="btn primary" type="button">Publish</button>' +
        "</section>" +
        '<section class="card flex-col">' +
          '<div class="flex-between"><div class="card-title">Accounts</div><span class="muted" id="account-count"></span></div>' +
          '<div id="account-selector" class="account-selector">Loading...</div>' +
          '<div class="card-title" style="margin-top:8px">Results</div>' +
          '<div id="publish-results"><p class="empty">Nothing sent yet.</p></div>' +
        "</section>" +
      "</div>";

    var incoming = null;
    try { incoming = JSON.parse(sessionStorage.getItem("blotato_compose") || "null"); } catch (e) { incoming = null; }
    if (incoming && incoming.text) {
      state.compose.text = incoming.text;
      sessionStorage.removeItem("blotato_compose");
    }
    $("#compose-text").value = state.compose.text;
    updateMeter();
    paintMedia();
    $("#schedule-toggle").checked = state.compose.schedule;
    $("#schedule-row").hidden = !state.compose.schedule;
    if (state.compose.when) $("#schedule-when").value = state.compose.when;

    $("#compose-text").addEventListener("input", function () {
      state.compose.text = this.value;
      updateMeter();
    });
    $("#media-add").addEventListener("click", function () {
      var url = $("#media-url").value.trim();
      if (!url) return;
      state.compose.media.push(url);
      $("#media-url").value = "";
      paintMedia();
    });
    $("#media-upload").addEventListener("click", function () {
      var input = $("#media-file");
      if (!input.files || !input.files[0]) { toast("error", "Choose a file first"); return; }
      var btn = this;
      btn.disabled = true;
      btn.textContent = "Uploading...";
      uploadFile(input.files[0]).then(function (data) {
        state.compose.media.push(data.publicUrl);
        paintMedia();
        toast("success", "Uploaded " + (data.contentType || "file"));
        input.value = "";
      }).catch(function (err) {
        toast("error", err.message);
      }).then(function () {
        btn.disabled = false;
        btn.textContent = "Upload file";
      });
    });
    $("#schedule-toggle").addEventListener("change", function () {
      state.compose.schedule = this.checked;
      $("#schedule-row").hidden = !this.checked;
    });
    $("#schedule-when").addEventListener("change", function () { state.compose.when = this.value; });
    $("#next-slot").addEventListener("click", fillNextSlot);
    $("#publish").addEventListener("click", publishFanout);

    var host = $("#account-selector");
    var drawAccounts = function (accounts) {
      if (!accounts.length) {
        host.innerHTML = '<p class="empty">No accounts. Add one in Settings.</p>';
        return;
      }
      host.innerHTML = accounts.map(function (a) {
        var on = !!state.compose.selected[a.id];
        return '<label class="account-option' + (on ? " selected" : "") + '" data-id="' + esc(a.id) + '">' +
          '<input type="checkbox"' + (on ? " checked" : "") + ">" +
          "<div><strong>" + esc(a.name || a.platform) + "</strong>" +
          '<div class="muted" style="font-size:11px">' + esc(a.username || a.platform) + "</div></div>" +
          pill(a.platform, a.isActive) + "</label>";
      }).join("");
      $("#account-count").textContent = accounts.length + " connected";
      $all(".account-option", host).forEach(function (el) {
        el.addEventListener("click", function (ev) {
          ev.preventDefault();
          var id = el.getAttribute("data-id");
          state.compose.selected[id] = !state.compose.selected[id];
          el.classList.toggle("selected", state.compose.selected[id]);
        });
      });
    };
    if (state.accounts.length) drawAccounts(state.accounts);
    else loadAccounts().then(drawAccounts).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function updateMeter() {
    var n = state.compose.text.length;
    var meter = $("#char-meter");
    if (!meter) return;
    $("#char-count").textContent = String(n);
    meter.classList.toggle("warn", n > 220 && n <= 280);
    meter.classList.toggle("over", n > 280);
  }

  function paintMedia() {
    var host = $("#media-chips");
    if (!host) return;
    if (!state.compose.media.length) { host.innerHTML = ""; return; }
    host.innerHTML = state.compose.media.map(function (url, i) {
      return '<span class="media-chip"><span title="' + esc(url) + '">' + esc(clip(url, 42)) +
        '</span><button type="button" data-i="' + i + '" aria-label="Remove">x</button></span>';
    }).join("");
    $all("button", host).forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.compose.media.splice(Number(btn.getAttribute("data-i")), 1);
        paintMedia();
      });
    });
  }

  function fillNextSlot() {
    var ids = Object.keys(state.compose.selected).filter(function (id) { return state.compose.selected[id]; });
    var path = "/v2/schedule-slots/next" + (ids[0] ? "?accountId=" + encodeURIComponent(ids[0]) : "");
    api("GET", path).then(function (data) {
      if (!data || !data.slotTime) { toast("error", "No free slot"); return; }
      var local = toLocalInput(data.slotTime);
      state.compose.schedule = true;
      state.compose.when = local;
      $("#schedule-toggle").checked = true;
      $("#schedule-row").hidden = false;
      $("#schedule-when").value = local;
      toast("success", "Next slot " + fmtWhen(data.slotTime));
    }).catch(function (err) { toast("error", err.message); });
  }

  function toLocalInput(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function publishFanout() {
    var text = state.compose.text.trim();
    var media = state.compose.media.slice();
    if (!text && !media.length) { toast("error", "Add text or at least one media URL"); return; }
    var ids = Object.keys(state.compose.selected).filter(function (id) { return state.compose.selected[id]; });
    if (!ids.length) { toast("error", "Select at least one account"); return; }
    var scheduled = null;
    if (state.compose.schedule) {
      if (!state.compose.when) { toast("error", "Pick a date and time"); return; }
      scheduled = new Date(state.compose.when).toISOString();
    }
    var btn = $("#publish");
    btn.disabled = true;
    var host = $("#publish-results");
    host.innerHTML = '<p class="muted">Sending ' + ids.length + "...</p>";
    var jobs = ids.map(function (id) {
      var account = accountById(id);
      var body = {
        post: {
          accountId: id,
          content: { text: text, mediaUrls: media, platform: account ? account.platform : undefined },
          target: {}
        }
      };
      if (scheduled) body.scheduledTime = scheduled;
      return api("POST", "/v2/posts", body).then(function (data) {
        return { ok: true, id: id, data: data };
      }).catch(function (err) {
        return { ok: false, id: id, error: err.message };
      });
    });
    Promise.all(jobs).then(function (results) {
      host.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Account</th><th>Status</th><th>Link</th></tr></thead><tbody>' +
        results.map(function (r) {
          if (!r.ok) return "<tr><td>" + esc(accountLabel(r.id)) + '</td><td>' + badge("failed") + '</td><td class="inline-error">' + esc(r.error) + "</td></tr>";
          var link = r.data.url ? '<a href="' + esc(r.data.url) + '" target="_blank" rel="noopener">' + esc(r.data.url) + "</a>" : esc(r.data.postSubmissionId || "");
          return "<tr><td>" + esc(accountLabel(r.id)) + "</td><td>" + badge(r.data.status) + "</td><td>" + link + "</td></tr>";
        }).join("") + "</tbody></table></div>";
      var failed = results.filter(function (r) { return !r.ok || r.data.status === "failed"; }).length;
      toast(failed ? "error" : "success", failed ? failed + " of " + results.length + " failed" : "Sent to " + results.length + " account" + (results.length > 1 ? "s" : ""));
      refreshCredits();
      if (!failed) {
        state.compose.text = "";
        state.compose.media = [];
        $("#compose-text").value = "";
        updateMeter();
        paintMedia();
      }
    }).then(function () { btn.disabled = false; }, function () { btn.disabled = false; });
  }

  /* ----------------------------------------------------------------- queue */

  function renderQueue(view) {
    view.innerHTML =
      '<section class="card"><div class="flex-between"><div class="card-title">Scheduled posts</div><button id="queue-reload" class="btn sm" type="button">Reload</button></div><div id="queue-list" style="margin-top:12px">Loading...</div></section>' +
      '<div class="grid-2">' +
        '<section class="card flex-col"><div class="card-title">Add a weekly slot</div>' +
          '<label class="field"><span>Account</span><select id="slot-account"></select></label>' +
          '<div class="grid-3">' +
            '<label class="field"><span>Day</span><select id="slot-day">' +
              ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"].map(function (d, i) {
                return '<option value="' + i + '">' + d + "</option>";
              }).join("") +
            "</select></label>" +
            '<label class="field"><span>Hour</span><input id="slot-hour" type="number" min="0" max="23" value="9"></label>' +
            '<label class="field"><span>Minute</span><input id="slot-minute" type="number" min="0" max="59" value="0"></label>' +
          "</div>" +
          '<button id="slot-add" class="btn primary" type="button">Add slot</button>' +
        "</section>" +
        '<section class="card"><div class="flex-between"><div class="card-title">Weekly slots</div>' +
          '<button id="slot-next" class="btn sm" type="button">Next free slot</button></div>' +
          '<p id="slot-next-out" class="muted" style="margin:8px 0"></p>' +
          '<div id="slot-list">Loading...</div></section>' +
      "</div>";

    var fillSelect = function (accounts) {
      var sel = $("#slot-account");
      if (!accounts.length) { sel.innerHTML = '<option value="">No accounts</option>'; return; }
      sel.innerHTML = accounts.map(function (a) {
        return '<option value="' + esc(a.id) + '" data-platform="' + esc(a.platform) + '">' +
          esc((a.name || a.username || a.platform) + " · " + a.platform) + "</option>";
      }).join("");
    };
    (state.accounts.length ? Promise.resolve(state.accounts) : loadAccounts()).then(fillSelect).catch(function (err) {
      toast("error", err.message);
    });

    $("#queue-reload").addEventListener("click", loadQueue);
    $("#slot-add").addEventListener("click", addSlot);
    $("#slot-next").addEventListener("click", function () {
      var id = $("#slot-account").value;
      var path = "/v2/schedule-slots/next" + (id ? "?accountId=" + encodeURIComponent(id) : "");
      api("GET", path).then(function (data) {
        $("#slot-next-out").textContent = data && data.slotTime
          ? "Next free: " + fmtWhen(data.slotTime) + " (" + (data.timezone || "") + ")"
          : "No slot returned";
      }).catch(function (err) {
        $("#slot-next-out").textContent = err.message;
        toast("error", err.message);
      });
    });
    loadQueue();
    loadSlots();
  }

  function loadQueue() {
    var host = $("#queue-list");
    host.innerHTML = "Loading...";
    api("GET", "/v2/schedules").then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">Nothing scheduled.</p>'; return; }
      host.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Account</th><th>Text</th><th>When</th><th>Reschedule</th></tr></thead><tbody>' +
        items.map(function (p) {
          var id = p.postSubmissionId || p.id;
          return "<tr data-id=\"" + esc(id) + "\"><td>" + esc(accountLabel(p.accountId)) + " " + pill(p.platform) +
            "</td><td>" + esc(clip(p.text, 70)) + "</td><td class=\"muted\">" + esc(fmtWhen(p.scheduledTime)) +
            '</td><td><div class="flex-row"><input class="resched" type="datetime-local" value="' + esc(toLocalInput(p.scheduledTime)) + '">' +
            '<button class="btn sm save-time" type="button">Save</button>' +
            '<button class="btn sm danger cancel-post" type="button">Cancel</button></div></td></tr>';
        }).join("") + "</tbody></table></div>";
      $all(".save-time", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var row = btn.closest("tr");
          var value = $(".resched", row).value;
          if (!value) { toast("error", "Pick a time"); return; }
          api("PATCH", "/v2/schedules/" + row.getAttribute("data-id"), { patch: { scheduledTime: new Date(value).toISOString() } })
            .then(function () { toast("success", "Rescheduled"); loadQueue(); })
            .catch(function (err) { toast("error", err.message); });
        });
      });
      $all(".cancel-post", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.closest("tr").getAttribute("data-id");
          api("DELETE", "/v2/schedules/" + id).then(function () {
            toast("success", "Cancelled");
            loadQueue();
          }).catch(function (err) { toast("error", err.message); });
        });
      });
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function loadSlots() {
    var host = $("#slot-list");
    host.innerHTML = "Loading...";
    api("GET", "/v2/schedule-slots").then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">No weekly slots yet.</p>'; return; }
      host.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Account</th><th>Day</th><th>Time</th><th></th></tr></thead><tbody>' +
        items.map(function (s) {
          return "<tr data-id=\"" + esc(s.id) + "\"><td>" + esc(accountLabel(s.accountId)) + "</td><td>" +
            esc(s.dayName) + "</td><td>" + esc(s.time) + ' <span class="muted">' + esc(s.timezone || "") +
            '</span></td><td><button class="btn sm danger del-slot" type="button">Delete</button></td></tr>';
        }).join("") + "</tbody></table></div>";
      $all(".del-slot", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          api("DELETE", "/v2/schedule-slots/" + btn.closest("tr").getAttribute("data-id")).then(function () {
            toast("success", "Slot removed");
            loadSlots();
          }).catch(function (err) { toast("error", err.message); });
        });
      });
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function addSlot() {
    var sel = $("#slot-account");
    var accountId = sel.value;
    if (!accountId) { toast("error", "Pick an account"); return; }
    var opt = sel.options[sel.selectedIndex];
    var body = {
      accountId: accountId,
      platform: opt ? opt.getAttribute("data-platform") : "instagram",
      dayOfWeek: Number($("#slot-day").value),
      hour: Number($("#slot-hour").value),
      minute: Number($("#slot-minute").value),
      timezone: "Asia/Kolkata"
    };
    api("POST", "/v2/schedule-slots", body).then(function () {
      toast("success", "Slot added");
      loadSlots();
    }).catch(function (err) { toast("error", err.message); });
  }

  /* ----------------------------------------------------------------- inbox */

  function renderInbox(view) {
    view.innerHTML =
      '<div class="flex-row">' +
        '<button class="btn' + (state.inboxTab === "comments" ? " primary" : "") + '" id="tab-comments" type="button">Comments</button>' +
        '<button class="btn' + (state.inboxTab === "messages" ? " primary" : "") + '" id="tab-messages" type="button">Messages</button>' +
        '<label class="flex-row" style="margin-left:auto"><input id="unread-only" type="checkbox"> <span class="muted">Unreplied only</span></label>' +
      "</div>" +
      '<div id="inbox-body">Loading...</div>';
    $("#tab-comments").addEventListener("click", function () { state.inboxTab = "comments"; renderInbox(view); });
    $("#tab-messages").addEventListener("click", function () { state.inboxTab = "messages"; renderInbox(view); });
    $("#unread-only").addEventListener("change", function () { if (state.inboxTab === "comments") loadComments(); });
    if (state.inboxTab === "comments") loadComments();
    else loadThreads();
  }

  function loadComments() {
    var host = $("#inbox-body");
    host.innerHTML = "Loading...";
    var unread = $("#unread-only") && $("#unread-only").checked;
    api("GET", "/v2/comments?unreadOnly=" + (unread ? "true" : "false")).then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">No comments.</p>'; return; }
      host.innerHTML = '<div class="flex-col">' + items.map(function (c) {
        var replied = c.isReplied
          ? '<div class="muted" style="font-size:12px">Replied: ' + esc(c.replyText || "") + "</div>"
          : '<div class="flex-row"><input class="reply-text" type="text" placeholder="Write a reply..."><button class="btn sm primary send-reply" type="button">Reply</button></div>';
        return '<article class="card" data-id="' + esc(c.id) + '"><div class="flex-between"><strong>' +
          esc(c.author || "someone") + "</strong>" + pill(c.platform) + "</div>" +
          "<p style=\"margin:8px 0\">" + esc(c.text) + '</p><div class="muted" style="font-size:12px;margin-bottom:8px">' +
          esc(accountLabel(c.accountId)) + " · " + esc(fmtWhen(c.createdAt)) + "</div>" + replied + "</article>";
      }).join("") + "</div>";
      $all(".send-reply", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var card = btn.closest("article");
          var text = $(".reply-text", card).value.trim();
          if (!text) { toast("error", "Reply text is empty"); return; }
          btn.disabled = true;
          api("POST", "/v2/comments/" + card.getAttribute("data-id") + "/reply", { text: text }).then(function () {
            toast("success", "Reply sent");
            loadComments();
          }).catch(function (err) {
            btn.disabled = false;
            toast("error", err.message);
          });
        });
      });
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function loadThreads() {
    var host = $("#inbox-body");
    host.innerHTML = "Loading...";
    api("GET", "/v2/messages").then(function (data) {
      var items = (data && data.items) || [];
      host.innerHTML =
        '<div class="inbox-split"><div class="threads-list" id="thread-list"></div>' +
        '<div class="conversation-pane"><div class="conversation-messages" id="thread-msgs"><p class="empty">Select a thread.</p></div>' +
        '<form class="conversation-input" id="thread-form"><input id="thread-text" type="text" placeholder="Message..." autocomplete="off">' +
        '<button class="btn primary" type="submit">Send</button></form></div></div>';
      var list = $("#thread-list");
      if (!items.length) {
        list.innerHTML = '<p class="empty">No threads.</p>';
      } else {
        list.innerHTML = items.map(function (t) {
          var badgeHtml = t.unread ? '<span class="badge badge-scheduled">' + esc(t.unread) + "</span>" : "";
          return '<div class="thread-item" data-id="' + esc(t.threadId) + '" data-account="' + esc(t.accountId || "") + '">' +
            '<div class="flex-between"><strong>' + esc(t.participant || "thread") + "</strong>" + badgeHtml + "</div>" +
            '<div class="muted" style="font-size:12px">' + pill(t.platform) + " " + esc(clip(t.lastMessage, 60)) + "</div>" +
            '<div class="muted" style="font-size:11px">' + esc(fmtWhen(t.lastAt)) + "</div></div>";
        }).join("");
        $all(".thread-item", list).forEach(function (el) {
          el.addEventListener("click", function () { openThread(el.getAttribute("data-id"), el.getAttribute("data-account")); });
        });
        if (state.threadId) openThread(state.threadId);
      }
      $("#thread-form").addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (!state.threadId) { toast("error", "Open a thread first"); return; }
        var input = $("#thread-text");
        var text = input.value.trim();
        if (!text) return;
        var active = $(".thread-item.active");
        var accountId = active ? active.getAttribute("data-account") : "";
        var body = { threadId: state.threadId, text: text };
        if (accountId) body.accountId = accountId;
        api("POST", "/v2/messages", body).then(function () {
          input.value = "";
          openThread(state.threadId);
        }).catch(function (err) { toast("error", err.message); });
      });
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function openThread(threadId) {
    state.threadId = threadId;
    $all(".thread-item").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-id") === threadId);
    });
    var host = $("#thread-msgs");
    if (!host) return;
    host.innerHTML = "Loading...";
    api("GET", "/v2/messages/" + encodeURIComponent(threadId)).then(function (data) {
      var msgs = (data && data.messages) || [];
      if (!msgs.length) { host.innerHTML = '<p class="empty">Empty thread.</p>'; return; }
      host.innerHTML = msgs.map(function (m) {
        var dir = m.direction === "outbound" ? "outbound" : "inbound";
        return '<div class="bubble ' + dir + '">' + esc(m.text) +
          '<div class="bubble-meta">' + esc(m.author || "") + " · " + esc(fmtWhen(m.createdAt)) + "</div></div>";
      }).join("");
      host.scrollTop = host.scrollHeight;
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  /* ----------------------------------------------------------- automations */

  function renderAutomations(view) {
    view.innerHTML =
      '<div class="grid-2">' +
        '<section class="card flex-col"><div class="card-title">New automation</div>' +
          '<label class="field"><span>Name</span><input id="auto-name" type="text" placeholder="Link in bio"></label>' +
          '<label class="field"><span>Account</span><select id="auto-account"></select></label>' +
          '<label class="field"><span>DM message</span><textarea id="auto-dm" rows="3" placeholder="Here is the link..."></textarea></label>' +
          '<label class="field"><span>Comment reply variants</span>' +
            '<input id="auto-v1" type="text" placeholder="Variant 1">' +
            '<input id="auto-v2" type="text" placeholder="Variant 2" style="margin-top:6px">' +
            '<input id="auto-v3" type="text" placeholder="Variant 3" style="margin-top:6px">' +
          "</label>" +
          '<label class="field"><span>Keywords (comma separated)</span><input id="auto-keys" type="text" placeholder="link, guide, send"></label>' +
          '<label class="field"><span>Webhook URL (optional)</span><input id="auto-hook" type="text" placeholder="https://..."></label>' +
          '<button id="auto-create" class="btn primary" type="button">Create</button>' +
        "</section>" +
        '<section class="card"><div class="flex-between"><div class="card-title">Automations</div><button id="auto-reload" class="btn sm" type="button">Reload</button></div>' +
          '<div id="auto-list" style="margin-top:12px">Loading...</div>' +
          '<div class="card-title" style="margin-top:18px">Recent events</div><div id="auto-events" style="margin-top:8px">Loading...</div>' +
        "</section>" +
      "</div>";

    var fill = function (accounts) {
      var sel = $("#auto-account");
      sel.innerHTML = accounts.length
        ? accounts.map(function (a) {
            return '<option value="' + esc(a.id) + '" data-platform="' + esc(a.platform) + '">' +
              esc((a.name || a.platform) + " · " + a.platform) + "</option>";
          }).join("")
        : '<option value="">No accounts</option>';
    };
    (state.accounts.length ? Promise.resolve(state.accounts) : loadAccounts()).then(fill).catch(function (err) { toast("error", err.message); });
    $("#auto-create").addEventListener("click", createAutomation);
    $("#auto-reload").addEventListener("click", function () { loadAutomations(); loadEvents(); });
    loadAutomations();
    loadEvents();
  }

  function createAutomation() {
    var sel = $("#auto-account");
    var accountId = sel.value;
    var dm = $("#auto-dm").value.trim();
    if (!accountId || !dm) { toast("error", "Account and DM message are required"); return; }
    var keywords = $("#auto-keys").value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    var variants = ["#auto-v1", "#auto-v2", "#auto-v3"].map(function (id) {
      return { text: $(id).value.trim() };
    }).filter(function (v) { return v.text; });
    var hook = $("#auto-hook").value.trim();
    var opt = sel.options[sel.selectedIndex];
    var body = {
      accountId: accountId,
      platform: opt ? opt.getAttribute("data-platform") : "instagram",
      name: $("#auto-name").value.trim() || "Keyword DM",
      dmMessage: dm,
      triggers: [{ type: "comment-received", keywords: keywords.length ? keywords : ["link"], isActive: true }],
      commentReply: { variants: variants.length ? variants : [{ text: "Sent you a DM" }, { text: "Check your DMs" }, { text: "Just sent it" }] },
      buttons: [],
      followGate: {},
      emailGate: {},
      webhook: hook ? { url: hook, method: "POST" } : {}
    };
    api("POST", "/v2/dm-automations", body).then(function () {
      toast("success", "Automation created");
      ["#auto-name", "#auto-dm", "#auto-v1", "#auto-v2", "#auto-v3", "#auto-keys", "#auto-hook"].forEach(function (id) { $(id).value = ""; });
      loadAutomations();
    }).catch(function (err) { toast("error", err.message); });
  }

  function loadAutomations() {
    var host = $("#auto-list");
    host.innerHTML = "Loading...";
    api("GET", "/v2/dm-automations").then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">No automations yet.</p>'; return; }
      host.innerHTML = '<div class="flex-col">' + items.map(function (a) {
        var keys = (a.triggers || []).map(function (t) { return (t.keywords || []).join(", "); }).filter(Boolean).join(" · ");
        var stats = a.stats || {};
        return '<article class="card" data-id="' + esc(a.id) + '" style="padding:14px"><div class="flex-between"><strong>' +
          esc(a.name) + "</strong>" + pill(a.platform, a.isActive) + "</div>" +
          '<div class="muted" style="font-size:12px;margin:6px 0">Keywords: ' + esc(keys || "--") + "</div>" +
          '<div class="muted" style="font-size:12px">Triggered ' + fmtNum(stats.triggered) + " · completed " +
          fmtNum(stats.completed) + " · failed " + fmtNum(stats.failed) + "</div>" +
          '<div class="flex-row" style="margin-top:10px">' +
            '<button class="btn sm toggle-auto" type="button">' + (a.isActive ? "Pause" : "Activate") + "</button>" +
            '<button class="btn sm test-auto" type="button">Test</button>' +
            '<button class="btn sm danger del-auto" type="button">Delete</button>' +
          "</div><div class=\"test-out muted\" style=\"font-size:12px;margin-top:8px\"></div></article>";
      }).join("") + "</div>";
      $all(".toggle-auto", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var card = btn.closest("article");
          var turningOn = btn.textContent === "Activate";
          api("PATCH", "/v2/dm-automations/" + card.getAttribute("data-id"), { isActive: turningOn }).then(function () {
            toast("success", turningOn ? "Activated" : "Paused");
            loadAutomations();
          }).catch(function (err) { toast("error", err.message); });
        });
      });
      $all(".del-auto", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.closest("article").getAttribute("data-id");
          api("DELETE", "/v2/dm-automations/" + id).then(function () {
            toast("success", "Deleted");
            loadAutomations();
          }).catch(function (err) { toast("error", err.message); });
        });
      });
      $all(".test-auto", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var card = btn.closest("article");
          var text = window.prompt("Comment text to test against this automation", "send me the link");
          if (text == null) return;
          var out = $(".test-out", card);
          out.textContent = "Testing...";
          api("POST", "/v2/dm-automations/" + card.getAttribute("data-id") + "/test", {
            text: text, author: "console", type: "comment-received"
          }).then(function (data) {
            var matched = (data && data.matched) || [];
            if (!matched.length) {
              out.textContent = "No match. Skipped " + ((data && data.skipped) || 0) + ".";
              return;
            }
            out.textContent = matched.map(function (m) {
              var actions = (m.actions || []).map(function (act) {
                return typeof act === "string" ? act : (act.type || act.action || JSON.stringify(act));
              }).join(", ");
              return (m.name || "match") + (m.keyword ? " (" + m.keyword + ")" : "") + (actions ? ": " + actions : "");
            }).join(" | ");
          }).catch(function (err) {
            out.textContent = err.message;
            toast("error", err.message);
          });
        });
      });
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function loadEvents() {
    var host = $("#auto-events");
    api("GET", "/v2/webhook-events").then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">No events yet.</p>'; return; }
      host.innerHTML = '<div class="table-wrap"><table><thead><tr><th>When</th><th>Type</th><th>Status</th><th>Detail</th></tr></thead><tbody>' +
        items.slice(0, 12).map(function (e) {
          return "<tr><td class=\"muted\">" + esc(fmtWhen(e.createdAt)) + "</td><td>" + esc(e.eventType) +
            "</td><td>" + badge(e.status) + "</td><td>" + esc(clip(e.detail, 80)) + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
    });
  }

  /* --------------------------------------------------------------- visuals */

  function renderVisuals(view) {
    view.innerHTML =
      '<div class="grid-2">' +
        '<section class="card"><div class="card-title">Templates</div><div id="tpl-grid" class="template-grid" style="margin-top:12px">Loading...</div></section>' +
        '<section class="card flex-col"><div class="card-title" id="tpl-title">Pick a template</div>' +
          '<p id="tpl-desc" class="muted"></p><form id="tpl-form" class="flex-col"></form>' +
          '<button id="tpl-go" class="btn primary" type="button" disabled>Generate</button></section>' +
      "</div>" +
      '<section class="card"><div class="card-title">Latest output</div><div id="tpl-output" class="outputs-grid"></div></section>' +
      '<section class="card"><div class="flex-between"><div class="card-title">History</div><button id="vis-reload" class="btn sm" type="button">Reload</button></div><div id="vis-history" style="margin-top:12px">Loading...</div></section>';

    state.template = null;
    api("GET", "/v2/videos/templates").then(function (data) {
      var items = (data && data.items) || [];
      var grid = $("#tpl-grid");
      if (!items.length) { grid.innerHTML = '<p class="empty">No templates.</p>'; return; }
      grid.innerHTML = items.map(function (t) {
        return '<article class="template-card" data-id="' + esc(t.id) + '"><strong>' + esc(t.title) +
          '</strong><p class="muted" style="font-size:12px">' + esc(t.description || "") + "</p></article>";
      }).join("");
      $all(".template-card", grid).forEach(function (card) {
        card.addEventListener("click", function () {
          $all(".template-card", grid).forEach(function (c) { c.classList.remove("active"); });
          card.classList.add("active");
          var found = null;
          for (var i = 0; i < items.length; i++) if (items[i].id === card.getAttribute("data-id")) found = items[i];
          mountTemplate(found);
        });
      });
    }).catch(function (err) {
      $("#tpl-grid").innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
    $("#tpl-go").addEventListener("click", generateVisual);
    $("#vis-reload").addEventListener("click", loadCreations);
    loadCreations();
  }

  function mountTemplate(template) {
    state.template = template;
    $("#tpl-title").textContent = template.title;
    $("#tpl-desc").textContent = template.description || "";
    $("#tpl-go").disabled = false;
    var form = $("#tpl-form");
    form.innerHTML = (template.inputs || []).map(function (input, index) {
      return fieldFor(input, index);
    }).join("");
    $all("[data-add-row]", form).forEach(function (btn) {
      btn.addEventListener("click", function () {
        var box = btn.parentElement.querySelector(".rows");
        box.insertAdjacentHTML("beforeend", arrayRow(btn.getAttribute("data-add-row"), ""));
      });
    });
  }

  function fieldFor(input, index) {
    var name = esc(input.name);
    var req = input.required ? " *" : "";
    if (input.type === "enum") {
      var opts = (input.options || []).map(function (o) {
        var sel = String(o) === String(input["default"] == null ? "" : input["default"]) ? " selected" : "";
        return '<option value="' + esc(o) + '"' + sel + ">" + esc(o) + "</option>";
      }).join("");
      return '<label class="field"><span>' + name + req + '</span><select data-input="' + name + '" data-type="enum">' + opts + "</select></label>";
    }
    if (input.type === "array") {
      return '<div class="field"><span>' + name + req + '</span><div class="rows" data-array="' + name + '">' +
        arrayRow(input.name, "") + '</div><button class="btn sm" type="button" data-add-row="' + name + '">Add row</button></div>';
    }
    var long = /text|body|points|slides|steps/.test(input.name);
    var control = long
      ? '<textarea data-input="' + name + '" data-type="string" rows="3" placeholder="' + esc(input.description || "") + '"></textarea>'
      : '<input data-input="' + name + '" data-type="string" type="text" value="' + esc(input["default"] || "") + '">';
    return '<label class="field"><span>' + name + req + "</span>" + control + "</label>";
  }

  function arrayRow(name, value) {
    return '<div class="flex-row" style="margin-bottom:6px"><input type="text" data-row="' + esc(name) + '" value="' + esc(value) + '" placeholder="title | body">' +
      '<button class="btn sm ghost" type="button" onclick="this.parentElement.remove()">x</button></div>';
  }

  function readInputs() {
    var inputs = {};
    var template = state.template;
    (template.inputs || []).forEach(function (spec) {
      if (spec.type === "array") {
        inputs[spec.name] = $all('[data-row="' + spec.name + '"]').map(function (el) {
          var raw = el.value.trim();
          if (!raw) return null;
          var parts = raw.split("|");
          if (parts.length > 1) return { title: parts[0].trim(), body: parts.slice(1).join("|").trim() };
          return spec.name === "points" ? raw : { title: raw, body: "" };
        }).filter(Boolean);
      } else {
        var el = $('[data-input="' + spec.name + '"]');
        inputs[spec.name] = el ? el.value : "";
      }
    });
    return inputs;
  }

  function generateVisual() {
    if (!state.template) return;
    var btn = $("#tpl-go");
    btn.disabled = true;
    btn.textContent = "Generating...";
    api("POST", "/v2/videos/from-templates", { templateId: state.template.id, inputs: readInputs() }).then(function (data) {
      paintOutputs(data);
      if (data.status === "failed") toast("error", data.error || "Render failed");
      else toast("success", "Rendered " + ((data.outputs || []).length) + " image" + ((data.outputs || []).length === 1 ? "" : "s"));
      loadCreations();
    }).catch(function (err) { toast("error", err.message); })
      .then(function () { btn.disabled = false; btn.textContent = "Generate"; });
  }

  function paintOutputs(item) {
    var host = $("#tpl-output");
    var urls = (item && item.outputs) || [];
    if (!urls.length) {
      host.innerHTML = '<p class="inline-error">' + esc((item && item.error) || "No images returned") + "</p>";
      return;
    }
    host.innerHTML = urls.map(function (url) {
      return '<div class="output-item"><img src="' + esc(url) + '" alt="' + esc(item.templateTitle || "visual") + '">' +
        '<a class="muted" style="font-size:12px" href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + "</a></div>";
    }).join("");
  }

  function loadCreations() {
    var host = $("#vis-history");
    host.innerHTML = "Loading...";
    api("GET", "/v2/videos/creations").then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">No visuals yet.</p>'; return; }
      host.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Template</th><th>Status</th><th>When</th><th></th></tr></thead><tbody>' +
        items.map(function (v) {
          return '<tr data-id="' + esc(v.id) + '"><td>' + esc(v.templateTitle || v.templateId) + "</td><td>" + badge(v.status) +
            '</td><td class="muted">' + esc(fmtWhen(v.createdAt)) + '</td><td><div class="flex-row">' +
            '<button class="btn sm show-vis" type="button">Show</button>' +
            '<button class="btn sm danger del-vis" type="button">Delete</button></div></td></tr>';
        }).join("") + "</tbody></table></div>";
      $all(".show-vis", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.closest("tr").getAttribute("data-id");
          var found = null;
          for (var i = 0; i < items.length; i++) if (String(items[i].id) === id) found = items[i];
          if (found) paintOutputs(found);
        });
      });
      $all(".del-vis", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.closest("tr").getAttribute("data-id");
          api("DELETE", "/v2/videos/" + id).then(function () {
            toast("success", "Deleted");
            loadCreations();
          }).catch(function (err) { toast("error", err.message); });
        });
      });
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  /* --------------------------------------------------------------- sources */

  var SOURCE_TYPES = ["article", "youtube", "x", "tiktok", "pdf", "audio", "text"];

  function renderSources(view) {
    view.innerHTML =
      '<section class="card flex-col"><div class="card-title">Extract a source</div>' +
        '<div class="grid-2">' +
          '<label class="field"><span>URL</span><input id="src-url" type="text" placeholder="https://..."></label>' +
          '<label class="field"><span>Source type</span><select id="src-type">' +
            SOURCE_TYPES.map(function (t) { return '<option value="' + t + '">' + t + "</option>"; }).join("") +
          "</select></label>" +
        "</div>" +
        '<label class="field" id="src-text-field" hidden><span>Text</span><textarea id="src-text" rows="5" placeholder="Paste source text..."></textarea></label>' +
        '<button id="src-go" class="btn primary" type="button">Extract</button>' +
      "</section>" +
      '<section class="card" id="src-result" hidden><div class="flex-between"><div class="card-title" id="src-title"></div>' +
        '<button id="src-repurpose" class="btn" type="button">Repurpose</button></div>' +
        '<pre id="src-body" style="white-space:pre-wrap;margin-top:12px;color:var(--muted);font-family:var(--font-sans)"></pre>' +
        '<div id="src-drafts" class="flex-col" style="margin-top:14px"></div></section>' +
      '<section class="card"><div class="card-title">Previous sources</div><div id="src-list" style="margin-top:12px">Loading...</div></section>';

    $("#src-type").addEventListener("change", function () {
      $("#src-text-field").hidden = this.value !== "text";
    });
    $("#src-go").addEventListener("click", extractSource);
    $("#src-repurpose").addEventListener("click", repurposeSource);
    loadSources();
  }

  function extractSource() {
    var type = $("#src-type").value;
    var body = { sourceType: type };
    if (type === "text") {
      body.text = $("#src-text").value;
      if (!body.text.trim()) { toast("error", "Paste some text"); return; }
    } else {
      body.url = $("#src-url").value.trim();
      if (!body.url) { toast("error", "Enter a URL"); return; }
    }
    var btn = $("#src-go");
    btn.disabled = true;
    btn.textContent = "Extracting...";
    api("POST", "/v2/source-resolutions", body).then(function (data) {
      state.source = data;
      showExtraction(data);
      if (data.status === "failed") toast("error", data.error || "Extraction failed");
      else toast("success", "Extracted");
      loadSources();
    }).catch(function (err) { toast("error", err.message); })
      .then(function () { btn.disabled = false; btn.textContent = "Extract"; });
  }

  function showExtraction(data) {
    var box = $("#src-result");
    box.hidden = false;
    var extracted = data.extracted || {};
    $("#src-title").textContent = extracted.title || "(no title)";
    $("#src-body").textContent = extracted.body || data.error || "";
    $("#src-drafts").innerHTML = "";
  }

  function repurposeSource() {
    if (!state.source || !state.source.id) { toast("error", "Extract a source first"); return; }
    var platforms = ["instagram", "twitter", "linkedin", "threads", "facebook"];
    api("POST", "/v2/source-resolutions/" + state.source.id + "/repurpose", { platforms: platforms }).then(function (data) {
      var drafts = (data && data.drafts) || [];
      var host = $("#src-drafts");
      if (!drafts.length) { host.innerHTML = '<p class="empty">No drafts returned.</p>'; return; }
      host.innerHTML = drafts.map(function (d, i) {
        return '<label class="field"><span>' + esc(d.platform) + '</span><textarea data-draft="' + i + '" rows="4">' +
          esc(d.text) + '</textarea><button class="btn sm" type="button" data-send="' + i + '" data-platform="' +
          esc(d.platform) + '">Send to Compose</button></label>';
      }).join("");
      $all("[data-send]", host).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var area = $('[data-draft="' + btn.getAttribute("data-send") + '"]');
          sessionStorage.setItem("blotato_compose", JSON.stringify({ text: area.value, platform: btn.getAttribute("data-platform") }));
          toast("success", "Copied into Compose");
          setRoute("compose");
        });
      });
    }).catch(function (err) { toast("error", err.message); });
  }

  function loadSources() {
    var host = $("#src-list");
    api("GET", "/v2/source-resolutions").then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">No sources yet.</p>'; return; }
      host.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Title</th><th>Type</th><th>Status</th><th>When</th></tr></thead><tbody>' +
        items.map(function (s) {
          return "<tr><td>" + esc(clip(s.title || s.url || s.id, 70)) + "</td><td>" + esc(s.sourceType) +
            "</td><td>" + badge(s.status) + '</td><td class="muted">' + esc(fmtWhen(s.createdAt)) + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
    });
  }

  /* ------------------------------------------------------------- analytics */

  function renderAnalytics(view) {
    view.innerHTML =
      '<div class="grid-4" id="an-stats">' + skeletonTiles(4) + "</div>" +
      '<section class="card"><div class="flex-between"><div class="card-title">Top posts</div>' +
        '<label class="flex-row"><span class="muted">Metric</span><select id="an-metric">' +
          ["views", "likes", "comments", "shares"].map(function (m) {
            return '<option value="' + m + '"' + (m === state.metric ? " selected" : "") + ">" + m + "</option>";
          }).join("") +
        "</select></label></div>" +
        '<div id="an-bars" style="margin-top:12px">Loading...</div></section>' +
      '<section class="card" id="an-detail" hidden><div class="card-title">Post metrics</div><div id="an-detail-body" style="margin-top:12px"></div></section>';

    $("#an-metric").addEventListener("change", function () {
      state.metric = this.value;
      loadTop();
    });
    api("GET", "/v2/analytics").then(function (data) {
      var ov = (data && data.overview) || {};
      $("#an-stats").innerHTML =
        stat("Published", fmtNum(ov.totalPublished), "posts") +
        stat("Impressions", fmtNum(ov.totalImpressions), "views counted as impressions") +
        stat("Likes", fmtNum(ov.totalLikes), "all snapshots") +
        stat("Engagement", (parseFloat(ov.averageEngagementRate) || 0).toFixed(2) + "%", "comments " + fmtNum(ov.totalComments));
    }).catch(function (err) {
      $("#an-stats").innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
    loadTop();
  }

  function loadTop() {
    var host = $("#an-bars");
    host.innerHTML = "Loading...";
    api("GET", "/v2/analytics/top?metric=" + encodeURIComponent(state.metric) + "&limit=10").then(function (data) {
      var items = (data && data.items) || [];
      if (!items.length) { host.innerHTML = '<p class="empty">No analytics snapshots yet.</p>'; return; }
      var max = 0;
      items.forEach(function (item) { max = Math.max(max, Number(item[state.metric]) || 0); });
      if (!max) max = 1;
      host.innerHTML = items.map(function (item, i) {
        var value = Number(item[state.metric]) || 0;
        var width = Math.max(2, Math.round(value / max * 100));
        return '<div class="bar-row" data-id="' + esc(item.postSubmissionId) + '"><div class="bar-fill" style="width:' + width + '%"></div>' +
          '<div class="bar-rank">' + (i + 1) + '</div><div class="bar-plat">' + pill(item.platform) + "</div>" +
          '<div class="bar-text">' + esc(clip(item.text, 80)) + '</div><div class="bar-val">' + fmtNum(value) + "</div></div>";
      }).join("");
      $all(".bar-row", host).forEach(function (row) {
        row.addEventListener("click", function () { showPostMetrics(row.getAttribute("data-id")); });
      });
    }).catch(function (err) {
      host.innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  function showPostMetrics(id) {
    var box = $("#an-detail");
    box.hidden = false;
    $("#an-detail-body").innerHTML = "Loading...";
    api("GET", "/v2/analytics?postSubmissionId=" + encodeURIComponent(id)).then(function (data) {
      var m = (data && data.metrics) || {};
      var history = (data && data.history) || [];
      $("#an-detail-body").innerHTML =
        '<div class="grid-4">' +
          stat("Views", fmtNum(m.views), "") + stat("Likes", fmtNum(m.likes), "") +
          stat("Comments", fmtNum(m.comments), "") + stat("Shares", fmtNum(m.shares), "") +
        "</div>" +
        '<p style="margin-top:12px">' + esc(clip(data.text, 180)) + "</p>" +
        (data.url ? '<p style="margin-top:6px"><a href="' + esc(data.url) + '" target="_blank" rel="noopener">' + esc(data.url) + "</a></p>" : "") +
        '<p class="muted" style="margin-top:8px">' + history.length + " snapshot" + (history.length === 1 ? "" : "s") +
        (m.saves != null ? " · saves " + fmtNum(m.saves) + " · reach " + fmtNum(m.reach) : "") + "</p>";
    }).catch(function (err) {
      $("#an-detail-body").innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
      toast("error", err.message);
    });
  }

  /* -------------------------------------------------------------- settings */

  var PLATFORMS = ["instagram", "facebook", "twitter", "linkedin", "youtube", "threads", "bluesky", "pinterest", "tiktok"];

  function renderSettings(view) {
    view.innerHTML =
      '<div class="grid-2">' +
        '<section class="card flex-col"><div class="card-title">API key</div>' +
          '<p class="muted">Stored locally as blotato_key and sent as the blotato-api-key header.</p>' +
          '<label class="field"><span>Key</span><input id="set-key" type="password" autocomplete="off"></label>' +
          '<button id="set-save" class="btn primary" type="button">Save key</button>' +
          '<p class="hint">How to get a key: default master key <code>blt_master_venu_secret_2026</code>.</p>' +
        "</section>" +
        '<section class="card flex-col"><div class="card-title">Engine</div><div id="health">Loading...</div></section>' +
      "</div>" +
      '<section class="card"><div class="card-title">Accounts</div><div id="set-accounts" style="margin-top:12px">Loading...</div></section>' +
      '<section class="card flex-col"><div class="card-title">Add account</div>' +
        '<div class="grid-3">' +
          '<label class="field"><span>Platform</span><select id="acc-platform">' +
            PLATFORMS.map(function (p) { return '<option value="' + p + '">' + p + "</option>"; }).join("") +
          "</select></label>" +
          '<label class="field"><span>Name</span><input id="acc-name" type="text" placeholder="Brand page"></label>' +
          '<label class="field"><span>Username</span><input id="acc-user" type="text" placeholder="@handle"></label>' +
        "</div>" +
        '<label class="field"><span>Credentials JSON (optional)</span><textarea id="acc-creds" rows="3" placeholder="{}"></textarea></label>' +
        '<button id="acc-add" class="btn primary" type="button">Add account</button>' +
      "</section>";

    $("#set-key").value = state.key;
    $("#set-save").addEventListener("click", function () {
      var value = $("#set-key").value.trim();
      if (!value) { toast("error", "Key is empty"); return; }
      state.key = value;
      localStorage.setItem(KEY_STORE, value);
      api("GET", "/v2/credits").then(function (data) {
        state.credits = data;
        paintPlan();
        toast("success", "Key saved for " + (data.clientName || data.plan));
      }).catch(function (err) { toast("error", err.message); });
    });
    $("#acc-add").addEventListener("click", function () {
      var credentials = {};
      var raw = $("#acc-creds").value.trim();
      if (raw) {
        try { credentials = JSON.parse(raw); }
        catch (e) { toast("error", "Credentials must be JSON"); return; }
      }
      api("POST", "/v2/users/me/accounts", {
        platform: $("#acc-platform").value,
        name: $("#acc-name").value.trim(),
        username: $("#acc-user").value.trim(),
        credentials: credentials
      }).then(function () {
        toast("success", "Account added");
        $("#acc-name").value = "";
        $("#acc-user").value = "";
        $("#acc-creds").value = "";
        return loadAccounts();
      }).then(paintSettingsAccounts).catch(function (err) { toast("error", err.message); });
    });

    api("GET", "/health").then(function (data) {
      $("#health").innerHTML =
        '<div class="stat-value">' + esc(data.version || "--") + "</div>" +
        '<div class="stat-meta">' + esc(data.service || "") + " · " + esc(data.status || "") +
        "<br>REST " + esc(data.restEndpoint || "/v2") + " · MCP " + esc(data.mcpEndpoint || "/mcp") + "</div>";
    }).catch(function (err) {
      $("#health").innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
    });

    (state.accounts.length ? Promise.resolve(state.accounts) : loadAccounts())
      .then(paintSettingsAccounts)
      .catch(function (err) {
        $("#set-accounts").innerHTML = '<p class="inline-error">' + esc(err.message) + "</p>";
        toast("error", err.message);
      });
  }

  function paintSettingsAccounts(accounts) {
    var host = $("#set-accounts");
    if (!host) return;
    var list = accounts || state.accounts;
    if (!list.length) { host.innerHTML = '<p class="empty">No accounts connected.</p>'; return; }
    host.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Platform</th><th>Name</th><th>Username</th><th>Status</th></tr></thead><tbody>' +
      list.map(function (a) {
        return "<tr><td>" + pill(a.platform, a.isActive) + "</td><td>" + esc(a.name || "") +
          "</td><td>" + esc(a.username || "") + "</td><td>" + (a.isActive ? badge("published") : badge("draft")) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  /* ------------------------------------------------------------------ start */

  function fetchPost(id) {
    return api("GET", "/v2/posts/" + encodeURIComponent(id));
  }
  // Exposed so a deep link can resolve one submission without a second client.
  window.Blotato = { api: api, getPost: fetchPost };

  boot();
})();
