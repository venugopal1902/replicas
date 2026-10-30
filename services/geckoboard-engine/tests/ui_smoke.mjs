// Headless UI verification for Geckoboard Engine over CDP.
//   chrome --headless=new --remote-debugging-port=9334 --user-data-dir=<scratch>/.tmp-chrome about:blank
//   GECKOBOARD_TEST_PORT=8641 GECKOBOARD_BASE=http://127.0.0.1:8641 CDP_URL=http://127.0.0.1:9334 node tests/ui_smoke.mjs
const baseOverride = process.env.GECKOBOARD_BASE;
const TEST_PORT = Number(process.env.GECKOBOARD_TEST_PORT || new URL(baseOverride || "http://127.0.0.1:8640").port || 8640);
const BASE = baseOverride || `http://127.0.0.1:${TEST_PORT}`;
const EXPECTED_ORIGIN = new URL(BASE).origin;
const CDP = process.env.CDP_URL || "http://127.0.0.1:9334";
const PASSWORD = process.env.GECKOBOARD_ADMIN_PASSWORD || "";
const http = async (p, o) => (await fetch(CDP + p, o)).json();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
    (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

  const checks = [];
  const check = (name, ok, detail = "") => {
    checks.push({ name, ok });
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " :: " + detail : ""}`);
  };
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
  await send("Network.enable");
  await send("Network.clearBrowserCookies");
  await send("Emulation.setDeviceMetricsOverride", { width: 1480, height: 940, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE + "/" });
  await sleep(600);
  check("page loads", await waitFor(`document.title === 'Geckoboard Engine'`), await ev("document.title"));
  check("login form visible", await ev(`!!document.getElementById('login-form') && !document.getElementById('login-view').classList.contains('hidden')`));

  await ev(`document.getElementById('login-email').value='admin@geckoboard.local';
    document.getElementById('login-password').value=${JSON.stringify(PASSWORD)};
    document.getElementById('login-form').dispatchEvent(new Event('submit',{cancelable:true,bubbles:true})); true`);
  check("signs in", await waitFor(`!document.getElementById('workspace').classList.contains('hidden')`));
  check("overview stats rendered", await waitFor(`document.querySelectorAll('#overview-stats .stat, #overview-stats > *').length > 0`), await ev(`document.getElementById('overview-stats').textContent.trim().slice(0,60)`));

  const tab = async (name, sel) => {
    await ev(`document.querySelector('.nav-item[data-tab="${name}"]').click(); true`);
    return waitFor(
      `document.querySelector('[data-panel="${name}"]').classList.contains('hidden') === false && document.querySelectorAll('${sel}').length > 0`,
      10000,
    );
  };
  check("datasets tab lists seeded datasets", await tab("datasets", "#dataset-list > *"), await ev(`document.querySelectorAll('#dataset-list > *').length`));
  check("dashboards tab lists seeded dashboards", await tab("dashboards", "#dashboard-list > *"), await ev(`document.querySelectorAll('#dashboard-list > *').length`));
  check("rotations tab lists seeded rotation", await tab("rotations", "#rotation-list > *"), await ev(`document.querySelectorAll('#rotation-list > *').length`));
  check("push example uses selected origin", await ev(`document.getElementById('push-example').textContent.includes(${JSON.stringify(EXPECTED_ORIGIN)})`));
  check("dataset key is not labeled in dataset list", await ev(`!document.getElementById('dataset-list').textContent.toLowerCase().includes('api_key')`));

  await ev(`document.getElementById('logout').click(); true`);
  check("sign out returns to login", await waitFor(`!document.getElementById('login-view').classList.contains('hidden')`));
  check("no JS exceptions", errors.length === 0, errors.slice(0, 3).join(" | "));

  const failed = checks.filter((c) => !c.ok).length;
  console.log(`\nTOTAL ${checks.length - failed} passed, ${failed} failed`);
  ws.close();
  process.exit(failed ? 1 : 0);
}

main().catch((error) => {
  console.error("UI smoke crashed:", error);
  process.exit(1);
});
