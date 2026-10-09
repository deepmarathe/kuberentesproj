// Admin: business stats, live order feed (SSE firehose), product editing, FlashSale custom resources.
import { $, api, auth, esc, money, setTitle, toast } from '../core.js';
import { adminFeed } from '../live.js';

let stopFeed = () => {};
let timer = null;
const CATEGORIES = ['Electronics', 'Home', 'Outdoors', 'Books', 'Grocery', 'Toys'];

export async function mount(root) {
  setTitle('Admin');
  if (!auth.isAdmin) { root.innerHTML = '<div class="panel">Admins only. <a href="#/login">Log in</a> with the admin account.</div>'; return; }
  root.innerHTML = `
    <h1>Admin</h1>
    <div id="stats" class="stats"></div>
    <div class="cols">
      <section class="panel">
        <h2>⚡ Flash sales <span class="muted small">— FlashSale custom resources, reconciled by the operator</span></h2>
        <div id="fsList"></div>
        <form id="fsForm" class="form grid-form">
          <label>Name <input name="name" required pattern="[a-z0-9]([a-z0-9\-]*[a-z0-9])?" placeholder="toy-frenzy"></label>
          <label>Title <input name="title" required maxlength="80" placeholder="Toy Frenzy"></label>
          <label>Category <select name="category">${CATEGORIES.map((c) => `<option>${c}</option>`).join('')}</select></label>
          <label>Discount % <input name="discountPercent" type="number" min="1" max="99" value="20" required></label>
          <label>Starts in (min) <input name="startInMinutes" type="number" min="0" value="0"></label>
          <label>Duration (min) <input name="durationMinutes" type="number" min="1" value="30"></label>
          <button class="btn primary">Create FlashSale</button>
        </form>
        <details class="hint"><summary>Same thing with kubectl</summary><pre id="fsYaml"></pre></details>
        <p class="muted small">Try 95%, or a 70% Grocery sale: the API server rejects it using the CRD's schema and CEL rules.</p>
      </section>
      <section class="panel">
        <h2>Live orders <span class="muted small">— SSE firehose</span></h2>
        <ul id="feed" class="feed"><li class="muted">Waiting for orders… (the load generator creates some every few seconds)</li></ul>
      </section>
    </div>
    <section class="panel">
      <h2>Products</h2>
      <div class="table-wrap"><table class="table" id="products"><thead><tr><th></th><th>Product</th><th>Category</th><th>Price (cents)</th><th>Stock</th><th>Rating</th><th></th></tr></thead><tbody></tbody></table></div>
    </section>`;

  const form = $('#fsForm', root);
  const yaml = () => {
    const f = Object.fromEntries(new FormData(form));
    const ends = new Date(Date.now() + (Number(f.startInMinutes) + Number(f.durationMinutes)) * 60000).toISOString().replace(/\.\d+Z$/, 'Z');
    $('#fsYaml', root).textContent = [
      'kubectl apply -f - <<EOF',
      'apiVersion: shopfleet.io/v1alpha1',
      'kind: FlashSale',
      'metadata:',
      `  name: ${f.name || 'my-sale'}`,
      '  namespace: shopfleet',
      'spec:',
      `  title: "${f.title}"`,
      `  category: ${f.category}`,
      `  discountPercent: ${f.discountPercent}`,
      `  endsAt: "${ends}"`,
      'EOF',
      'kubectl -n shopfleet get flashsales -w',
    ].join('\n');
  };
  form.oninput = yaml;
  yaml();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    try {
      await api('POST', '/api/ops/flashsales', {
        ...f, discountPercent: Number(f.discountPercent), startInMinutes: Number(f.startInMinutes), durationMinutes: Number(f.durationMinutes),
      });
      toast(`FlashSale ${f.name} created — watch the operator pick it up`, 'good');
      loadFlashSales(root);
    } catch (err) { toast(`Rejected: ${err.message}`, 'bad', 7000); }
  };
  $('#fsList', root).onclick = async (e) => {
    const name = e.target.dataset?.del;
    if (!name) return;
    try {
      await api('DELETE', `/api/ops/flashsales/${name}`);
      toast(`Deleting ${name} (the finalizer removes the discount first)`);
      loadFlashSales(root);
    } catch (err) { toast(err.message, 'bad'); }
  };
  $('#products', root).onclick = (e) => onProductAction(e, root);

  const feed = $('#feed', root);
  stopFeed = adminFeed((evt) => {
    if (feed.firstElementChild?.classList.contains('muted')) feed.innerHTML = '';
    const li = document.createElement('li');
    li.innerHTML = `<span class="mono">${new Date(evt.ts * 1000).toLocaleTimeString()}</span> <span class="badge s-${esc(evt.status)}">${esc(evt.status)}</span> #${esc(evt.order_id.slice(0, 8))} ${evt.total_cents ? money(evt.total_cents) : ''} ${evt.reason ? `<span class="st-bad small">${esc(evt.reason)}</span>` : ''}`;
    feed.prepend(li);
    while (feed.children.length > 40) feed.lastChild.remove();
  });

  await Promise.all([loadStats(root), loadFlashSales(root), loadProducts(root)]);
  timer = setInterval(() => { loadStats(root); loadFlashSales(root); }, 5000);
}

export function unmount() { stopFeed(); clearInterval(timer); }

async function loadStats(root) {
  const box = $('#stats', root);
  if (!box) return;
  try {
    const s = await api('GET', '/api/orders/admin/stats?minutes=60', undefined, { quiet: true });
    const by = Object.fromEntries(s.by_status.map((r) => [r.status, r]));
    const okStates = ['PAID', 'FULFILLING', 'SHIPPED'];
    const ok = okStates.reduce((n, k) => n + (by[k]?.orders || 0), 0);
    const revenue = okStates.reduce((n, k) => n + (by[k]?.total_cents || 0), 0);
    const failed = by.FAILED?.orders || 0;
    const rate = ok + failed ? Math.round((ok / (ok + failed)) * 1000) / 10 : 100;
    box.innerHTML = [
      ['Orders (1h)', ok + failed], ['Revenue (1h)', money(revenue)], ['Success rate', `${rate}%`],
      ['Shipped', by.SHIPPED?.orders || 0], ['In fulfilment', (by.PAID?.orders || 0) + (by.FULFILLING?.orders || 0)], ['Customers', s.registered_users],
    ].map(([k, v]) => `<div class="stat"><div class="muted small">${k}</div><div class="stat-value">${v}</div></div>`).join('')
      + (s.failures.length ? `<div class="stat wide"><div class="muted small">Failure reasons</div>${s.failures.map((f) => `<span class="badge bad">${esc(f.failure_reason)} × ${f.orders}</span>`).join(' ')}</div>` : '');
  } catch (e) { box.innerHTML = `<div class="panel error">${esc(e.message)}</div>`; }
}

async function loadFlashSales(root) {
  const box = $('#fsList', root);
  if (!box) return;
  try {
    const { items } = await api('GET', '/api/ops/flashsales', undefined, { quiet: true });
    box.innerHTML = items.length ? `<table class="table"><tr><th>Name</th><th>Sale</th><th>Phase</th><th>Ends</th><th></th></tr>${items.map((f) => `
      <tr><td class="mono">${esc(f.name)}${f.deleting ? ' <span class="badge warn">deleting</span>' : ''}</td>
      <td>${esc(f.discountPercent)}% ${esc(f.category)}<div class="muted small">${esc(f.title || '')}</div></td>
      <td><span class="badge p-${esc(f.phase)}">${esc(f.phase)}</span><div class="muted small">${esc(f.message || '')}</div></td>
      <td class="small">${f.endsAt ? new Date(f.endsAt).toLocaleString() : '-'}</td>
      <td><button class="btn ghost small" data-del="${esc(f.name)}">Delete</button></td></tr>`).join('')}</table>`
      : '<p class="muted">No FlashSales yet.</p>';
  } catch (e) { box.innerHTML = `<p class="st-bad small">${esc(e.message)}</p>`; }
}

async function loadProducts(root) {
  const { items } = await api('GET', '/api/catalog/products?limit=200');
  $('#products tbody', root).innerHTML = items.map((p) => `
    <tr data-id="${p.id}"><td>${esc(p.emoji)}</td><td>${esc(p.name)}</td><td>${esc(p.category)}</td>
    <td><input class="cell" type="number" min="1" data-field="price_cents" value="${p.price_cents}" aria-label="Price in cents"> <span class="muted small">${p.discount_percent ? `sale ${money(p.sale_price_cents)}` : ''}</span></td>
    <td><input class="cell" type="number" min="0" data-field="stock" value="${p.stock}" aria-label="Stock"></td>
    <td>${p.rating ? `${p.rating} ★ (${p.review_count})` : '-'}</td>
    <td><button class="btn ghost small" data-save="${p.id}">Save</button></td></tr>`).join('');
}

async function onProductAction(e, root) {
  const id = e.target.dataset?.save;
  if (!id) return;
  const row = e.target.closest('tr');
  const body = {};
  row.querySelectorAll('input[data-field]').forEach((i) => { body[i.dataset.field] = Number(i.value); });
  try {
    await api('PATCH', `/api/catalog/admin/products/${id}`, body);
    toast('Saved (catalog cache invalidated)', 'good');
    loadProducts(root);
  } catch (err) { toast(err.message, 'bad'); }
}
