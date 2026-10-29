(() => {
  const $ = (selector) => document.querySelector(selector);
  const loginView = $('#login-view');
  const appView = $('#app-view');
  const tokenKey = 'zigpoll_admin_token';
  let selectedSurvey = null;
  const token = () => sessionStorage.getItem(tokenKey) || '';
  const authHeaders = () => ({Authorization: `Bearer ${token()}`});
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, headers: {...authHeaders(), ...(options.headers || {})}});
    if (response.status === 401) { sessionStorage.removeItem(tokenKey); showLogin(); }
    return response;
  }
  function showLogin() { loginView.hidden = false; appView.hidden = true; }
  function showApp() { loginView.hidden = true; appView.hidden = false; refreshSurveys(); }
  function showError(el, text) { el.textContent = text || ''; }
  function addText(parent, tag, text, className) { const node = document.createElement(tag); node.textContent = text ?? ''; if (className) node.className = className; parent.append(node); return node; }

  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    showError($('#login-error'), '');
    try {
      const response = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:form.get('email'), password:form.get('password')})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Sign-in failed');
      sessionStorage.setItem(tokenKey, data.access_token);
      event.currentTarget.reset(); showApp();
    } catch (error) { showError($('#login-error'), error.message); }
  });

  $('#logout').addEventListener('click', () => { sessionStorage.removeItem(tokenKey); showLogin(); });
  $('#refresh').addEventListener('click', refreshSurveys);
  $('#copy-secret').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#secret-value').value); $('#copy-secret').textContent = 'Copied'; }
    catch { $('#secret-value').select(); document.execCommand('copy'); $('#copy-secret').textContent = 'Copied'; }
  });

  $('#create-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    showError($('#create-error'), '');
    const form = new FormData(event.currentTarget);
    let questions;
    try { questions = JSON.parse(form.get('questions')); }
    catch { showError($('#create-error'), 'Questions must be valid JSON.'); return; }
    try {
      const response = await api('/api/admin/surveys', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:form.get('title'),description:form.get('description'),webhook_url:form.get('webhook_url') || null,status:'active',questions})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Survey creation failed');
      $('#secret-value').value = data.webhook_secret;
      $('#secret-notice').hidden = false;
      event.currentTarget.reset();
      await refreshSurveys();
      await openSurvey(data.id);
    } catch (error) { showError($('#create-error'), error.message); }
  });

  async function refreshSurveys() {
    const list = $('#survey-list'); list.replaceChildren();
    try {
      const response = await api('/api/admin/surveys');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not load surveys');
      if (!data.items.length) { addText(list, 'div', 'No surveys yet. Create one to get started.', 'empty'); return; }
      data.items.forEach((survey) => {
        const row = document.createElement('div'); row.className = 'survey-row';
        const info = document.createElement('div');
        addText(info, 'div', survey.title, 'survey-name');
        addText(info, 'div', `${survey.public_id} · ${survey.questions.length} questions`, 'survey-meta');
        const badge = addText(info, 'span', survey.status, `badge ${survey.status}`); badge.style.marginTop = '7px';
        row.append(info);
        const controls = document.createElement('div'); controls.className = 'survey-buttons';
        const view = addText(controls, 'button', 'Insights', 'button'); view.type = 'button'; view.addEventListener('click', () => openSurvey(survey.id));
        const toggle = addText(controls, 'button', survey.status === 'active' ? 'Close' : 'Activate', 'button quiet'); toggle.type = 'button';
        toggle.addEventListener('click', async () => { await api(`/api/admin/surveys/${encodeURIComponent(survey.id)}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:survey.status === 'active' ? 'closed' : 'active'})}); await refreshSurveys(); });
        row.append(controls); list.append(row);
      });
    } catch (error) { addText(list, 'div', error.message, 'empty'); }
  }

  async function openSurvey(id) {
    try {
      const response = await api(`/api/admin/surveys/${encodeURIComponent(id)}/aggregates`);
      const summary = await response.json();
      if (!response.ok) throw new Error(summary.detail || 'Could not load aggregates');
      const surveysResponse = await api('/api/admin/surveys');
      const surveys = await surveysResponse.json();
      selectedSurvey = surveys.items.find((item) => item.id === id);
      if (!selectedSurvey) throw new Error('Survey not found');
      $('#detail-panel').hidden = false;
      $('#detail-title').textContent = selectedSurvey.title;
      $('#csv-link').href = `/api/admin/surveys/${encodeURIComponent(id)}/export.csv`;
      $('#csv-link').onclick = async (event) => { event.preventDefault(); const r = await api(event.currentTarget.href); if (!r.ok) return; const blob = await r.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `zigpoll-${selectedSurvey.public_id}.csv`; a.click(); URL.revokeObjectURL(a.href); };
      $('#embed-link').href = selectedSurvey.embed_url;
      $('#response-count').textContent = `${summary.response_count} total`;
      renderAggregates(summary);
      await renderResponses(id);
      $('#detail-panel').scrollIntoView({behavior:'smooth',block:'start'});
    } catch (error) { showError($('#create-error'), error.message); }
  }

  function renderAggregates(summary) {
    const root = $('#aggregates'); root.replaceChildren();
    const questions = new Map((selectedSurvey?.questions || []).map((q) => [q.id, q]));
    Object.entries(summary.questions).forEach(([qid, aggregate]) => {
      const card = document.createElement('div'); card.className = 'aggregate';
      addText(card, 'h4', questions.get(qid)?.label || qid);
      const counts = aggregate.answers;
      const entries = Object.entries(counts);
      if (!entries.length) addText(card, 'div', 'No answers yet', 'survey-meta');
      entries.forEach(([answer, count]) => {
        const line = document.createElement('div'); line.className = 'agg-line';
        addText(line, 'span', answer); addText(line, 'b', String(count)); card.append(line);
      });
      root.append(card);
    });
  }

  async function renderResponses(id) {
    const root = $('#responses'); root.replaceChildren();
    const response = await api(`/api/admin/surveys/${encodeURIComponent(id)}/responses`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not load responses');
    if (!data.items.length) { addText(root, 'div', 'No responses yet.', 'empty'); return; }
    data.items.forEach((item) => {
      const card = document.createElement('article'); card.className = 'response';
      addText(card, 'small', `${item.respondent_id || 'Anonymous'} · ${new Date(item.submitted_at).toLocaleString()}`);
      const answers = document.createElement('div'); answers.className = 'answer-list';
      Object.entries(item.answers).forEach(([key, value]) => {
        const question = selectedSurvey.questions.find((q) => q.id === key);
        const block = document.createElement('div'); block.className = 'answer-item';
        addText(block, 'b', question?.label || key); addText(block, 'span', Array.isArray(value) ? value.join(', ') : String(value)); answers.append(block);
      });
      card.append(answers); root.append(card);
    });
  }

  if (token()) showApp(); else showLogin();
})();
