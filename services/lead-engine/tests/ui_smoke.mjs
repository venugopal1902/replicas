// Headless UI verification for Lead Engine over CDP.
const BASE = "http://localhost:8500";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";
const http = async (p, o) => (await fetch(CDP + p, o)).json();

async function main() {
  const targets = await http("/json/list");
  const page = targets.find(t => t.type === "page") || await http("/json/new?about:blank", { method: "PUT" });
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));

  let id = 0;
  const pending = new Map();
  const errors = [];
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === "Runtime.exceptionThrown") {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      errors.push(m.params.args.map(a => a.value ?? a.description).join(" "));
    }
  });
  const send = (method, params = {}) => new Promise(res => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const ev = async expr => (await send("Runtime.evaluate", {
    expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

  const checks = [];
  const check = (name, ok, detail = "") => {
    checks.push({ name, ok, detail });
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " :: " + detail : ""}`);
  };

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1480, height: 940, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 2200));

  // start from a clean session so the login gate is exercised every run
  await ev("localStorage.clear(); sessionStorage.clear();");
  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 2200));

  const displayOf = async sel => ev(
    `(() => { const el = document.querySelector(${JSON.stringify(sel)}); return el ? getComputedStyle(el).display : 'missing'; })()`);

  check("page loads", (await ev("document.title")) === "Lead Engine", String(await ev("document.title")));

  const loginDisp = await displayOf("#login");
  const appDisp = await displayOf("#app");
  check("login screen visible and app hidden",
    loginDisp !== "none" && appDisp === "none",
    `login=${loginDisp} app=${appDisp}`);
  check("credentials prefilled", (await ev("document.getElementById('login-email').value")) === "admin@leadengine.local");

  // Sign in (password is no longer prefilled, so type it like a user would)
  await ev("document.getElementById('login-password').value = 'leadengine123'");
  await ev("document.getElementById('login-form').requestSubmit()");
  await new Promise(r => setTimeout(r, 3000));

  const submitState = await ev(`JSON.stringify({
    error: (document.getElementById('login-error')||{}).textContent || '',
    token: !!localStorage.getItem('lead_token'),
    appHidden: document.getElementById('app').hidden,
    loginHidden: document.getElementById('login').hidden
  })`);
  if (process.env.DEBUG_LOGIN) console.log("submit state:", submitState);

  const appDispAfter = await displayOf("#app");
  const loginDispAfter = await displayOf("#login");
  check("app unlocks and login is gone",
    appDispAfter !== "none" && loginDispAfter === "none",
    `app=${appDispAfter} login=${loginDispAfter}`);
  // wait for the first view to finish its data fetch instead of a fixed sleep
  for (let i = 0; i < 24; i++) {
    const len = Number(await ev("document.getElementById('view').innerText.length")) || 0;
    if (len > 100) break;
    await new Promise(r => setTimeout(r, 500));
  }
  const dash = await ev("document.getElementById('view').innerText");
  check("dashboard rendered", String(dash).length > 200, `${String(dash).length} chars`);
  check("credits widget populated", !String(await ev("document.getElementById('side-credits').textContent")).includes("--"),
    await ev("document.getElementById('side-credits').textContent"));

  const routes = ["prospector", "lists", "enrich", "verifier", "campaigns", "api", "settings", "dashboard"];
  for (const route of routes) {
    await ev(`location.hash = '#/${route}'`);
    await new Promise(r => setTimeout(r, 1800));
    const text = String(await ev("document.getElementById('view').innerText"));
    const title = String(await ev("document.getElementById('page-title').innerText"));
    check(`route ${route} renders`, text.length > 120, `${text.length} chars, title=${title}`);
  }

  // Prospector should show real seeded contacts after running a search
  await ev("location.hash = '#/prospector'");
  await new Promise(r => setTimeout(r, 1800));
  await ev("(document.getElementById('search-go')||{click(){}}).click()");
  await new Promise(r => setTimeout(r, 3200));
  const prospect = String(await ev("document.getElementById('view').innerText"));
  check("prospector lists seeded contacts",
    /Okafor|Mehta|Nwosu|Qureshi|Petrova|Fischer|Iyer/i.test(prospect),
    prospect.slice(0, 100).replace(/\s+/g, " "));
  check("prospector shows masked emails", prospect.includes("***"), "masked domain visible");

  // Lists screen should show the folder created by the API test
  await ev("location.hash = '#/lists'");
  await new Promise(r => setTimeout(r, 2000));
  const lists = String(await ev("document.getElementById('view').innerText"));
  check("lists screen shows folders", /default folder|Q4 Prospects/i.test(lists),
    lists.slice(0, 90).replace(/\s+/g, " "));

  // API screen should expose a key
  await ev("location.hash = '#/api'");
  await new Promise(r => setTimeout(r, 2000));
  const api = String(await ev("document.getElementById('view').innerText"));
  check("api screen shows a key", api.includes("lke_live_"), "key visible");
  check("api screen documents mcp", /mcp/i.test(api));

  check("no uncaught JS errors", errors.length === 0, errors.slice(0, 2).join(" | "));

  const failed = checks.filter(c => !c.ok);
  console.log(`\n===== ${checks.length - failed.length}/${checks.length} UI checks passed =====`);
  failed.forEach(f => console.log("  FAILED:", f.name, f.detail));
  if (failed.length) process.exitCode = 1;
  ws.close();
}

main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
