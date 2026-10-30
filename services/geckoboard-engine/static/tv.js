const root = document.getElementById('tv-screen');
const publicId = root?.dataset.rotation || '';
const host = document.getElementById('dashboard-host');
let screenLoop = null;
let refreshLoop = null;
let rotation = null;
let screenIndex = 0;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function renderNumber(widget, card) {
  card.append(el('div', 'number-value', widget.result?.value ?? 0));
  card.append(el('div', 'widget-foot', `${widget.aggregate || 'sum'} · ${widget.dataset_id}`));
}

function renderChart(widget, card) {
  const items = widget.result?.items || [];
  if (!items.length) { card.append(el('div', 'empty', 'No records yet')); return; }
  const list = el('div', 'chart-list');
  const max = Math.max(1, ...items.map((item) => Math.abs(Number(item.value) || 0)));
  items.forEach((item) => {
    const row = el('div', 'chart-row');
    row.append(el('span', '', item.label));
    const track = el('div', 'chart-track');
    const fill = el('div', 'chart-fill');
    fill.style.width = `${Math.max(2, Math.min(100, Math.abs(Number(item.value) || 0) / max * 100))}%`;
    track.append(fill);
    row.append(track, el('span', 'chart-value', item.value));
    list.append(row);
  });
  card.append(list);
}

function renderTable(widget, card) {
  const records = widget.result?.items || [];
  if (!records.length) { card.append(el('div', 'empty', 'No records yet')); return; }
  const keys = Object.keys(records[0]).slice(0, 10);
  const table = el('table', 'widget-table');
  const head = el('thead');
  const headRow = el('tr');
  keys.forEach((key) => headRow.append(el('th', '', key)));
  head.append(headRow);
  const body = el('tbody');
  records.forEach((record) => {
    const row = el('tr');
    keys.forEach((key) => row.append(el('td', '', record[key] ?? '')));
    body.append(row);
  });
  table.append(head, body);
  const wrap = el('div', 'table-wrap');
  wrap.append(table);
  card.append(wrap);
}

function renderDashboard(dashboard, index, total) {
  host.replaceChildren();
  const title = el('div', 'dashboard-title');
  title.append(el('h1', '', dashboard.name));
  title.append(el('span', '', `${dashboard.widgets.length} widgets · screen ${index + 1} of ${total}`));
  host.append(title);
  const grid = el('div', 'widgets');
  dashboard.widgets.forEach((widget) => {
    const card = el('article', 'widget');
    card.dataset.width = String(widget.width || 6);
    card.append(el('div', 'widget-title', widget.title));
    if (widget.type === 'number') renderNumber(widget, card);
    else if (widget.type === 'table') renderTable(widget, card);
    else renderChart(widget, card);
    grid.append(card);
  });
  host.append(grid);
  const progress = el('div', 'screen-progress');
  progress.append(el('i'));
  host.append(progress);
  const fill = progress.firstElementChild;
  const seconds = rotation?.interval_seconds || 30;
  fill.style.transitionDuration = `${seconds}s`;
  requestAnimationFrame(() => { fill.style.width = '100%'; });
  document.getElementById('screen-name').textContent = rotation?.name || 'Live screen';
  document.getElementById('screen-counter').textContent = `${index + 1} / ${total}`;
}

async function refresh() {
  try {
    const response = await fetch(`/api/public/rotations/${encodeURIComponent(publicId)}`, { credentials: 'omit', cache: 'no-store' });
    if (!response.ok) throw new Error(response.status === 404 ? 'This TV rotation is unavailable.' : 'Unable to load screen data.');
    rotation = await response.json();
    if (!rotation.dashboards?.length) throw new Error('This TV rotation has no dashboards yet.');
    screenIndex %= rotation.dashboards.length;
    renderDashboard(rotation.dashboards[screenIndex], screenIndex, rotation.dashboards.length);
    document.getElementById('last-updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
    if (screenLoop) clearInterval(screenLoop);
    screenLoop = setInterval(() => {
      if (!rotation?.dashboards?.length) return;
      screenIndex = (screenIndex + 1) % rotation.dashboards.length;
      renderDashboard(rotation.dashboards[screenIndex], screenIndex, rotation.dashboards.length);
    }, rotation.interval_seconds * 1000);
  } catch (error) {
    host.replaceChildren(el('div', 'loading error', error.message));
  }
}

refresh();
refreshLoop = setInterval(refresh, 60_000);
window.addEventListener('beforeunload', () => {
  if (screenLoop) clearInterval(screenLoop);
  if (refreshLoop) clearInterval(refreshLoop);
});
