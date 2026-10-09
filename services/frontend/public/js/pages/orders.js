// Orders: status tracker, updated live by Server-Sent Events (no polling).
import { $, api, auth, esc, money, on, setTitle } from '../core.js';

const STEPS = ['PENDING', 'PAID', 'FULFILLING', 'SHIPPED'];
let root = null;
on('order-update', () => { if (root) load(); });

export async function mount(el) {
  root = el;
  setTitle('Orders');
  root.innerHTML = `
    <h1>Your orders</h1>
    <p class="muted">${auth.user ? `Signed in as ${esc(auth.user.email)} — orders from every device.` : 'Guest session — <a href="#/login">log in</a> to keep orders across devices.'}
      Statuses update live: the worker publishes events → Redis pub/sub → notifications service → your browser.</p>
    <div id="orderList"></div>`;
  root.addEventListener('click', toggleDetails);
  await load();
}
export function unmount() { root = null; }

async function load() {
  if (!root) return;
  const list = $('#orderList', root);
  try {
    const { items } = await api('GET', '/api/orders');
    list.innerHTML = items.length ? items.map(row).join('') : '<p class="muted">No orders yet. <a href="#/">Go shopping</a>.</p>';
  } catch (e) {
    list.innerHTML = `<div class="panel error">Orders unavailable: ${esc(e.message)}</div>`;
  }
}

function row(o) {
  const failed = o.status === 'FAILED';
  const idx = STEPS.indexOf(o.status);
  const steps = STEPS.map((s, i) => `<li class="${failed ? '' : i <= idx ? 'done' : ''} ${i === idx ? 'current' : ''}">${s.toLowerCase()}</li>`).join('');
  return `<article class="order ${failed ? 'failed' : ''}" data-order="${esc(o.id)}">
    <div class="order-head">
      <div><strong class="mono">#${esc(o.id.slice(0, 8))}</strong> · ${money(o.total_cents)}
        <div class="muted small">${new Date(o.created_at).toLocaleString()}${o.tracking_number ? ` · tracking <span class="mono">${esc(o.tracking_number)}</span>` : ''}</div></div>
      <span class="badge s-${esc(o.status)}">${esc(o.status)}</span>
    </div>
    ${failed ? `<p class="st-bad small">Failed: ${esc(o.failure_reason || 'unknown')}</p>` : `<ol class="tracker">${steps}</ol>`}
    <button class="btn ghost small" data-details="${esc(o.id)}">Details</button>
    <div class="details" hidden></div>
  </article>`;
}

async function toggleDetails(e) {
  const id = e.target.dataset?.details;
  if (!id) return;
  const box = e.target.nextElementSibling;
  if (!box.hidden) { box.hidden = true; return; }
  try {
    const o = await api('GET', `/api/orders/${id}`);
    box.innerHTML = `<table class="table small"><tr><th>Item</th><th>Qty</th><th>Price</th></tr>${o.items.map((i) => `<tr><td>${esc(i.name)}</td><td>${i.qty}</td><td>${money(i.price_cents)}</td></tr>`).join('')}</table>
      <p class="muted small">payment ${esc(o.payment_id || '-')}</p>`;
    box.hidden = false;
  } catch (err) { box.textContent = err.message; box.hidden = false; }
}
