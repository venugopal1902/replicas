// Capture PNG screenshots of the Lead Engine UI over CDP.
import { writeFileSync } from "node:fs";

const BASE = "http://localhost:8500";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";
const OUT = process.env.OUT_DIR || ".";
const http = async (p, o) => (await fetch(CDP + p, o)).json();

async function main() {
  const targets = await http("/json/list");
  const page = targets.find(t => t.type === "page") || await http("/json/new?about:blank", { method: "PUT" });
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));

  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise(res => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const ev = async x => (await send("Runtime.evaluate", {
    expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });

  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 2000));
  await ev("localStorage.clear()");
  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 2000));
  await ev("document.getElementById('login-form').requestSubmit()");
  await new Promise(r => setTimeout(r, 3000));

  const shots = [
    ["dashboard", "dashboard", 2200],
    ["prospector", "prospector", 2000],
    ["enrich", "enrich", 1800],
    ["verifier", "verifier", 1600],
    ["campaigns", "campaigns", 1600],
    ["api", "api", 1600],
  ];

  for (const [name, route, wait] of shots) {
    await ev(`location.hash = '#/${route}'`);
    await new Promise(r => setTimeout(r, wait));
    if (route === "prospector") {
      await ev("(document.getElementById('search-go')||{click(){}}).click()");
      await new Promise(r => setTimeout(r, 3000));
    }
    const png = (await send("Page.captureScreenshot", { format: "png" })).result?.data;
    if (!png) { console.log("FAIL", name); continue; }
    writeFileSync(`${OUT}/ui-${name}.png`, Buffer.from(png, "base64"));
    console.log("saved", `ui-${name}.png`, Buffer.from(png, "base64").length, "bytes");
  }
  ws.close();
}

main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
