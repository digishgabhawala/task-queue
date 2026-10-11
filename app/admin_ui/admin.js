const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));

const ago = value => {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
};

const shortId = value => value ? esc(value.slice(0, 8)) : '—';

async function refresh() {
  try {
    const [workers, tasks] = await Promise.all([
      fetch('/api/workers').then(response => response.json()),
      fetch('/api/tasks').then(response => response.json()),
    ]);
    document.querySelector('#online').textContent = workers.filter(worker => worker.online).length;
    document.querySelector('#busy').textContent = workers.filter(worker => worker.online && worker.state === 'busy').length;
    document.querySelector('#queued').textContent = tasks.filter(task => task.status === 'queued').length;
    document.querySelector('#running').textContent = tasks.filter(task => task.status === 'claimed').length;
    document.querySelector('#updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
    document.querySelector('#workers').innerHTML = workers.length ? `
      <table>
        <thead><tr><th>Status</th><th>Worker</th><th>State</th><th>Capabilities</th><th>Current task</th><th>Last heartbeat</th></tr></thead>
        <tbody>${workers.map(worker => `<tr><td><span class="badge ${worker.online ? 'online' : 'offline'}"><span class="dot"></span>${worker.online ? 'Online' : 'Offline'}</span></td><td><strong>${esc(worker.name)}</strong><br><code>${esc(worker.id)}</code><br><span class="muted">${esc(worker.version || 'version unknown')}</span></td><td><span class="badge ${worker.state === 'busy' ? 'busy' : ''}">${esc(worker.state)}</span></td><td>${esc((worker.capabilities.task_types || []).join(', ') || '—')}<br><span class="muted">${esc((worker.capabilities.profiles || []).join(', '))}</span></td><td><code>${shortId(worker.current_task_id)}</code></td><td>${ago(worker.last_heartbeat_at)}</td></tr>`).join('')}</tbody>
      </table>` : '<div class="empty">No worker has registered yet.</div>';
    document.querySelector('#tasks').innerHTML = tasks.length ? `
      <table>
        <thead><tr><th>Status</th><th>Task</th><th>Worker</th><th>Progress</th><th>Created</th></tr></thead>
        <tbody>${tasks.map(task => `<tr><td><span class="badge">${esc(task.status)}</span></td><td><strong>${esc(task.task_type)}</strong><br><code>${shortId(task.id)}</code></td><td>${esc(task.claimed_by || '—')}</td><td>${esc(task.progress_stage || '—')}${task.progress_message ? `<br><span class="muted">${esc(task.progress_message)}</span>` : ''}</td><td>${ago(task.created_at)}</td></tr>`).join('')}</tbody>
      </table>` : '<div class="empty">No tasks found.</div>';
  } catch (error) {
    document.querySelector('#updated').textContent = 'Could not load queue status';
  }
}

refresh();
setInterval(refresh, 15000);
