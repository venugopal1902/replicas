// Diagnose the login submit path.
const BASE = "http://localhost:8500";
const CDP = process.env.CDP_URL;
const http = async (p, o) => (await fetch(CDP + p, o)).json();

async function main() {
  const t = (await http("/json/list")).find(x => x.type === "page");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));
  let id = 0; const pending = new Map(); const errs = []; const net = [];
  ws.addEventListener("message", e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === "Runtime.exceptionThrown") errs.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === "Runtime.consoleAPICalled") errs.push(m.params.type + ": " + m.params.args.map(a => a.value ?? a.description).join(" "));
    if (m.method === "Network.responseReceived") net.push(m.params.response.status + " " + m.params.response.url.replace(BASE, ""));
  });
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async x => (await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;

  await send("Runtime.enable"); await send("Page.enable"); await send("Network.enable");
  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 2000));
  await ev("localStorage.clear()");
  await send("Page.navigate", { url: BASE });
  await new Promise(r => setTimeout(r, 2000));

  console.log("before submit:", await ev("JSON.stringify({login: document.getElementById('login').hidden, app: document.getElementById('app').hidden, email: document.getElementById('login-email').value, pass: document.getElementById('login-password').value})"));

  await ev("document.getElementById('login-form').requestSubmit()");
  await new Promise(r => setTimeout(r, 3000));

  console.log("after submit:", await ev("JSON.stringify({login: document.getElementById('login').hidden, app: document.getElementById('app').hidden, loginDisplay: getComputedStyle(document.getElementById('login')).display, appDisplay: getComputedStyle(document.getElementById('app')).display, error: document.getElementById('login-error').textContent, token: !!localStorage.getItem('lead_token')})"));
  console.log("\nnetwork:");
  net.filter(n => n.includes("/api/")).forEach(n => console.log("  ", n));
  console.log("\nconsole/exceptions:");
  console.log(errs.slice(0, 8).join("\n") || "  none");
  ws.close();
}
main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
