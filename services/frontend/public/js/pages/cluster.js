// Cluster: a live picture of the namespace, built from the Kubernetes API by ops-api.
// Every panel names the kubectl command that shows the same thing -- learn both.
import { $, ago, api, auth, esc, setTitle, toast } from '../core.js';

let timer = null;
let paused = false;
const COLORS = ['#4f7cff', '#13a37f', '#d9822b', '#b453d6', '#d6455d', '#2aa1c4', '#8a9a1d', '#c46b2a', '#5b6bd6', '#2f9e5c', '#a35dba', '#c2410c', '#0e7490', '#7c3aed'];
const colorFor = (app) => COLORS[[...String(app)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % COLORS.length];

export async function mount(root) {
  setTitle('Cluster');
  if (!auth.isAdmin) { root.innerHTML = '<div class="panel">Admins only. <a href="#/login">Log in</a> with the admin account.</div>'; return; }
  root.innerHTML = `
    <div class="page-head">
      <h1>Cluster</h1>
      <div class="muted small" id="meta"></div>
      <label class="small"><input type="checkbox" id="pause"> pause auto-refresh</label>
    </div>
    <p class="muted small">Run <code>./scripts/chaos.sh</code> or an exam task in another terminal and watch it happen here.</p>
    <section class="panel"><h2>Nodes &amp; pods <code class="cmd">kubectl get pods -o wide</code></h2><div id="nodes" class="nodes"></div><div id="legend" class="legend"></div></section>
    <div class="cols">
      <section class="panel"><h2>Workloads <code class="cmd">kubectl get deploy,sts,ds</code></h2><div id="workloads"></div></section>
      <section class="panel"><h2>Autoscalers <code class="cmd">kubectl get hpa</code></h2><div id="hpas"></div>
        <h2>CronJobs <code class="cmd">kubectl get cj</code></h2><div id="cronjobs"></div></section>
    </div>
    <section class="panel"><h2>Warning events <code class="cmd">kubectl get events --field-selector type=Warning</code></h2><div id="events"></div></section>
    <section class="panel"><h2>Who is this dashboard? <code class="cmd">kubectl auth can-i --list --as=system:serviceaccount:shopfleet:ops-api</code></h2><div id="whoami"></div></section>`;
  $('#pause', root).onchange = (e) => { paused = e.target.checked; };
  $('#workloads', root).onclick = (e) => onAction(e, root);
  await Promise.all([refresh(root), loadWhoami(root)]);
  timer = setInterval(() => { if (!paused) refresh(root); }, 3000);
}

export function unmount() { clearInterval(timer); }

async function refresh(root) {
  if (!$('#nodes', root)) return;
  let ov;
  let agents = { agents: [] };
  try {
    [ov, agents] = await Promise.all([
      api('GET', '/api/ops/overview', undefined, { quiet: true }),
      api('GET', '/api/ops/agents', undefined, { quiet: true }).catch(() => ({ agents: [] })),
    ]);
  } catch (e) {
    $('#nodes', root).innerHTML = `<div class="panel error">ops-api: ${esc(e.message)}<br><span class="muted small">Check its readiness and RBAC: <code>kubectl -n shopfleet describe pod -l app=ops-api</code></span></div>`;
    return;
  }
  $('#meta', root).textContent = `namespace ${ov.namespace} · served by ${ov.served_by} · ${new Date(ov.generated_at).toLocaleTimeString()}`;
  const agentBy = Object.fromEntries((agents.agents || []).filter((a) => a.node).map((a) => [a.node, a]));
  const byNode = {};
  ov.pods.forEach((p) => { (byNode[p.node || '(unscheduled)'] ||= []).push(p); });
  const nodes = ov.nodes.length ? ov.nodes.map((n) => n.name) : Object.keys(byNode);
  if (byNode['(unscheduled)'] && !nodes.includes('(unscheduled)')) nodes.push('(unscheduled)');

  const nodeInfo = Object.fromEntries(ov.nodes.map((n) => [n.name, n]));
  $('#nodes', root).innerHTML = (ov.errors.nodes ? `<p class="st-bad small">Nodes hidden — ${esc(ov.errors.nodes)}</p>` : '') + nodes.map((name) => {
    const n = nodeInfo[name];
    const a = agentBy[name];
    const memPct = a && a.mem_total_mb ? Math.round(100 * (1 - a.mem_available_mb / a.mem_total_mb)) : null;
    const pods = (byNode[name] || []).map(podChip).join('');
    return `<div class="node ${n && !n.ready ? 'notready' : ''} ${n?.unschedulable ? 'cordoned' : ''}">
      <div class="node-head">
        <strong>${esc(name)}</strong>
        ${n ? `<span class="muted small">${esc(n.roles.join(','))} · ${esc(n.kubelet || '')}</span>` : ''}
        ${n && !n.ready ? '<span class="badge bad">NotReady</span>' : ''}
        ${n?.unschedulable ? '<span class="badge warn">cordoned</span>' : ''}
        ${(n?.pressure || []).map((p) => `<span class="badge bad">${esc(p)}</span>`).join('')}
      </div>
      ${n?.taints?.length ? `<div class="taints">${n.taints.map((t) => `<code>${esc(t)}</code>`).join(' ')}</div>` : ''}
      ${a ? `<div class="meter" title="node-agent: load ${a.load1} / ${a.cpus} cpus"><span>load ${a.load1}</span><i style="width:${Math.min(100, (a.load1 / a.cpus) * 100)}%"></i></div>
             <div class="meter" title="memory used"><span>mem ${memPct}%</span><i style="width:${memPct}%"></i></div>` : '<div class="muted small">no node-agent data</div>'}
      <div class="pods">${pods || '<span class="muted small">no shopfleet pods</span>'}</div>
    </div>`;
  }).join('');
  const apps = [...new Set(ov.pods.map((p) => p.app))].sort();
  $('#legend', root).innerHTML = apps.map((a) => `<span><i style="background:${colorFor(a)}"></i>${esc(a)}</span>`).join('');

  const wl = (kind, rows, actions) => rows.map((d) => {
    const healthy = d.ready >= (d.desired ?? 0);
    const stuck = d.conditions.find((c) => c.reason === 'ProgressDeadlineExceeded');
    return `<tr><td><span class="muted small">${kind}</span> ${esc(d.name)}</td>
      <td class="${healthy ? 'st-ok' : 'st-warn'}">${d.ready}/${d.desired ?? '-'}</td><td class="small">${d.updated} updated</td>
      <td class="mono small">${esc(d.image || '')}</td>
      <td>${stuck ? '<span class="badge bad">rollout stuck</span>' : ''}${actions ? `
        <button class="btn ghost small" data-scale="${esc(d.name)}" data-to="${Math.max(0, (d.desired || 0) - 1)}" title="kubectl scale --replicas">−</button>
        <button class="btn ghost small" data-scale="${esc(d.name)}" data-to="${(d.desired || 0) + 1}">+</button>
        <button class="btn ghost small" data-restart="${esc(d.name)}" title="kubectl rollout restart">restart</button>` : ''}</td></tr>`;
  }).join('');
  $('#workloads', root).innerHTML = `<table class="table"><tr><th>Name</th><th>Ready</th><th>Rollout</th><th>Image</th><th></th></tr>
    ${wl('deploy', ov.deployments, true)}${wl('sts', ov.statefulsets, false)}${wl('ds', ov.daemonsets, false)}</table>`;

  $('#hpas', root).innerHTML = ov.hpas.length ? `<table class="table"><tr><th>HPA</th><th>Replicas</th><th>CPU</th><th>Range</th></tr>${ov.hpas.map((h) => `
    <tr><td>${esc(h.name)}</td><td>${h.current ?? '-'} → ${h.desired ?? '-'}</td>
    <td>${h.cpu_utilization == null ? '<span class="st-warn" title="metrics-server missing or pods just started">&lt;unknown&gt;</span>' : `${h.cpu_utilization}%`} / ${h.cpu_target}%</td>
    <td>${h.min}–${h.max}</td></tr>`).join('')}</table>` : '<p class="muted">none</p>';

  $('#cronjobs', root).innerHTML = `<table class="table"><tr><th>CronJob</th><th>Schedule</th><th>Last run</th></tr>${ov.cronjobs.map((c) => `
    <tr><td>${esc(c.name)} ${c.suspend ? '<span class="badge warn">suspended</span>' : ''}</td><td class="mono small">${esc(c.schedule)}</td>
    <td class="small">${c.last_schedule ? new Date(c.last_schedule).toLocaleTimeString() : '-'}</td></tr>`).join('')}</table>`;

  $('#events', root).innerHTML = ov.events.length ? `<table class="table"><tr><th>Reason</th><th>Object</th><th>Message</th><th>×</th></tr>${ov.events.map((e) => `
    <tr><td class="st-warn">${esc(e.reason)}</td><td class="mono small">${esc(e.object)}</td><td class="small">${esc(e.message)}</td><td>${e.count}</td></tr>`).join('')}</table>`
    : '<p class="muted">No warnings. Break something!</p>';
}

function podChip(p) {
  const bad = p.phase !== 'Running' && p.phase !== 'Succeeded';
  const notReady = p.phase === 'Running' && p.ready < p.containers;
  const cls = p.reason === 'Terminating' ? 'term' : bad ? 'bad' : notReady ? 'warn' : p.phase === 'Succeeded' ? 'done' : 'ok';
  const title = `${p.name}\nphase: ${p.phase}  ready: ${p.ready}/${p.containers}\nrestarts: ${p.restarts}  qos: ${p.qos}\nip: ${p.ip}  age: ${ago(p.age_seconds)}${p.reason ? `\nreason: ${p.reason}` : ''}`;
  return `<span class="pod ${cls}" style="--c:${colorFor(p.app)}" title="${esc(title)}">${esc(p.app)}${p.restarts ? `<b>↻${p.restarts}</b>` : ''}${p.reason ? `<em>${esc(p.reason)}</em>` : ''}</span>`;
}

async function onAction(e, root) {
  const { scale, to, restart } = e.target.dataset || {};
  try {
    if (scale) { await api('PATCH', `/api/ops/deployments/${scale}/scale`, { replicas: Number(to) }); toast(`Scaled ${scale} to ${to} (an HPA may change it back!)`); }
    if (restart) { await api('POST', `/api/ops/deployments/${restart}/restart`); toast(`Rolling restart of ${restart} started`); }
    refresh(root);
  } catch (err) { toast(err.message, 'bad', 6000); }
}

async function loadWhoami(root) {
  try {
    const w = await api('GET', '/api/ops/whoami', undefined, { quiet: true });
    $('#whoami', root).innerHTML = `<p>Kubernetes identity: <code>${esc(w.user.username)}</code> · can list nodes (ClusterRole): <strong class="${w.can_list_nodes ? 'st-ok' : 'st-bad'}">${w.can_list_nodes}</strong></p>
      <table class="table small"><tr><th>Verbs</th><th>API group</th><th>Resources</th></tr>${w.rules.map((r) => `<tr><td class="mono">${esc((r.verbs || []).join(', '))}</td><td class="mono">${esc((r.apiGroups || []).map((g) => g || 'core').join(', '))}</td><td class="mono">${esc((r.resources || []).join(', '))}</td></tr>`).join('')}</table>
      <p class="muted small">These rules come from <code>k8s/base/ops-api.yaml</code>. Delete the ClusterRoleBinding and watch the node map disappear with a 403.</p>`;
  } catch (e) { $('#whoami', root).innerHTML = `<p class="st-bad">${esc(e.message)}</p>`; }
}
