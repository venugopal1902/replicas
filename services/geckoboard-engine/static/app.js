const $ = (id) => document.getElementById(id);
const state = { datasets: [], dashboards: [], rotations: [], draftWidgets: [] };

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const type = response.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(typeof data === 'object' ? (data.detail || `Request failed (${response.status})`) : `Request failed (${response.status})`);
  return data;
}

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const showMessage = (id, message, error = false) => {
  const target = $(id);
  target.textContent = message;
  target.classList.toggle('error', error);
};

function showTab(name) {
  document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.toggle('hidden', panel.dataset.panel !== name));
  document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.tab === name));
  const labels = { overview: 'Overview', datasets: 'Datasets', dashboards: 'Dashboards', rotations: 'TV rotations' };
  $('page-title').textContent = labels[name] || 'Overview';
  if (name === 'datasets') loadDatasets();
  if (name === 'dashboards') loadDashboards();
  if (name === 'rotations') loadRotations();
}

document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => showTab(button.dataset.tab)));
document.querySelectorAll('[data-go]').forEach((button) => button.addEventListener('click', () => showTab(button.dataset.go)));

async function loadWorkspace() {
  try {
    const [datasets, dashboards, rotations] = await Promise.all([
      api('/api/admin/datasets'), api('/api/admin/dashboards'), api('/api/admin/rotations'),
    ]);
    state.datasets = datasets.items;
    state.dashboards = dashboards.items;
    state.rotations = rotations.items;
    $('overview-stats').innerHTML = [
      ['Custom datasets', datasets.total], ['Dashboards', dashboards.total], ['TV rotations', rotations.total], ['Push API', 'Ready'],
    ].map(([label, value]) => `<div class="stat-card"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
    populateDatasetSelect();
    $('login-view').classList.add('hidden');
    $('workspace').classList.remove('hidden');
    $('workspace-label').classList.remove('hidden');
    $('workspace-selector').classList.remove('hidden');
    $('nav').classList.remove('hidden');
    $('logout').classList.remove('hidden');
    $('signed-in').classList.remove('hidden');
    showMessage('login-error', '');
  } catch (error) {
    $('workspace').classList.add('hidden');
    $('login-view').classList.remove('hidden');
    $('workspace-label').classList.add('hidden');
    $('workspace-selector').classList.add('hidden');
    $('nav').classList.add('hidden');
    $('logout').classList.add('hidden');
    $('signed-in').classList.add('hidden');
    const expectedUnauth = ['Admin session required', 'Invalid admin session', 'Admin session expired'];
    if (!expectedUnauth.includes(error.message)) showMessage('login-error', error.message, true);
  }
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  showMessage('login-error', '');
  try {
    await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ email: $('login-email').value, password: $('login-password').value }) });
    $('login-password').value = '';
    await loadWorkspace();
  } catch (error) { showMessage('login-error', error.message, true); }
});

$('logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* Session may already be expired. */ }
  $('workspace').classList.add('hidden');
  $('login-view').classList.remove('hidden');
  $('workspace-label').classList.add('hidden');
  $('workspace-selector').classList.add('hidden');
  $('nav').classList.add('hidden');
  $('logout').classList.add('hidden');
  $('signed-in').classList.add('hidden');
  $('login-password').value = '';
});

function populateDatasetSelect() {
  const select = $('widget-dataset');
  const current = select.value;
  select.innerHTML = state.datasets.map((dataset) => `<option value="${esc(dataset.id)}">${esc(dataset.id)}</option>`).join('');
  if (state.datasets.some((dataset) => dataset.id === current)) select.value = current;
  refreshWidgetFields();
  $('rotation-dashboards').innerHTML = state.dashboards.map((dashboard) => `<option value="${esc(dashboard.id)}">${esc(dashboard.name)}</option>`).join('');
}

function refreshWidgetFields() {
  const dataset = state.datasets.find((item) => item.id === $('widget-dataset').value);
  const fields = dataset ? Object.entries(dataset.fields) : [];
  const numeric = fields.filter(([, field]) => ['number', 'money', 'percentage', 'duration'].includes(field.type));
  const times = fields.filter(([, field]) => ['date', 'datetime'].includes(field.type));
  const categorical = fields.filter(([, field]) => ['date', 'datetime', 'string'].includes(field.type));
  const options = (items, includeNone = false) => `${includeNone ? '<option value="">None</option>' : ''}${items.map(([name, field]) => `<option value="${esc(name)}">${esc(field.name || name)} · ${esc(field.type)}</option>`).join('')}`;
  $('widget-metric').innerHTML = options(numeric, true);
  $('widget-dimension').innerHTML = options(categorical, true);
  $('widget-time').innerHTML = options(times, true);
}
$('widget-dataset').addEventListener('change', refreshWidgetFields);

async function loadDatasets() {
  try {
    const result = await api('/api/admin/datasets');
    state.datasets = result.items;
    populateDatasetSelect();
    $('dataset-list').innerHTML = state.datasets.length ? state.datasets.map((dataset) => `
      <article class="dataset-card"><div class="dataset-card-head"><strong>${esc(dataset.id)}</strong><span class="field-chip">${Object.keys(dataset.fields).length} fields</span></div>
      <div class="dataset-meta">Unique by: ${esc(dataset.unique_by.join(', ') || 'none')} · updated ${esc(dataset.updated_at)}</div>
      <div class="dataset-fields">${Object.entries(dataset.fields).map(([name, field]) => `<span class="field-chip">${esc(name)} · ${esc(field.type)}</span>`).join('')}</div>
      <div class="dataset-actions"><a class="button ghost small" href="/api/admin/datasets/${encodeURIComponent(dataset.id)}/export.csv">Export CSV</a></div></article>`).join('') : '<div class="empty-state">No datasets yet. Define a schema above, then push your first records.</div>';
  } catch (error) { showMessage('dataset-message', error.message, true); }
}
$('refresh-datasets').addEventListener('click', loadDatasets);
$('dataset-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  showMessage('dataset-message', '');
  try {
    const fields = JSON.parse($('dataset-fields').value);
    const unique_by = $('dataset-unique').value.split(',').map((value) => value.trim()).filter(Boolean);
    await api('/api/admin/datasets', { method: 'POST', body: JSON.stringify({ id: $('dataset-id').value.trim(), fields, unique_by }) });
    showMessage('dataset-message', 'Dataset created. Its push API key is not shown here.');
    await loadDatasets();
  } catch (error) { showMessage('dataset-message', error.message, true); }
});

$('rotate-key').addEventListener('click', async () => {
  if (!window.confirm('Rotate the dataset push key? Existing integrations will stop working immediately.')) return;
  try {
    const result = await api('/api/admin/credentials/dataset-key/rotate', { method: 'POST' });
    $('new-key').textContent = result.api_key;
    $('key-result').classList.remove('hidden');
  } catch (error) { showMessage('dataset-message', error.message, true); }
});
$('copy-key').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('new-key').textContent); $('copy-key').textContent = 'Copied'; }
  catch (_) { showMessage('dataset-message', 'Copy was blocked by the browser. Select the key and copy it manually.', true); }
});

$('add-widget').addEventListener('click', () => {
  const datasetId = $('widget-dataset').value;
  if (!datasetId) { showMessage('dashboard-message', 'Create a dataset before adding a widget.', true); return; }
  const type = $('widget-type').value;
  const widget = { id: `widget-${state.draftWidgets.length + 1}`, type, title: $('widget-title').value.trim() || type, dataset_id: datasetId, aggregate: $('widget-aggregate').value };
  if (type !== 'table' && widget.aggregate !== 'count' && $('widget-metric').value) widget.metric_field = $('widget-metric').value;
  if (['bar', 'column', 'leaderboard'].includes(type) && $('widget-dimension').value) widget.dimension_field = $('widget-dimension').value;
  if (type === 'line' && $('widget-time').value) widget.time_field = $('widget-time').value;
  if (type === 'line' && !widget.time_field) { showMessage('dashboard-message', 'Line charts need a date or datetime field.', true); return; }
  if (['bar', 'column', 'leaderboard'].includes(type) && !widget.dimension_field) { showMessage('dashboard-message', 'This visualization needs a category field.', true); return; }
  if (type !== 'table' && widget.aggregate !== 'count' && !widget.metric_field) { showMessage('dashboard-message', 'Choose a numeric metric field.', true); return; }
  state.draftWidgets.push(widget);
  $('widget-count').textContent = state.draftWidgets.length;
  $('widget-draft').innerHTML = state.draftWidgets.map((item, index) => `<div class="draft-chip"><span>${esc(index + 1)}. ${esc(item.title)} · ${esc(item.type)} · ${esc(item.dataset_id)}</span><button type="button" data-remove-widget="${index}" aria-label="Remove widget">×</button></div>`).join('');
  $('widget-draft').querySelectorAll('[data-remove-widget]').forEach((button) => button.addEventListener('click', () => {
    state.draftWidgets.splice(Number(button.dataset.removeWidget), 1);
    state.draftWidgets.forEach((item, index) => { item.id = `widget-${index + 1}`; });
    $('widget-count').textContent = state.draftWidgets.length;
    $('widget-draft').innerHTML = state.draftWidgets.map((item, index) => `<div class="draft-chip"><span>${esc(index + 1)}. ${esc(item.title)} · ${esc(item.type)} · ${esc(item.dataset_id)}</span><button type="button" data-remove-widget="${index}">×</button></div>`).join('');
  }));
  $('widget-title').value = '';
  showMessage('dashboard-message', `${state.draftWidgets.length} widget${state.draftWidgets.length === 1 ? '' : 's'} in this dashboard.`);
});

async function loadDashboards() {
  try {
    const result = await api('/api/admin/dashboards');
    state.dashboards = result.items;
    populateDatasetSelect();
    const cards = await Promise.all(state.dashboards.map(async (dashboard) => {
      const preview = await api(`/api/admin/dashboards/${encodeURIComponent(dashboard.id)}/preview`);
      const widgets = preview.widgets.map((widget) => {
        const result = widget.result || {};
        const value = result.value !== undefined ? `<b>${esc(result.value)}</b>` : `<small>${esc((result.items || []).length)} ${widget.type === 'table' ? 'rows' : 'groups'}</small>`;
        return `<div class="preview-widget"><strong>${esc(widget.title)} · ${esc(widget.type)}</strong>${value}</div>`;
      }).join('');
      return `<article class="dashboard-card"><h4>${esc(dashboard.name)}</h4><span class="muted">${dashboard.widgets.length} widgets · ${esc(dashboard.id)}</span><div class="preview-widgets">${widgets || '<small class="muted">No widgets yet</small>'}</div></article>`;
    }));
    $('dashboard-list').innerHTML = cards.length ? cards.join('') : '<div class="empty-state">No dashboards yet. Add widgets and save a dashboard.</div>';
  } catch (error) { showMessage('dashboard-message', error.message, true); }
}
$('dashboard-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!state.draftWidgets.length) { showMessage('dashboard-message', 'Add at least one widget first.', true); return; }
  try {
    await api('/api/admin/dashboards', { method: 'POST', body: JSON.stringify({ name: $('dashboard-name').value.trim(), widgets: state.draftWidgets }) });
    state.draftWidgets = [];
    $('widget-count').textContent = '0';
    $('widget-draft').innerHTML = '';
    $('dashboard-name').value = '';
    showMessage('dashboard-message', 'Dashboard saved.');
    await loadDashboards();
  } catch (error) { showMessage('dashboard-message', error.message, true); }
});

async function loadRotations() {
  try {
    const result = await api('/api/admin/rotations');
    state.rotations = result.items;
    const base = window.location.origin;
    $('rotation-list').innerHTML = state.rotations.length ? state.rotations.map((rotation) => `
      <article class="rotation-card"><div class="rotation-card-head"><strong>${esc(rotation.name)}</strong><span class="status-pill">${rotation.active ? 'ACTIVE' : 'PAUSED'}</span></div>
      <div class="dataset-meta">${rotation.dashboard_ids.length} dashboards · ${rotation.interval_seconds}s per screen</div>
      <a class="public-link" href="/tv/${encodeURIComponent(rotation.public_id)}" target="_blank" rel="noopener">${esc(base)}/tv/${esc(rotation.public_id)}</a></article>`).join('') : '<div class="empty-state">No TV rotations yet. Select dashboards to create a screen loop.</div>';
  } catch (error) { showMessage('rotation-message', error.message, true); }
}
$('rotation-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const dashboard_ids = Array.from($('rotation-dashboards').selectedOptions).map((option) => option.value);
  try {
    await api('/api/admin/rotations', { method: 'POST', body: JSON.stringify({ name: $('rotation-name').value.trim(), dashboard_ids, interval_seconds: Number($('rotation-interval').value), active: true }) });
    $('rotation-name').value = '';
    showMessage('rotation-message', 'TV rotation created. The public link is shown in the list.');
    await loadRotations();
  } catch (error) { showMessage('rotation-message', error.message, true); }
});

loadWorkspace();
