// Diagnose auth/rendering behaviour: capture every network response the UI makes.
const BASE = "http://localhost:8400";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";

const http = async (p, o) => (await fetch(CDP + p, o)).json();

async function main() {
  const targets = await http("/json/list");
  const page = targets.find(t => t.type === "page") || await http("/json/new?about:blank", { method: "PUT" });
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));

  let id = 0;
  const pending = new Map();
  const net = [];
  const errors = [];

  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === "Network.responseReceived") {
      const r = m.params.response;
      net.push({ status: r.status, url: r.url.replace(BASE, "") });
    }
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
  const evaluate = async e => (await send("Runtime.evaluate", {
    expression: e, returnByValue: true, awaitPromise: true,
  })).result?.result?.value;

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Network.enable");

  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 2000));

  console.log("localStorage before:", await evaluate("JSON.stringify(Object.keys(localStorage))"));

  await evaluate(`localStorage.setItem('blotato_key','blt_master_venu_secret_2026')`);
  await send("Page.navigate", { url: BASE + "/#analytics" });
  await new Promise(r => setTimeout(r, 3000));

  const txt = await evaluate("document.body.innerText");
  console.log("\n--- body text (first 500) ---");
  console.log(String(txt).slice(0, 500));
  console.log("\n--- localStorage after:", await evaluate("JSON.stringify(localStorage)"));

  await new Promise(r => setTimeout(r, 1500));
  console.log("\n--- network responses ---");
  for (const r of net) console.log(`${String(r.status).padEnd(4)} ${r.url}`);

  console.log("\n--- errors ---");
  console.log(errors.length ? errors.slice(0, 5).join("\n") : "none");
  ws.close();
}

main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
