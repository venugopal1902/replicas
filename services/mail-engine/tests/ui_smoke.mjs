// Headless UI verification for Mail Engine over CDP.
// Launch chrome first:
//   chrome --headless=new --remote-debugging-port=9333 --user-data-dir=./.tmp-chrome about:blank
//   CDP_URL=http://127.0.0.1:9333 node tests/ui_smoke.mjs
const BASE = process.env.MAIL_BASE || "http://localhost:8640";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";
const http = async (p, o) => (await fetch(CDP + p, o)).json();

async function main() {
  const targets = await http("/json/list");
  const page = targets.find((t) => t.type === "page") || (await http("/json/new?about:blank", { method: "PUT" }));
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));

  let id = 0;
  const pending = new Map();
  const errors = [];
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
    if (m.method === "Runtime.exceptionThrown") {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      errors.push(m.params.args.map((a) => a.value ?? a.description).join(" "));
    }
  });
  const send = (method, params = {}) =>
    new Promise((res) => {
      const mid = ++id;
      pending.set(mid, res);
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  const ev = async (expr) =>
    (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result
      ?.value;

  const checks = [];
  const check = (name, ok, detail = "") => {
    checks.push({ name, ok, detail });
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " :: " + detail : ""}`);
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (expr, ms = 12000) => {
    const started = Date.now();
    while (Date.now() - started < ms) {
      if (await ev(expr)) return true;
      await sleep(150);
    }
    return false;
  };

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1480, height: 940, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE + "/" });
  await waitFor("document.readyState === 'complete'");

  // 1. login screen renders
  const loginVisible = await waitFor("(() => { const l = document.getElementById('login'); return l && !l.hidden && !!document.getElementById('login-email'); })()");
  check("01_login_screen_visible", loginVisible);

  // 2. wrong password is rejected and stays on the login screen
  await ev(`(() => {
    const e = document.getElementById('login-email'); const p = document.getElementById('login-password');
    e.value = 'admin@mailengine.local'; p.value = 'wrong-password-xyz';
    document.getElementById('login-form').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    return true;
  })()`);
  const badMsg = await waitFor("(() => { const el = document.getElementById('login-error'); return el && el.textContent.trim().length > 3; })()");
  const stillLogin = await ev("(() => { const a = document.getElementById('app'); return !a || a.hidden; })()");
  check("02_bad_password_rejected_in_ui", badMsg && stillLogin);

  // 3. real login reaches the app shell
  await ev(`(() => {
    const e = document.getElementById('login-email'); const p = document.getElementById('login-password');
    e.value = 'admin@mailengine.local'; p.value = 'mailengine123';
    document.getElementById('login-form').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    return true;
  })()`);
  const loggedIn = await waitFor("(() => { const a = document.getElementById('app'); const l = document.getElementById('login'); return a && !a.hidden && l && l.hidden; })()", 15000);
  check("03_login_success_shows_app", loggedIn);

  // 4. dashboard renders KPI cards + campaign table
  const dash = await waitFor("(() => { const v = document.getElementById('view'); return v && v.textContent.includes('Sent') && v.querySelectorAll('.stat-grid, .panel').length >= 2; })()", 15000);
  const dashText = await ev("document.getElementById('view')?.innerText.slice(0, 200) || ''");
  check("04_dashboard_renders", dash, dashText.replace(/\s+/g, " ").slice(0, 60));

  // 5. daily chart bars present
  const bars = await ev("document.querySelectorAll('#view .bar, #view .chart-bar, #view .day-bar, #view [class*=bar]').length");
  check("05_dashboard_chart_bars", bars > 0, `bars=${bars}`);

  // 6. campaigns list has rows
  await ev("location.hash = '#/campaigns'; true");
  const campRows = await (async () => { await waitFor("document.getElementById('view')?.querySelectorAll('tbody tr').length > 0", 15000); return ev("document.querySelectorAll('#view tbody tr').length"); })();
  const campNames = await ev("document.getElementById('view')?.innerText.includes('Q4 Data Engineering Outreach')");
  check("06_campaigns_list_rows", campRows > 0 && !!campNames, `rows=${campRows}`);

  // 7. campaign detail opens with steps + preview
  await ev(`(() => {
    const a = document.querySelector("#view a[href*='#/campaign/']") || document.querySelector("#view tbody tr");
    if (!a) return false;
    a.click(); return true;
  })()`);
  const detail = await waitFor("(() => { const v = document.getElementById('view'); const t = v?.innerText || ''; return t.includes('Step') && v.querySelectorAll('input, textarea').length > 0; })()", 15000);
  const hash = await ev("location.hash");
  check("07_campaign_detail_steps", detail, `hash=${hash}`);

  // 8. preview renders merged copy
  await ev(`(() => {
    const btn = [...document.querySelectorAll('#view button')].find(b => /preview/i.test(b.textContent));
    if (btn) { btn.click(); return 'clicked'; } return 'no-button';
  })()`);
  const preview = await waitFor("(() => { const v = document.getElementById('view'); return /Hi\\s+[A-Z]/.test(v.innerText) || v.querySelectorAll('.preview-panel').length > 0; })()", 12000);
  check("08_campaign_preview_renders", preview);

  // 9. leads list renders seeded leads
  await ev("location.hash = '#/leads'; true");
  const leadRows = await (async () => { await waitFor("document.querySelectorAll('#view tbody tr').length > 0", 15000); return ev("document.querySelectorAll('#view tbody tr').length"); })();
  check("09_leads_rows", leadRows > 0, `rows=${leadRows}`);

  // 10. leads search filters
  const searchOk = await ev(`(() => {
    const inp = document.querySelector("#view input[type='search'], #view input[placeholder*='earch'], #view .filters input");
    if (!inp) return 'no-input';
    inp.value = 'northwind';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    return 'typed';
  })()`);
  await sleep(1200);
  const filtered = await ev("document.querySelectorAll('#view tbody tr').length");
  check("10_leads_search_filters", searchOk === "typed" && filtered >= 1, `rows=${filtered}`);

  // 11. inbox renders messages
  await ev("location.hash = '#/inbox'; true");
  const inboxOk = await waitFor("(() => { const v = document.getElementById('view'); return v && v.innerText.length > 200 && !/Loading/i.test(v.innerText); })()", 15000);
  const unread = await ev("document.getElementById('nav-unread-badge')?.textContent?.trim() || ''");
  check("11_inbox_renders", inboxOk, `badge=${unread}`);

  // 12. mailboxes + warmup scores
  await ev("location.hash = '#/mailboxes'; true");
  const mbOk = await waitFor("(() => { const t = document.getElementById('view')?.innerText || ''; return t.includes('northvale-outreach.com') && /\\d+/.test(t); })()", 15000);
  check("12_mailboxes_warmup", mbOk);

  // 13. settings + suppressions
  await ev("location.hash = '#/settings'; true");
  const setOk = await waitFor("(() => { const v = document.getElementById('view'); const t = v?.innerText || ''; return /suppress/i.test(t); })()", 15000);
  check("13_settings_suppressions", setOk);

  // 14. no uncaught JS exceptions or console errors across the run
  const realErrors = errors.filter((e) => !/favicon|Failed to load resource.*404/i.test(e));
  check("14_no_js_exceptions", realErrors.length === 0, realErrors.slice(0, 2).join(" | ").slice(0, 160));

  // 15. no 5xx or failed API calls in the browser
  const failed = await ev(`(() => {
    const bad = performance.getEntriesByType('resource').filter(r => r.name.includes('/api/') && r.responseStatus >= 500);
    return bad.length;
  })()`);
  check("15_no_browser_5xx", (failed ?? 0) === 0, `bad=${failed}`);

  const passed = checks.filter((c) => c.ok).length;
  console.log(`\n${passed}/${checks.length} UI checks passed`);
  ws.close();
  process.exit(passed === checks.length ? 0 : 1);
}

main().catch((e) => {
  console.error("UI smoke crashed:", e.message);
  process.exit(2);
});
