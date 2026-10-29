// Verify the three fixes: folder colour, prospector reveal, LinkedIn enrichment with company.
const BASE = "http://localhost:8500";
const CDP = process.env.CDP_URL;
const http = async (p, o) => (await fetch(CDP + p, o)).json();

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
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async x => (await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  await send("Runtime.enable"); await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await sleep(2000);
  await ev("localStorage.setItem('lead_token', localStorage.getItem('lead_token')||'')");
  await ev("document.getElementById('login-password') && (document.getElementById('login-password').value='leadengine123')");
  await ev("document.getElementById('login-form') && document.getElementById('login-form').requestSubmit()");
  // wait until the app is actually visible before touching any screen
  for (let i = 0; i < 40; i++) {
    const ok = await ev("getComputedStyle(document.getElementById('app')).display !== 'none'");
    if (ok) break;
    await sleep(500);
  }
  console.log("app unlocked:", await ev("getComputedStyle(document.getElementById('app')).display"));

  // ---- 1. FOLDER COLOUR
  await ev("location.hash='#/lists'"); await sleep(1500);
  const made = await ev(`(async () => {
    const n = document.getElementById('folder-name'); const c = document.getElementById('folder-color');
    if (!n) return 'no folder form';
    n.value = 'Colour Test'; if (c) c.value = '#ff3366';
    document.getElementById('folder-create').click();
    await new Promise(r => setTimeout(r, 2000));
    const rows = Array.from(document.querySelectorAll('tbody tr'));
    const row = rows.find(r => r.innerText.includes('Colour Test'));
    if (!row) return 'folder row missing';
    const sw = row.querySelector('.swatch, .folder-dot');
    if (!sw) return 'no swatch element in row';
    const cs = getComputedStyle(sw);
    return JSON.stringify({ bg: cs.backgroundColor, w: cs.width, h: cs.height, style: sw.getAttribute('style') });
  })()`);
  console.log("FOLDER COLOUR:", made);

  // ---- 2. PROSPECTOR REVEAL
  await ev("location.hash='#/prospector'"); await sleep(1200);
  await ev("document.getElementById('search-go').click()"); await sleep(3000);
  const reveal = await ev(`(async () => {
    const btn = document.querySelector('[data-reveal]');
    if (!btn) return 'no unrevealed row available';
    btn.click();
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 750));
      const toast = Array.from(document.querySelectorAll('.toast')).map(t => t.innerText).join(' | ');
      if (toast) return toast;
    }
    return 'no toast after 30s';
  })()`);
  console.log("PROSPECTOR REVEAL:", reveal);

  // ---- 3. LINKEDIN ENRICH
  await ev("location.hash='#/enrich'"); await sleep(1500);
  const hasCompany = await ev("!!document.getElementById('li-company')");
  console.log("LINKEDIN has company field:", hasCompany);
  const enrich = await ev(`(async () => {
    document.getElementById('li-demo').click();
    document.getElementById('li-go').click();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const t = document.getElementById('job-panel').innerText || '';
      if (/done|failed/i.test(t) && t.length > 40) return t.slice(0, 500);
    }
    return 'job did not finish in 60s: ' + (document.getElementById('job-panel').innerText||'').slice(0,200);
  })()`);
  console.log("LINKEDIN JOB:\n" + enrich);

  // ---- 4. SETTINGS PROVIDERS
  await ev("location.hash='#/settings'"); await sleep(2000);
  const settings = await ev(`JSON.stringify({
    hasPanel: (document.getElementById('view').innerText||'').includes('Data providers'),
    hunter: !!document.getElementById('pv-hunter'),
    status: (document.getElementById('pv-status')||{}).textContent || ''
  })`);
  console.log("SETTINGS:", settings);

  console.log("JS EXCEPTIONS:", errors.slice(0, 5));
  ws.close();
}
main().catch(e => { console.error("driver:", e.message); process.exitCode = 2; });
