// Deep UI audit: drive each screen, capture text, errors, toasts and control behaviour.
const BASE = "http://localhost:8500";
const CDP = process.env.CDP_URL;
const http = async (p, o) => (await fetch(CDP + p, o)).json();

async function main() {
  const t = (await http("/json/list")).find(x => x.type === "page");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));
  let id = 0; const pending = new Map(); const errors = []; const netFail = [];
  ws.addEventListener("message", e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errors.push("console: " + m.params.args.map(a => a.value ?? a.description).join(" "));
    if (m.method === "Network.responseReceived" && m.params.response.status >= 400) netFail.push(m.params.response.status + " " + m.params.response.url.replace(BASE, ""));
  });
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async x => (await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const go = async route => { await ev(`location.hash = '#/${route}'`); await sleep(1500); };

  await send("Runtime.enable"); await send("Page.enable"); await send("Network.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await sleep(2000);
  await ev("localStorage.clear()");
  await send("Page.navigate", { url: BASE });
  await sleep(2000);
  await ev("document.getElementById('login-password').value='leadengine123'");
  await ev("document.getElementById('login-form').requestSubmit()");
  await sleep(3500);

  // ---------------- PROSPECTOR
  await go("prospector");
  await ev("(document.getElementById('search-go')||{click(){}}).click()");
  await sleep(3000);
  const pros = await ev(`JSON.stringify({
    rows: document.querySelectorAll('#search-results tbody tr').length,
    text: (document.getElementById('search-results')||{}).innerText || '',
    revealBtns: document.querySelectorAll('[data-reveal]').length,
    checkboxes: document.querySelectorAll('[data-pick]').length,
    empty: !!document.querySelector('.empty-state')
  })`);
  console.log("PROSPECTOR:", pros);

  // reveal first row
  const revealed = await ev(`(async () => {
    const btn = document.querySelector('[data-reveal]');
    if (!btn) return 'no reveal button';
    btn.click();
    await new Promise(r => setTimeout(r, 6000));
    const toast = Array.from(document.querySelectorAll('.toast')).map(t => t.innerText).join(' | ');
    return toast || 'no toast';
  })()`);
  console.log("REVEAL RESULT:", revealed);

  // ---------------- LISTS
  await go("lists");
  const listsBefore = await ev("(document.getElementById('view')||{}).innerText || ''");
  const folderMade = await ev(`(async () => {
    const name = document.getElementById('folder-name');
    const color = document.getElementById('folder-color');
    if (!name) return 'no folder form';
    name.value = 'Color Probe';
    if (color) color.value = '#ff3366';
    const btn = document.querySelector('#folder-form button, #create-folder, .folder-form button');
    if (btn) btn.click(); else {
      const f = document.getElementById('folder-form');
      if (f) f.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
      else return 'no submit control';
    }
    await new Promise(r => setTimeout(r, 2500));
    return 'submitted';
  })()`);
  await sleep(2000);
  const listsAfter = await ev(`JSON.stringify({
    text: (document.getElementById('view')||{}).innerText || '',
    swatches: Array.from(document.querySelectorAll('[style*="background"]')).slice(0,8).map(e => e.getAttribute('style')),
    dots: Array.from(document.querySelectorAll('.folder-dot, .dot, .swatch')).map(e => getComputedStyle(e).backgroundColor)
  })`);
  console.log("LISTS submit:", folderMade);
  console.log("LISTS before:", JSON.stringify(String(listsBefore).slice(0, 200)));
  console.log("LISTS after:", listsAfter);

  // ---------------- ENRICH
  await go("enrich");
  const enrichText = await ev("(document.getElementById('view')||{}).innerText || ''");
  console.log("ENRICH screen:", JSON.stringify(String(enrichText).slice(0, 300)));

  const enrichRun = await ev(`(async () => {
    const ta = document.querySelector('textarea');
    if (!ta) return 'no textarea';
    ta.value = 'https://www.linkedin.com/in/maya-okafor-1a2b';
    const company = document.querySelector('input[name="company"], #enrich-company, #company-hint');
    if (company) company.value = 'Stripe';
    const btn = Array.from(document.querySelectorAll('button')).find(b => /enrich|run|start/i.test(b.innerText));
    if (!btn) return 'no run button';
    btn.click();
    await new Promise(r => setTimeout(r, 9000));
    return (document.getElementById('view')||{}).innerText.slice(0, 400);
  })()`);
  console.log("ENRICH run:", JSON.stringify(enrichRun));

  // ---------------- VERIFIER
  await go("verifier");
  const verif = await ev(`(async () => {
    const ta = document.querySelector('textarea');
    if (!ta) return 'no textarea';
    ta.value = 'info@stripe.com';
    const btn = Array.from(document.querySelectorAll('button')).find(b => /verify/i.test(b.innerText));
    if (!btn) return 'no verify button';
    btn.click();
    await new Promise(r => setTimeout(r, 5000));
    return (document.getElementById('view')||{}).innerText.slice(0, 300);
  })()`);
  console.log("VERIFIER:", JSON.stringify(verif));

  console.log("\nHTTP 4xx/5xx:", netFail.slice(0, 10));
  console.log("JS errors:", errors.slice(0, 6));
  ws.close();
}
main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
