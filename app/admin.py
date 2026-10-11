from fastapi.responses import HTMLResponse


def admin_page() -> HTMLResponse:
    return HTMLResponse("""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Task Queue Admin</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    body { margin:0; background:#f5f7fb; color:#172033; }
    main { max-width:1180px; margin:auto; padding:32px 20px 60px; }
    header { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; }
    h1 { margin:0; font-size:28px; } .muted { color:#68738a; font-size:13px; }
    .cards { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:24px 0; }
    .card,.panel { background:white; border:1px solid #e1e6ef; border-radius:14px; box-shadow:0 2px 8px #1720330a; }
    .card { padding:16px; } .number { font-size:28px; font-weight:700; margin-top:4px; }
    .panel { margin-top:18px; overflow:hidden; } .panel h2 { margin:0; padding:18px 20px; font-size:17px; }
    table { width:100%; border-collapse:collapse; } th,td { text-align:left; padding:12px 20px; border-top:1px solid #edf0f5; font-size:13px; vertical-align:top; }
    th { color:#68738a; font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
    .badge { display:inline-flex; align-items:center; gap:6px; border-radius:999px; padding:4px 9px; font-weight:600; font-size:12px; background:#eef2f7; }
    .dot { width:8px; height:8px; border-radius:50%; background:#98a2b3; }
    .online .dot { background:#16a46c; } .online { color:#087a4d; background:#e8f8f1; }
    .busy { color:#1859a9; background:#eaf2ff; } .offline { color:#9a3d3d; background:#fff0f0; }
    code { font-size:12px; } .empty { padding:28px 20px; color:#68738a; }
    @media(max-width:760px){.cards{grid-template-columns:repeat(2,1fr)} th:nth-child(4),td:nth-child(4){display:none} header{align-items:flex-start;flex-direction:column}}
  </style>
</head>
<body><main>
  <header><div><h1>Task Queue</h1><div class="muted">Workers and recent activity</div></div><div id="updated" class="muted">Loading…</div></header>
  <section class="cards">
    <div class="card"><div class="muted">Workers online</div><div class="number" id="online">–</div></div>
    <div class="card"><div class="muted">Workers busy</div><div class="number" id="busy">–</div></div>
    <div class="card"><div class="muted">Tasks queued</div><div class="number" id="queued">–</div></div>
    <div class="card"><div class="muted">Tasks running</div><div class="number" id="running">–</div></div>
  </section>
  <section class="panel"><h2>Registered workers</h2><div id="workers"></div></section>
  <section class="panel"><h2>Recent tasks</h2><div id="tasks"></div></section>
</main><script>
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago = value => { const s=Math.max(0,Math.floor((Date.now()-new Date(value).getTime())/1000)); return s<60?`${s}s ago`:s<3600?`${Math.floor(s/60)}m ago`:`${Math.floor(s/3600)}h ago`; };
const shortId = value => value ? esc(value.slice(0,8)) : '—';
async function refresh(){
  try {
    const [workers,tasks] = await Promise.all([fetch('/api/workers').then(r=>r.json()),fetch('/api/tasks').then(r=>r.json())]);
    document.querySelector('#online').textContent=workers.filter(w=>w.online).length;
    document.querySelector('#busy').textContent=workers.filter(w=>w.online&&w.state==='busy').length;
    document.querySelector('#queued').textContent=tasks.filter(t=>t.status==='queued').length;
    document.querySelector('#running').textContent=tasks.filter(t=>t.status==='claimed').length;
    document.querySelector('#updated').textContent=`Updated ${new Date().toLocaleTimeString()}`;
    document.querySelector('#workers').innerHTML = workers.length ? `<table><thead><tr><th>Status</th><th>Worker</th><th>State</th><th>Capabilities</th><th>Current task</th><th>Last heartbeat</th></tr></thead><tbody>${workers.map(w=>`<tr><td><span class="badge ${w.online?'online':'offline'}"><span class="dot"></span>${w.online?'Online':'Offline'}</span></td><td><strong>${esc(w.name)}</strong><br><code>${esc(w.id)}</code><br><span class="muted">${esc(w.version||'version unknown')}</span></td><td><span class="badge ${w.state==='busy'?'busy':''}">${esc(w.state)}</span></td><td>${esc((w.capabilities.task_types||[]).join(', ')||'—')}<br><span class="muted">${esc((w.capabilities.profiles||[]).join(', '))}</span></td><td><code>${shortId(w.current_task_id)}</code></td><td>${ago(w.last_heartbeat_at)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">No worker has registered yet.</div>';
    document.querySelector('#tasks').innerHTML = tasks.length ? `<table><thead><tr><th>Status</th><th>Task</th><th>Worker</th><th>Progress</th><th>Created</th></tr></thead><tbody>${tasks.map(t=>`<tr><td><span class="badge">${esc(t.status)}</span></td><td><strong>${esc(t.task_type)}</strong><br><code>${shortId(t.id)}</code></td><td>${esc(t.claimed_by||'—')}</td><td>${esc(t.progress_stage||'—')}${t.progress_message?`<br><span class="muted">${esc(t.progress_message)}</span>`:''}</td><td>${ago(t.created_at)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">No tasks found.</div>';
  } catch (e) { document.querySelector('#updated').textContent='Could not load queue status'; }
}
refresh(); setInterval(refresh,15000);
</script></body></html>""")
