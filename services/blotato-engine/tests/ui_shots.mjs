// Capture PNG screenshots of key UI screens via CDP.
import { writeFileSync } from "node:fs";

const BASE = "http://localhost:8400";
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
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise(res => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evaluate = async e => (await send("Runtime.evaluate", {
    expression: e, returnByValue: true, awaitPromise: true })).result?.result?.value;

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });

  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 1500));
  await evaluate(`localStorage.setItem('blotato_key','blt_master_venu_secret_2026')`);

  const shots = [
    ["dashboard", "#dashboard"],
    ["compose", "#compose"],
    ["inbox", "#inbox"],
    ["visuals", "#visuals"],
    ["analytics", "#analytics"],
  ];

  for (const [name, hash] of shots) {
    await send("Page.navigate", { url: BASE + "/" + hash });
    await new Promise(r => setTimeout(r, 2600));
    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    const data = shot.result?.data;
    if (!data) { console.log("FAIL", name); continue; }
    writeFileSync(`${OUT}/ui-${name}.png`, Buffer.from(data, "base64"));
    console.log("saved", `ui-${name}.png`, Buffer.from(data, "base64").length, "bytes");
  }
  ws.close();
}

main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
