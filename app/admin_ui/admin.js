const REFRESH_INTERVAL_MS = 15000;
const API_BASE = document.querySelector('meta[name="task-queue-api-base"]')?.content || '/api/queue';
const apiUrl = path => `${API_BASE.replace(/\/$/, '')}/${path.replace(/^\//, '')}`;

const elements = {
  account: document.querySelector('#account'),
  accountEmail: document.querySelector('#account-email'),
  attention: document.querySelector('#attention'),
  attentionMessage: document.querySelector('#attention-message'),
  authActions: document.querySelector('#auth-actions'),
  authMessage: document.querySelector('#auth-message'),
  authSignOut: document.querySelector('#auth-sign-out'),
  authShell: document.querySelector('#auth-shell'),
  busy: document.querySelector('#busy'),
  busyNote: document.querySelector('#busy-note'),
  connection: document.querySelector('#connection-summary'),
  main: document.querySelector('#main'),
  online: document.querySelector('#online'),
  onlineNote: document.querySelector('#online-note'),
  queued: document.querySelector('#queued'),
  queuedNote: document.querySelector('#queued-note'),
  refresh: document.querySelector('#refresh'),
  running: document.querySelector('#running'),
  runningNote: document.querySelector('#running-note'),
  signOut: document.querySelector('#sign-out'),
  skipLink: document.querySelector('#skip-link'),
  tasks: document.querySelector('#tasks'),
  tasksCount: document.querySelector('#tasks-count'),
  tasksMessage: document.querySelector('#tasks-message'),
  updated: document.querySelector('#updated'),
  workers: document.querySelector('#workers'),
  workersCount: document.querySelector('#workers-count'),
  workersMessage: document.querySelector('#workers-message'),
};

const resources = {
  workers: { data: null, error: null, updatedAt: null },
  tasks: { data: null, error: null, updatedAt: null },
};

let refreshInProgress = false;
let accessToken = null;
let authClient = null;
let dashboardStarted = false;

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}[character]));

const sentenceCase = value => {
  const words = String(value || '').replaceAll('_', ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Unknown';
};

const plural = (count, singular, pluralForm = `${singular}s`) => (
  `${count} ${count === 1 ? singular : pluralForm}`
);

const relativeTime = value => {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return 'Unknown';
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 10) return 'Just now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
};

const clockTime = value => new Intl.DateTimeFormat(undefined, {
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
}).format(value);

const shortId = value => value ? String(value).slice(0, 8) : '—';

const identifier = (value, label) => {
  if (!value) return '<span aria-label="No value">—</span>';
  return `<code class="identifier" title="${escapeHtml(value)}" aria-label="${escapeHtml(label)} ${escapeHtml(value)}" translate="no">${escapeHtml(shortId(value))}</code>`;
};

const statusBadge = (label, kind, includeDot = true) => `
  <span class="status status-${escapeHtml(kind)}">
    ${includeDot ? '<span class="status-dot" aria-hidden="true"></span>' : ''}
    ${escapeHtml(label)}
  </span>`;

const taskStatus = status => ({
  queued: { label: 'Queued', kind: 'queued' },
  claimed: { label: 'Running', kind: 'running' },
  done: { label: 'Completed', kind: 'completed' },
  failed: { label: 'Failed', kind: 'failed' },
}[status] || { label: sentenceCase(status), kind: 'neutral' });

const workerState = worker => {
  if (!worker.online) return { label: 'Offline', kind: 'offline' };
  if (worker.state === 'busy') return { label: 'Busy', kind: 'busy' };
  return { label: 'Available', kind: 'available' };
};

const capabilities = worker => {
  const values = [
    ...(worker.capabilities?.task_types || []),
    ...(worker.capabilities?.profiles || []),
  ];
  if (!values.length) return '<span class="secondary-line">None reported</span>';
  return `<div class="capability-list" translate="no">${values.map(value => `<span class="capability">${escapeHtml(value)}</span>`).join('')}</div>`;
};

const progress = task => {
  const stage = sentenceCase(task.progress_stage || task.status);
  const current = Number(task.progress_current);
  const total = Number(task.progress_total);
  const hasProgress = Number.isFinite(current) && Number.isFinite(total) && total > 0;
  const message = task.status === 'failed'
    ? 'The worker reported a failure.'
    : task.progress_message;

  if (!hasProgress) {
    return `<strong>${escapeHtml(stage)}</strong>${message ? `<span class="progress-message">${escapeHtml(message)}</span>` : ''}`;
  }

  const boundedCurrent = Math.min(Math.max(current, 0), total);
  const percent = Math.round((boundedCurrent / total) * 100);
  return `
    <div class="progress-copy">
      <div class="progress-heading"><strong>${escapeHtml(stage)}</strong><span>${percent}%</span></div>
      <progress value="${boundedCurrent}" max="${total}" aria-label="${escapeHtml(sentenceCase(task.task_type))} progress">${percent}%</progress>
      ${message ? `<span class="progress-message">${escapeHtml(message)}</span>` : ''}
    </div>`;
};

const renderWorkers = workers => {
  elements.workersCount.textContent = plural(workers.length, 'worker');
  if (!workers.length) {
    elements.workers.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h12v6h3v8h-3v6H6v-6H3V8h3V2Zm2 2v16h8V4H8Zm10 6v4h1v-4h-1ZM5 10v4h1v-4H5Zm5-3h4v2h-4V7Zm0 4h4v2h-4v-2Z"/></svg>
        <strong>No workers registered</strong>
        <p>Start a configured worker to register it and begin sending heartbeats.</p>
      </div>`;
    return;
  }

  elements.workers.innerHTML = `
    <div class="table-scroll" tabindex="0" role="region" aria-label="Registered workers table">
      <table>
        <thead><tr><th scope="col">Worker</th><th scope="col">Status</th><th scope="col">Capabilities</th><th scope="col">Current task</th><th scope="col">Last heartbeat</th></tr></thead>
        <tbody>${workers.map(worker => {
    const state = workerState(worker);
    return `
          <tr>
            <td data-label="Worker" data-priority="primary"><strong>${escapeHtml(worker.name)}</strong>${identifier(worker.id, 'Worker ID')}<span class="secondary-line">${escapeHtml(worker.version || 'Version unknown')}</span></td>
            <td data-label="Status">${statusBadge(state.label, state.kind)}</td>
            <td data-label="Capabilities">${capabilities(worker)}</td>
            <td data-label="Current task">${identifier(worker.current_task_id, 'Current task ID')}</td>
            <td data-label="Last heartbeat"><time datetime="${escapeHtml(worker.last_heartbeat_at)}">${escapeHtml(relativeTime(worker.last_heartbeat_at))}</time></td>
          </tr>`;
  }).join('')}</tbody>
      </table>
    </div>`;
};

const renderTasks = tasks => {
  elements.tasksCount.textContent = plural(tasks.length, 'task');
  if (!tasks.length) {
    elements.tasks.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h9l5 5v15H6V2Zm8 2H8v16h10V8h-4V4Zm-4 8h6v2h-6v-2Zm0 4h6v2h-6v-2Z"/></svg>
        <strong>No tasks submitted</strong>
        <p>Recent queue activity will appear here after a producer submits work.</p>
      </div>`;
    return;
  }

  elements.tasks.innerHTML = `
    <div class="table-scroll" tabindex="0" role="region" aria-label="Recent tasks table">
      <table>
        <thead><tr><th scope="col">Task</th><th scope="col">Status</th><th scope="col">Worker</th><th scope="col">Progress</th><th scope="col">Created</th></tr></thead>
        <tbody>${tasks.map(task => {
    const state = taskStatus(task.status);
    return `
          <tr>
            <td data-label="Task" data-priority="primary"><strong>${escapeHtml(sentenceCase(task.task_type))}</strong>${identifier(task.id, 'Task ID')}</td>
            <td data-label="Status">${statusBadge(state.label, state.kind)}</td>
            <td data-label="Worker">${task.claimed_by ? `<span class="technical-value">${escapeHtml(task.claimed_by)}</span>` : '<span class="secondary-line">Waiting for worker</span>'}</td>
            <td data-label="Progress">${progress(task)}</td>
            <td data-label="Created"><time datetime="${escapeHtml(task.created_at)}">${escapeHtml(relativeTime(task.created_at))}</time></td>
          </tr>`;
  }).join('')}</tbody>
      </table>
    </div>`;
};

const setPanelMessage = (element, message, isError = false) => {
  element.hidden = !message;
  element.classList.toggle('is-error', isError);
  element.textContent = message || '';
};

const unavailableState = (container, noun) => {
  container.innerHTML = `
    <div class="empty-state">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 1 21h22L12 2Zm0 4 7.5 13h-15L12 6Zm-1 4v5h2v-5h-2Zm0 7v2h2v-2h-2Z"/></svg>
      <strong>${escapeHtml(sentenceCase(noun))} unavailable</strong>
      <p>The queue did not return ${escapeHtml(noun)}. Use Refresh to try again.</p>
    </div>`;
};

const fetchJson = async endpoint => {
  const headers = { Accept: 'application/json' };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(endpoint, { headers });
  if (!response.ok) throw new Error(`${endpoint} returned ${response.status}`);
  const data = await response.json();
  if (!Array.isArray(data)) throw new Error(`${endpoint} returned an unexpected response`);
  return data;
};

const loadResource = async (name, endpoint, render, container, messageElement) => {
  const resource = resources[name];
  container.setAttribute('aria-busy', 'true');
  try {
    const data = await fetchJson(endpoint);
    resource.data = data;
    resource.error = null;
    resource.updatedAt = new Date();
    render(data);
    setPanelMessage(messageElement, '');
    return true;
  } catch (error) {
    resource.error = error;
    if (resource.data) {
      render(resource.data);
      setPanelMessage(
        messageElement,
        `Could not refresh ${name}. Showing data from ${clockTime(resource.updatedAt)}.`,
      );
    } else {
      unavailableState(container, name);
      setPanelMessage(messageElement, `Could not load ${name}.`, true);
    }
    return false;
  } finally {
    container.setAttribute('aria-busy', 'false');
  }
};

const updateMetrics = () => {
  const workers = resources.workers.data;
  const tasks = resources.tasks.data;

  if (workers) {
    const online = workers.filter(worker => worker.online).length;
    const busy = workers.filter(worker => worker.online && worker.state === 'busy').length;
    const offline = workers.length - online;
    elements.online.textContent = online;
    elements.onlineNote.textContent = offline ? `${plural(offline, 'worker')} offline` : `${workers.length} registered`;
    elements.busy.textContent = busy;
    elements.busyNote.textContent = busy ? `of ${online} online` : 'No active work';
  } else {
    elements.online.textContent = '–';
    elements.busy.textContent = '–';
    elements.onlineNote.textContent = 'Unavailable';
    elements.busyNote.textContent = 'Unavailable';
  }

  if (tasks) {
    const queued = tasks.filter(task => task.status === 'queued').length;
    const running = tasks.filter(task => task.status === 'claimed').length;
    const failed = tasks.filter(task => task.status === 'failed').length;
    elements.queued.textContent = queued;
    elements.queuedNote.textContent = `${tasks.length} recent`;
    elements.running.textContent = running;
    elements.runningNote.textContent = failed ? `${plural(failed, 'task')} failed` : 'No recent failures';
  } else {
    elements.queued.textContent = '–';
    elements.running.textContent = '–';
    elements.queuedNote.textContent = 'Unavailable';
    elements.runningNote.textContent = 'Unavailable';
  }
};

const updateAttention = () => {
  const offline = (resources.workers.data || []).filter(worker => !worker.online).length;
  const failed = (resources.tasks.data || []).filter(task => task.status === 'failed').length;
  const messages = [];
  if (offline) messages.push(`${plural(offline, 'worker')} offline`);
  if (failed) messages.push(`${plural(failed, 'recent task')} failed`);
  elements.attention.hidden = !messages.length;
  elements.attentionMessage.textContent = messages.length
    ? `${messages.join(' and ')}. Review the records below for context.`
    : '';
};

const setConnectionState = (workersOk, tasksOk) => {
  elements.connection.classList.remove('is-live', 'is-warning', 'is-error');
  if (workersOk && tasksOk) {
    elements.connection.classList.add('is-live');
    elements.updated.textContent = `Queue updated ${clockTime(new Date())}`;
  } else if (workersOk || tasksOk) {
    elements.connection.classList.add('is-warning');
    elements.updated.textContent = 'Partial update · showing available data';
  } else {
    elements.connection.classList.add('is-error');
    const hasCachedData = resources.workers.data || resources.tasks.data;
    elements.updated.textContent = hasCachedData
      ? 'Queue unavailable · showing last known data'
      : 'Could not connect to queue';
  }
};

const refresh = async () => {
  if (refreshInProgress || elements.main.hidden) return;
  refreshInProgress = true;
  elements.refresh.disabled = true;
  elements.refresh.classList.add('is-refreshing');
  elements.refresh.querySelector('span').textContent = 'Refreshing';

  const [workersOk, tasksOk] = await Promise.all([
    loadResource('workers', apiUrl('workers'), renderWorkers, elements.workers, elements.workersMessage),
    loadResource('tasks', apiUrl('tasks'), renderTasks, elements.tasks, elements.tasksMessage),
  ]);

  updateMetrics();
  updateAttention();
  setConnectionState(workersOk, tasksOk);
  elements.refresh.disabled = false;
  elements.refresh.classList.remove('is-refreshing');
  elements.refresh.querySelector('span').textContent = 'Refresh';
  refreshInProgress = false;
};

elements.refresh.addEventListener('click', refresh);
// Sign-in lives on the parent site's shared /login page, which forwards a
// signed-in browser straight back to `next`. So only "no session" and a
// rejected session (401) go there; anything else shows a message here, or
// the two pages would bounce the browser between them forever.
const LOGIN_URL = `/login?next=${encodeURIComponent('/queue')}`;
const goToLogin = () => window.location.replace(LOGIN_URL);

const resetDashboard = () => {
  dashboardStarted = false;
  Object.values(resources).forEach(resource => {
    resource.data = null;
    resource.error = null;
    resource.updatedAt = null;
  });
};

const showMessage = (message, { actions = true } = {}) => {
  accessToken = null;
  resetDashboard();
  elements.main.hidden = true;
  elements.skipLink.hidden = true;
  elements.account.hidden = true;
  elements.authShell.hidden = false;
  elements.authMessage.textContent = message;
  elements.authMessage.classList.toggle('is-error', actions);
  elements.authActions.hidden = !actions;
};

const showDashboard = user => {
  elements.authShell.hidden = true;
  elements.main.hidden = false;
  elements.skipLink.hidden = false;
  if (user) {
    elements.accountEmail.textContent = user.email;
    elements.account.hidden = false;
  } else {
    elements.account.hidden = true;
  }
  if (!dashboardStarted) {
    dashboardStarted = true;
    refresh();
  }
};

const verifyAdminSession = async session => {
  if (!session?.access_token) {
    goToLogin();
    return;
  }

  accessToken = session.access_token;
  try {
    const response = await fetch(apiUrl('auth/me'), {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    });
    if (response.status === 403) {
      showMessage('This account does not have Task Queue admin access.');
      return;
    }
    if (response.status === 401) {
      await authClient.auth.signOut({ scope: 'local' });
      goToLogin();
      return;
    }
    if (!response.ok) {
      showMessage('Your access could not be checked right now. Please reload in a moment.');
      return;
    }
    showDashboard(await response.json());
  } catch (error) {
    showMessage('The identity service is unavailable. Please reload in a moment.');
  }
};

const signOutEverywhere = async button => {
  button.disabled = true;
  try {
    await authClient?.auth.signOut();
  } finally {
    window.location.assign('/login');
  }
};

elements.signOut.addEventListener('click', () => signOutEverywhere(elements.signOut));
elements.authSignOut.addEventListener('click', () => signOutEverywhere(elements.authSignOut));

const initializeAuth = async () => {
  try {
    const response = await fetch(apiUrl('auth/config'), { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error('Auth configuration unavailable');
    const config = await response.json();

    if (!config.enabled) {
      showDashboard(null);
      return;
    }
    if (!window.supabase?.createClient) {
      throw new Error('Supabase browser client unavailable');
    }

    authClient = window.supabase.createClient(
      config.supabase_url,
      config.supabase_publishable_key,
      { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false } },
    );
    const { data, error } = await authClient.auth.getSession();
    if (error) throw error;
    await verifyAdminSession(data.session);

    authClient.auth.onAuthStateChange((event, session) => {
      if (event === 'TOKEN_REFRESHED') accessToken = session?.access_token || null;
      if (event === 'SIGNED_OUT') goToLogin();
    });
  } catch (error) {
    showMessage('Sign-in could not be initialized. Please reload in a moment.', { actions: false });
  }
};

initializeAuth();
setInterval(() => {
  if (document.visibilityState === 'visible' && !elements.main.hidden) refresh();
}, REFRESH_INTERVAL_MS);
