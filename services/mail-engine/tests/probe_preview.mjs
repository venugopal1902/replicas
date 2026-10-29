const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";
const BASE = process.env.MAIL_BASE || "http://localhost:8640";
const http = async (p, o) => (await fetch(CDP + p, o)).json();

const targets = await http("/json/list");
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}) => new Promise((res) => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
const ev = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Runtime.enable");
await ev("location.hash = '#/campaigns'; true");
await sleep(1500);
await ev(`(() => { const a = document.querySelector("#view a[href*='#/campaign/']"); if (a) a.click(); return location.hash; })()`);
await sleep(2500);
console.log("hash:", await ev("location.hash"));
console.log("buttons:", await ev(`[...document.querySelectorAll('#view button')].map(b => b.textContent.trim()).join(' | ')`));
console.log("dataActions:", await ev(`[...document.querySelectorAll('#view [data-action]')].map(b => b.dataset.action).join(',')`));
console.log("click:", await ev(`(() => { const b = document.querySelector("#view [data-action='preview']"); if (!b) return 'missing'; b.click(); return 'clicked'; })()`));
await sleep(2500);
console.log("host:", (await ev("document.getElementById('preview-host')?.innerHTML || 'EMPTY'")).slice(0, 400));
console.log("panels:", await ev("document.querySelectorAll('#view .preview-panel').length"));
console.log("toast:", await ev("document.getElementById('toasts')?.innerText || ''"));
ws.close();
process.exit(0);
