// Dump what the Lists and Prospector screens render, step by step.
const BASE = "http://localhost:8500";
const CDP = process.env.CDP_URL;
const http = async (p, o) => (await fetch(CDP + p, o)).json();
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const t = (await http("/json/list")).find(x => x.type === "page");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));
  let id = 0; const pending = new Map(); const errors = [];
  ws.addEventListener("message", e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  });
  const send = (m, p = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method: m, params: p })); });
  const ev = async x => (await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;

  await send("Runtime.enable"); await send("Page.enable");
  await send("Page.navigate", { url: BASE });
  await sleep(2500);
  console.log("logged in:", await ev("!!localStorage.getItem('lead_token')"));
  console.log("login hidden:", await ev("document.getElementById('login').hidden"));

  await ev("location.hash='#/lists'"); await sleep(2500);
  console.log("\nLISTS view text:\n" + String(await ev("(document.getElementById('view')||{}).innerText||''")).slice(0, 400));
  console.log("list ids present:", await ev("JSON.stringify(['folder-name','folder-color','folder-create','folder-form'].map(i=>[i,!!document.getElementById(i)]))"));

  await ev("location.hash='#/prospector'"); await sleep(2500);
  console.log("\nPROSPECTOR view text:\n" + String(await ev("(document.getElementById('view')||{}).innerText||''")).slice(0, 500));
  console.log("ids:", await ev("JSON.stringify(['search-go','search-input','search-results'].map(i=>[i,!!document.getElementById(i)]))"));
  console.log("exceptions:", errors.slice(0, 4));
  ws.close();
}
main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
