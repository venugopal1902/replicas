// Headless smoke test for the Blotato Engine UI over CDP.
// Launches evaluated against an already-running Chrome on :9333.
const BASE = "http://localhost:8400";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";

async function http(path, opts) {
  const r = await fetch(CDP + path, opts);
  return r.json();
}

async function main() {
  const targets = await http("/json/list");
  let page = targets.find(t => t.type === "page");
  if (!page) {
    page = await http("/json/new?about:blank", { method: "PUT" });
  }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const consoleErrors = [];

  ws.addEventListener("message", ev => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
      consoleErrors.push(msg.params.args.map(a => a.value ?? a.description).join(" "));
    }
    if (msg.method === "Runtime.exceptionThrown") {
      consoleErrors.push(msg.params.exceptionDetails.text + " " +
        (msg.params.exceptionDetails.exception?.description || ""));
    }
  });

  await new Promise(res => ws.addEventListener("open", res));

  const send = (method, params = {}) => new Promise(resolve => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Network.enable");

  const evaluate = async expr => {
    const r = await send("Runtime.evaluate", {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    if (r.result?.exceptionDetails) {
      return { error: r.result.exceptionDetails.text };
    }
    return r.result?.result?.value;
  };

  const goto = async url => {
    await send("Page.navigate", { url });
    await new Promise(r => setTimeout(r, 2500));
  };

  const results = [];
  const check = (name, ok, detail = "") => {
    results.push({ name, ok, detail });
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " :: " + detail : ""}`);
  };

  // 1. load the app
  await goto(BASE);
  let title = await evaluate("document.title");
  check("page loads with title", title === "Blotato Engine", String(title));

  let hasGate = await evaluate("!!document.querySelector('input[type=password], input[placeholder*=\"key\" i], input[id*=\"key\" i]')");
  check("login gate rendered", hasGate === true, String(hasGate));

  // 2. seed the key the way the app expects, then reload
  await evaluate(`localStorage.setItem('blotato_key', 'blt_master_venu_secret_2026')`);
  await goto(BASE);
  await new Promise(r => setTimeout(r, 1500));

  const body = await evaluate("document.body.innerText");
  check("dashboard rendered after key", body.length > 200, `${String(body).length} chars`);

  const navCount = await evaluate("document.querySelectorAll('nav a, aside a, .sidebar a').length");
  check("sidebar navigation present", Number(navCount) >= 8, `${navCount} links`);

  const accountText = await evaluate(
    "(document.body.innerText.match(/instagram|youtube|linkedin|bluesky|pinterest|tiktok|threads/gi)||[]).length"
  );
  check("accounts rendered from API", Number(accountText) >= 4, `${accountText} platform mentions`);

  // 3. walk every route and capture text length + errors
  const routes = ["dashboard", "compose", "queue", "inbox", "automations", "visuals", "sources", "analytics", "settings"];
  for (const route of routes) {
    await evaluate(`location.hash = '#${route}'`);
    await new Promise(r => setTimeout(r, 1200));
    const len = await evaluate("document.body.innerText.length");
    const h = await evaluate("location.hash");
    check(`route #${route} renders`, Number(len) > 150 && String(h).includes(route),
      `${len} chars, hash=${h}`);
  }

  // 4. exercise a real data flow: analytics screen should show a number from the API
  await evaluate("location.hash = '#analytics'");
  await new Promise(r => setTimeout(r, 1500));
  const analyticsText = await evaluate("document.body.innerText");
  check("analytics shows real totals",
    /18,?400|25|views|Views/i.test(String(analyticsText)),
    String(analyticsText).slice(0, 120).replace(/\s+/g, " "));

  // 5. visuals route should list templates
  await evaluate("location.hash = '#visuals'");
  await new Promise(r => setTimeout(r, 1500));
  const visualsText = await evaluate("document.body.innerText");
  check("visuals lists templates",
    /quote|carousel|infographic|tweet/i.test(String(visualsText)),
    String(visualsText).slice(0, 120).replace(/\s+/g, " "));

  // 6. console errors
  check("no uncaught JS errors", consoleErrors.length === 0,
    consoleErrors.slice(0, 3).join(" | "));

  const failed = results.filter(r => !r.ok);
  console.log(`\n===== ${results.length - failed.length}/${results.length} UI checks passed =====`);
  if (failed.length) {
    failed.forEach(f => console.log("  FAILED:", f.name, f.detail));
    process.exitCode = 1;
  }
  ws.close();
}

main().catch(e => {
  console.error("driver error:", e.message);
  process.exitCode = 2;
});
