// UI regression for Lead Engine. Raw CDP over WebSocket, Node 22+, no npm packages.
//
//   CDP_URL=http://127.0.0.1:<DevToolsActivePort> node tests/ui_regression.mjs
//
// Asserts the API contract and the documented user flows, not the current (possibly
// broken) rendering. A missing control or a wrong colour is a failure, reported with
// the observed DOM text so the caller can finish the fix.
const BASE = process.env.BASE_URL || "http://localhost:8500";
const CDP = process.env.CDP_URL;
const EMAIL = "admin@leadengine.local";
const PASSWORD = "leadengine123";
const OUTCOMES = ["found", "not_found", "needs_company", "invalid_url"];

if (!CDP) {
  console.error("CDP_URL is required (http://127.0.0.1:<port> from DevToolsActivePort)");
  process.exit(2);
}

const checks = [];
const http4xx = [];
const http5xx = [];
const jsErrors = [];

function check(name, ok, detail = "") {
  checks.push({ name, ok: !!ok, detail: String(detail || "") });
  const line = `${ok ? "PASS" : "FAIL"}  ${name}${detail ? " :: " + detail : ""}`;
  console.log(line.slice(0, 900));
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  const list = await (await fetch(CDP + "/json/list")).json();
  let page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
  if (!page) {
    page = await (await fetch(CDP + "/json/new?about:blank", { method: "PUT" })).json();
  }
  if (!page || !page.webSocketDebuggerUrl) throw new Error("no debuggable page on " + CDP);

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => {
    ws.addEventListener("open", r);
    ws.addEventListener("error", () => j(new Error("CDP websocket failed")));
  });

  let seq = 0;
  const pending = new Map();
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails || {};
      jsErrors.push(d.exception?.description || d.text || "exception");
    }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      const text = (m.params.args || []).map((a) => a.value ?? a.description ?? "").join(" ");
      // resource load failures are already counted from Network; keep real exceptions here
      if (!/failed to load resource|net::ERR/i.test(text)) jsErrors.push("console: " + text);
    }
    if (m.method === "Network.responseReceived") {
      const res = m.params.response || {};
      const url = String(res.url || "").replace(BASE, "");
      if (res.status >= 500) http5xx.push(res.status + " " + url);
      else if (res.status >= 400) http4xx.push(res.status + " " + url);
    }
  });

  const send = (method, params = {}, ms = 20000) =>
    new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => {
        pending.delete(id);
        rej(new Error("CDP timeout: " + method));
      }, ms);
      pending.set(id, (msg) => {
        clearTimeout(timer);
        res(msg);
      });
      ws.send(JSON.stringify({ id, method, params }));
    });

  const ev = async (expr) => {
    const msg = await send("Runtime.evaluate", {
      expression: expr,
      returnByValue: true,
      awaitPromise: true,
    });
    const result = msg.result?.result;
    if (result?.subtype === "error" || msg.result?.exceptionDetails) {
      const d = msg.result?.exceptionDetails || {};
      throw new Error(result?.description || d.text || "evaluate failed");
    }
    return result?.value;
  };

  // Poll until pred(value) is truthy. Returns the last value either way.
  async function poll(expr, pred, timeoutMs, every = 400) {
    const start = Date.now();
    let last;
    while (Date.now() - start < timeoutMs) {
      last = await ev(expr);
      if (pred(last)) return last;
      await sleep(every);
    }
    return last;
  }

  const displayOf = (sel) =>
    ev(
      `(() => { const el = document.querySelector(${JSON.stringify(sel)}); return el ? getComputedStyle(el).display : 'missing'; })()`
    );

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Network.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1500,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });

  // ---------- LOGIN ----------
  await send("Page.navigate", { url: BASE + "/" });
  await poll("document.readyState", (s) => s === "complete", 10000);
  await send("Page.reload", { ignoreCache: true });
  await poll("document.readyState", (s) => s === "complete", 10000);
  await ev("(() => { try { localStorage.clear(); sessionStorage.clear(); location.hash = ''; } catch(e){} })()");
  await send("Page.navigate", { url: BASE + "/" });
  await poll(
    `(() => document.getElementById('login') ? getComputedStyle(document.getElementById('login')).display : '')()`,
    (d) => d && d !== "none",
    10000
  );

  const loginBefore = await displayOf("#login");
  const appBefore = await displayOf("#app");
  check(
    "LOGIN before submit: #login visible, #app hidden",
    loginBefore !== "none" && loginBefore !== "missing" && appBefore === "none",
    `#login=${loginBefore} #app=${appBefore}`
  );
  check(
    "LOGIN before submit: never both visible",
    !(loginBefore !== "none" && appBefore !== "none"),
    `#login=${loginBefore} #app=${appBefore}`
  );

  // Type the password; the field is not prefilled. Set it through the input event
  // path so frameworks that read the event (not just .value) see it.
  await ev(`(() => {
    const el = document.getElementById('login-password');
    el.focus();
    const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    proto.set.call(el, ${JSON.stringify(PASSWORD)});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value.length;
  })()`);
  const typed = await ev("document.getElementById('login-password').value");
  if (typed !== PASSWORD) {
    await send("Runtime.evaluate", {
      expression: "document.getElementById('login-password').focus()",
    });
    await send("Input.insertText", { text: PASSWORD });
  }
  const email = await ev("document.getElementById('login-email').value");
  if (email !== EMAIL) {
    await ev(`(() => {
      const el = document.getElementById('login-email');
      const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
      proto.set.call(el, ${JSON.stringify(EMAIL)});
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return el.value;
    })()`);
  }

  await ev("document.getElementById('login-submit').click()");
  const after = await poll(
    `(() => JSON.stringify({
      login: getComputedStyle(document.getElementById('login')).display,
      app: getComputedStyle(document.getElementById('app')).display,
      token: !!localStorage.getItem('lead_token'),
      error: (document.getElementById('login-error') || {}).textContent || ''
    }))()`,
    (raw) => {
      try {
        const v = JSON.parse(raw);
        return v.app !== "none" && v.login === "none";
      } catch (e) {
        return false;
      }
    },
    15000
  );
  let afterState = {};
  try {
    afterState = JSON.parse(after || "{}");
  } catch (e) {
    afterState = {};
  }
  check(
    "LOGIN after submit: #app visible, #login hidden",
    afterState.app !== "none" && afterState.login === "none",
    `#login=${afterState.login} #app=${afterState.app} token=${afterState.token} error=${JSON.stringify(afterState.error || "")}`
  );
  check(
    "LOGIN after submit: never both visible",
    !(afterState.login !== "none" && afterState.app !== "none"),
    `#login=${afterState.login} #app=${afterState.app}`
  );

  async function goHash(hash) {
    await ev(`(() => {
      location.hash = ${JSON.stringify(hash)};
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    })()`);
    await sleep(1000);
  }

  // ---------- PROSPECTOR ----------
  await goHash('#/prospector');
  for (let i = 0; i < 40; i++) {
    const ok = await ev("!!document.getElementById('search-go')");
    if (ok) break;
    await sleep(250);
  }
  // Clear any existing filters in the form so we get the full default page
  await ev(`(() => {
    ['f-title','f-company','f-industry','f-location','f-headcount','f-seniority','f-prompt'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const cb = document.getElementById('f-direct');
    if (cb) cb.checked = false;
  })()`);
  await ev("(document.getElementById('search-go') || {click(){}}).click()");
  for (let i = 0; i < 20; i++) {
    const c = await ev("document.querySelectorAll('#search-results tbody tr').length");
    if (Number(c) >= 10) break;
    await sleep(500);
  }
  const rowsCount = Number(await ev("document.querySelectorAll('#search-results tbody tr').length")) || 0;
  const prospectText = String(
    (await ev("(document.getElementById('search-results') || document.getElementById('view') || {}).innerText || ''"))
  ) || "";
  check("PROSPECTOR table has >= 10 rows", rowsCount >= 10, `rows=${rowsCount}`);

  const countText = String(
    (await ev(`(() => {
      const el = document.querySelector('.pagination, #result-count, [data-result-count], .result-count');
      return el ? el.innerText : '';
    })()`)) || ""
  );
  const countFallback = /\d/.test(countText) ? countText : prospectText;
  check(
    "PROSPECTOR pagination or result count mentions a number",
    /\d/.test(countFallback) && (/\d/.test(countText) || /people|result|page|of \d/i.test(prospectText)),
    JSON.stringify((countText || prospectText).slice(0, 200))
  );

  const formErrors = String(
    (await ev(`Array.from(document.querySelectorAll('.form-error')).map(e => e.textContent.trim()).filter(Boolean).join(' | ')`)) || ""
  );
  check("PROSPECTOR no .form-error text", formErrors === "", JSON.stringify(formErrors.slice(0, 300)));

  // Click reveal on an unrevealed row. The control is [data-reveal]; if the parent
  // renamed it, fall back to a button whose label is Reveal inside a result row.
  const revealOutcome = await ev(`(async () => {
    const host = document.getElementById('search-results') || document.getElementById('view');
    const btn = host.querySelector('[data-reveal]') ||
      Array.from(host.querySelectorAll('button')).find(b => /^\\s*reveal\\s*$/i.test(b.textContent));
    if (!btn) return JSON.stringify({ ok: false, reason: 'no unrevealed reveal control', text: host.innerText.slice(0, 300) });
    const row = btn.closest('tr') || btn.parentElement;
    const before = row ? row.innerText : '';
    let thrown = '';
    try { btn.click(); } catch (e) { thrown = String(e && e.message || e); }
    const start = Date.now();
    let toast = '';
    let changed = false;
    while (Date.now() - start < 20000) {
      toast = Array.from(document.querySelectorAll('#toasts .toast, .toast')).map(t => t.innerText.trim()).filter(Boolean).join(' | ');
      const now = row && row.isConnected ? row.innerText : '';
      changed = !!now && now !== before;
      if (toast || changed) break;
      await new Promise(r => setTimeout(r, 400));
    }
    return JSON.stringify({ ok: !thrown && (toast.length > 0 || changed), thrown, toast, changed, before: before.slice(0, 160) });
  })()`);
  let reveal = {};
  try {
    reveal = JSON.parse(revealOutcome || "{}");
  } catch (e) {
    reveal = { ok: false, reason: "bad payload " + revealOutcome };
  }
  check(
    "PROSPECTOR reveal on an unrevealed row produces a toast or an inline change, no throw",
    !!reveal.ok,
    reveal.reason || `toast=${JSON.stringify(reveal.toast || "")} inlineChange=${!!reveal.changed} thrown=${JSON.stringify(reveal.thrown || "")}`
  );
  console.log("  reveal toast: " + JSON.stringify(reveal.toast || "(none)"));

  // ---------- LISTS ----------
  const stamp = Date.now();
  const folderName = "Regression " + stamp;
  await goHash('#/lists');
  await poll("!!document.getElementById('folder-name') && !!document.getElementById('folder-create')", (v) => v === true, 10000);

  await ev(`(() => {
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    set('folder-name', ${JSON.stringify(folderName)});
    set('folder-color', '#ff3366');
    const btn = document.getElementById('folder-create');
    if (btn) btn.click();
  })()`);

  const folderSeen = await poll(
    `(document.getElementById('view') || {}).innerText || ''`,
    (t) => String(t).includes(folderName),
    15000
  );
  check(
    "LISTS new folder appears in the table",
    String(folderSeen).includes(folderName),
    JSON.stringify(String(folderSeen).slice(0, 300))
  );

  // Colour must be visible on the row: a computed background of rgb(255, 51, 102)
  // inside the row, or a style/data attribute carrying ff3366.
  const colour = await ev(`(() => {
    const name = ${JSON.stringify(folderName)};
    const rows = Array.from(document.querySelectorAll('#view tr, #view [data-folder], #view li'));
    const row = rows.find(r => (r.innerText || '').includes(name));
    if (!row) return JSON.stringify({ found: false, reason: 'row not in DOM' });
    const nodes = [row, ...row.querySelectorAll('*')];
    const paints = nodes.map(n => getComputedStyle(n).backgroundColor);
    const hit = paints.find(c => c === 'rgb(255, 51, 102)');
    const attr = nodes.some(n => /ff3366/i.test((n.getAttribute('style') || '') + ' ' + (n.getAttribute('data-color') || '') + ' ' + (n.dataset ? Object.values(n.dataset).join(' ') : '')));
    return JSON.stringify({ found: !!hit || attr, computed: hit || null, attr, sample: paints.filter(c => c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent').slice(0, 6) });
  })()`);
  let colourState = {};
  try {
    colourState = JSON.parse(colour || "{}");
  } catch (e) {
    colourState = { found: false, reason: colour };
  }
  check(
    "LISTS folder row carries #ff3366",
    !!colourState.found,
    colourState.found
      ? `computed=${colourState.computed || "(via attribute)"}`
      : `absent; sample=${JSON.stringify(colourState.sample || [])} ${colourState.reason || ""}`
  );

  // Delete it so the run leaves the DB as it found it. confirm() is not a real
  // dialog under this CDP session only if we stub it first.
  await ev("window.confirm = () => true");
  const deleted = await ev(`(() => {
    const name = ${JSON.stringify(folderName)};
    const rows = Array.from(document.querySelectorAll('#view tr'));
    const row = rows.find(r => (r.innerText || '').includes(name));
    if (!row) return 'row gone already';
    const btn = row.querySelector('[data-del], .btn.danger, button');
    const del = Array.from(row.querySelectorAll('button')).find(b => /delete/i.test(b.textContent)) || btn;
    if (!del) return 'no delete control';
    del.click();
    return 'clicked';
  })()`);
  const gone = await poll(
    `(document.getElementById('view') || {}).innerText || ''`,
    (t) => !String(t).includes(folderName),
    10000
  );
  check(
    "LISTS regression folder deleted",
    deleted === "clicked" && !String(gone).includes(folderName),
    `action=${deleted} stillPresent=${String(gone).includes(folderName)}`
  );

  // ---------- ENRICH ----------
  await goHash('#/enrich');
  await poll(
    `!!document.querySelector('#li-urls, textarea')`,
    (v) => v === true,
    10000
  );
  // Make sure the LinkedIn tab is the one showing.
  await ev(`(() => {
    const tab = Array.from(document.querySelectorAll('[data-tab], .tab-btn')).find(b => /linkedin/i.test(b.textContent + ' ' + (b.dataset.tab || '')));
    if (tab && !tab.classList.contains('active')) tab.click();
  })()`);
  await sleep(300);

  const companyProbe = await ev(`(() => {
    const body = document.getElementById('enrich-body') || document.getElementById('view');
    const inputs = Array.from(body.querySelectorAll('input, textarea, select'));
    const hit = inputs.find(el => /company|domain/i.test((el.id || '') + ' ' + (el.name || '') + ' ' + (el.getAttribute('placeholder') || '')));
    return JSON.stringify({
      found: !!hit,
      id: hit ? hit.id : '',
      name: hit ? (hit.name || '') : '',
      ids: inputs.map(el => el.id || el.name || el.placeholder || el.tagName).slice(0, 12)
    });
  })()`);
  let companyField = {};
  try {
    companyField = JSON.parse(companyProbe || "{}");
  } catch (e) {
    companyField = { found: false };
  }
  check(
    "ENRICH LinkedIn tab exposes a company or domain input",
    !!companyField.found,
    companyField.found
      ? `id=${companyField.id || "(none)"} name=${companyField.name || "(none)"}`
      : `neither exists; inputs=${JSON.stringify(companyField.ids || [])}`
  );

  const url = "https://www.linkedin.com/in/maya-okafor-1a2b";
  await ev(`(() => {
    const set = (el, value) => {
      const proto = Object.getOwnPropertyDescriptor(el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype, 'value');
      proto.set.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    const ta = document.querySelector('#li-urls') || document.querySelector('#enrich-body textarea, #view textarea');
    if (ta) set(ta, ${JSON.stringify(url)});
    const body = document.getElementById('enrich-body') || document.getElementById('view');
    const company = Array.from(body.querySelectorAll('input')).find(el => /company|domain/i.test((el.id || '') + ' ' + (el.name || '')));
    if (company) set(company, 'Stripe');
    const start = document.getElementById('li-go') ||
      Array.from(body.querySelectorAll('button')).find(b => /start|enrich|run/i.test(b.textContent));
    if (start) start.click();
    return true;
  })()`);

  // Job summary renders Found / Credits spent once the worker finishes.
  const jobRaw = await poll(
    `(() => {
      const panel = document.getElementById('job-panel') || document.getElementById('view');
      const text = panel ? panel.innerText : '';
      const status = (text.match(/\\b(queued|running|done|failed)\\b/i) || [])[1] || '';
      return JSON.stringify({ status: status.toLowerCase(), text: text.slice(0, 500) });
    })()`,
    (raw) => {
      try {
        return /done|failed/.test(JSON.parse(raw).status);
      } catch (e) {
        return false;
      }
    },
    60000,
    1000
  );
  let job = {};
  try {
    job = JSON.parse(jobRaw || "{}");
  } catch (e) {
    job = { text: String(jobRaw || "") };
  }
  const foundMatch = String(job.text || "").match(/found[^\d]{0,20}([\d,]+)/i);
  const creditsMatch = String(job.text || "").match(/credits?\s*spent[^\d]{0,20}([\d,]+)/i);
  const foundN = foundMatch ? Number(foundMatch[1].replace(/,/g, "")) : NaN;
  const creditsN = creditsMatch ? Number(creditsMatch[1].replace(/,/g, "")) : NaN;
  check(
    "ENRICH job reaches done with found >= 1 and credits spent >= 1",
    job.status === "done" && foundN >= 1 && creditsN >= 1,
    `status=${job.status || "?"} found=${Number.isNaN(foundN) ? "?" : foundN} creditsSpent=${Number.isNaN(creditsN) ? "?" : creditsN} text=${JSON.stringify(String(job.text || "").slice(0, 280))}`
  );

  // Per-row outcomes. The summary panel does not list them, so read the result
  // table if the UI rendered one, otherwise the job CSV the contract exposes.
  const rowOutcomes = await ev(`(async () => {
    const allowed = ${JSON.stringify(OUTCOMES)};
    const panel = document.getElementById('job-panel') || document.getElementById('view');
    const rows = Array.from(panel.querySelectorAll('tbody tr, li, .result-row'));
    const fromDom = rows.map(r => {
      const cell = r.querySelector('[data-outcome]') || r;
      const text = (cell.dataset && cell.dataset.outcome) || cell.innerText || '';
      return allowed.find(o => new RegExp('\\\\b' + o + '\\\\b').test(text));
    }).filter(Boolean);
    if (fromDom.length) return JSON.stringify({ source: 'dom', outcomes: fromDom, rows: rows.length });
    const link = panel.querySelector('#job-download, a[href*="/jobs/"][href*="download"]');
    const href = link ? (link.getAttribute('href') || '') : '';
    const m = (panel.innerText || '').match(/Job\\s+([0-9a-f]{8})/i);
    const token = localStorage.getItem('lead_token') || '';
    let csv = '';
    if (href) {
      const res = await fetch(href, { headers: { Authorization: 'Bearer ' + token } });
      csv = await res.text();
    } else if (m) {
      const list = await fetch('/api/jobs', { headers: { Authorization: 'Bearer ' + token } }).then(r => r.json());
      const job = (list.items || []).find(j => (j.id || '').startsWith(m[1]));
      if (job) {
        const res = await fetch('/api/jobs/' + job.id + '/download', { headers: { Authorization: 'Bearer ' + token } });
        csv = await res.text();
      }
    }
    const outcomes = allowed.filter(o => new RegExp('\\\\b' + o + '\\\\b').test(csv));
    const lines = csv.split(/\\r?\\n/).filter(l => l.trim()).length;
    return JSON.stringify({ source: 'csv', outcomes, rows: Math.max(0, lines - 1), head: csv.slice(0, 240) });
  })()`);
  let outcomes = {};
  try {
    outcomes = JSON.parse(rowOutcomes || "{}");
  } catch (e) {
    outcomes = { outcomes: [], rows: 0 };
  }
  const shown = outcomes.outcomes || [];
  check(
    "ENRICH per-row result rendered with a known outcome",
    Number(outcomes.rows) >= 1 && shown.length >= 1 && shown.every((o) => OUTCOMES.includes(o)),
    `source=${outcomes.source || "?"} rows=${outcomes.rows} outcomes=${JSON.stringify(shown)} ${outcomes.head ? "head=" + JSON.stringify(outcomes.head) : ""}`
  );

  // ---------- SETTINGS ----------
  await goHash('#/settings');
  await poll(
    `(document.getElementById('view') || {}).innerText || ''`,
    (t) => /hunter|apollo|people data labs|\bpdl\b/i.test(String(t)),
    10000
  );
  const settingsText = String((await ev("(document.getElementById('view') || {}).innerText || ''")) || "");
  const providerSection = await ev(`(() => {
    const view = document.getElementById('view');
    const blocks = Array.from(view.querySelectorAll('section, .panel, div'));
    const hit = blocks.find(b => /hunter|apollo|people data labs|\\bpdl\\b/i.test(b.innerText || '') && (b.innerText || '').length < 2000);
    return hit ? hit.innerText.slice(0, 240) : '';
  })()`);
  check(
    "SETTINGS data-provider section mentions Hunter, Apollo or PDL",
    /hunter|apollo|people data labs|\bpdl\b/i.test(settingsText),
    JSON.stringify(String(providerSection || settingsText).slice(0, 240))
  );

  const beforeKeys = jsErrors.length;
  const typed2 = await ev(`(() => {
    const view = document.getElementById('view');
    const input = view.querySelector('input[id*="hunter" i], input[name*="hunter" i], input[id*="apollo" i], input[name*="apollo" i], input[id*="pdl" i], input[placeholder*="key" i], input[type="password"]');
    if (!input) return 'no key input';
    const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    proto.set.call(input, 'le_regression_probe_key');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return 'typed into ' + (input.id || input.name || input.placeholder || 'input');
  })()`);
  await sleep(600);
  const settingsAlive = await ev(`!!document.getElementById('view') && (document.getElementById('view').innerText || '').length > 40`);
  check(
    "SETTINGS entering a provider key does not crash the page",
    settingsAlive === true && jsErrors.length === beforeKeys,
    `${typed2}; newExceptions=${jsErrors.length - beforeKeys}`
  );

  // ---------- GLOBAL ----------
  check("GLOBAL zero uncaught JS exceptions", jsErrors.length === 0, jsErrors.slice(0, 3).join(" | ").slice(0, 400));
  check("GLOBAL zero HTTP 5xx", http5xx.length === 0, http5xx.slice(0, 5).join(" | "));
  if (http4xx.length) console.log("  HTTP 4xx (reported, not failed): " + http4xx.slice(0, 12).join(" | "));
  else console.log("  HTTP 4xx: none");

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n===== ${checks.length - failed.length}/${checks.length} regression checks passed =====`);
  failed.forEach((f) => console.log("  FAILED: " + f.name + (f.detail ? " :: " + f.detail : "")));
  ws.close();
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.error("driver: " + (e && e.stack ? e.stack : e));
  process.exitCode = 2;
});
