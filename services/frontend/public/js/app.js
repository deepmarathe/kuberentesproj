// ShopFleet SPA: hash router + global widgets (cart drawer, promo bar, account, hood panel).
import { $, $$, api, auth, esc, money, on, session, timeLeft, toast } from './core.js';
import { startLive, restartLive } from './live.js';
import * as shop from './pages/shop.js';
import * as product from './pages/product.js';
import * as orders from './pages/orders.js';
import * as account from './pages/account.js';
import * as admin from './pages/admin.js';
import * as cluster from './pages/cluster.js';

const routes = [
  [/^#?\/?$/, shop, 'shop'],
  [/^#\/product\/(\d+)$/, product, 'shop'],
  [/^#\/orders$/, orders, 'orders'],
  [/^#\/(login|register)$/, account, null],
  [/^#\/admin$/, admin, 'admin'],
  [/^#\/cluster$/, cluster, 'cluster'],
];

let current = null; // active page module (for cleanup)

async function route() {
  const hash = location.hash || '#/';
  for (const [re, page, nav] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    current?.unmount?.();
    current = page;
    $$('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === nav));
    const view = $('#view');
    view.innerHTML = '<div class="loading">Loading…</div>';
    try {
      await page.mount(view, ...m.slice(1));
    } catch (e) {
      view.innerHTML = `<div class="panel error"><h2>Something went wrong</h2><p>${esc(e.message)}</p>
        <p class="muted small">Check the "under the hood" panel below: which service failed, and with what status?</p></div>`;
    }
    window.scrollTo(0, 0);
    return;
  }
  location.hash = '#/';
}

// ---------- account button + admin nav ----------
function renderAccount() {
  const u = auth.user;
  const a = $('#account');
  if (u) {
    a.textContent = `${u.role === 'admin' ? '🛡️ ' : ''}${u.name} · Log out`;
    a.href = '#/';
    a.onclick = (e) => { e.preventDefault(); auth.logout(); toast('Logged out'); location.hash = '#/'; };
  } else {
    a.textContent = 'Log in';
    a.href = '#/login';
    a.onclick = null;
  }
  $$('.admin-only').forEach((el) => { el.hidden = !auth.isAdmin; });
}
on('auth', () => { renderAccount(); restartLive(); refreshCart(); });

// ---------- cart drawer ----------
const drawer = $('#cart');
function openCart() { drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false'); $('#scrim').hidden = false; refreshCart(); }
function closeCart() { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); $('#scrim').hidden = true; }
$('#cartBtn').onclick = openCart;
$('#closeCart').onclick = closeCart;
$('#scrim').onclick = closeCart;
on('cart', (cart) => renderCart(cart));
on('open-cart', openCart);

function renderCart(cart) {
  $('#cartCount').textContent = cart.item_count || 0;
  $('#cartTotal').textContent = money(cart.total_cents);
  $('#checkout').disabled = !cart.items.length;
  $('#cartHint').textContent = auth.user ? `Ordering as ${auth.user.email}` : 'Guest checkout — log in to see orders on any device.';
  $('#cartItems').innerHTML = cart.items.length ? cart.items.map((i) => `
    <div class="line">
      <span class="line-emoji">${esc(i.emoji || '📦')}</span>
      <span class="grow">${esc(i.name)}<br>
        <span class="muted small">${i.qty} × ${money(i.price_cents)}${i.list_price_cents && i.list_price_cents > i.price_cents ? ` <s>${money(i.list_price_cents)}</s>` : ''}</span></span>
      <button class="btn ghost small" data-remove="${i.product_id}" aria-label="Remove ${esc(i.name)}">✕</button>
    </div>`).join('') : '<p class="muted">Your cart is empty.</p>';
}

async function refreshCart() {
  try { renderCart(await api('GET', '/api/cart')); } catch (e) { $('#cartItems').innerHTML = `<p class="st-bad">Cart unavailable: ${esc(e.message)}</p>`; }
}

$('#cartItems').addEventListener('click', async (e) => {
  const id = e.target.dataset?.remove;
  if (!id) return;
  try { renderCart(await api('DELETE', `/api/cart/items/${id}`)); } catch (err) { toast(err.message, 'bad'); }
});

$('#checkout').onclick = async () => {
  const btn = $('#checkout');
  btn.disabled = true;
  btn.textContent = 'Processing payment…';
  try {
    const order = await api('POST', '/api/orders/checkout');
    toast(`Order ${order.id.slice(0, 8)} paid — ${money(order.total_cents)}`, 'good');
    closeCart();
    await refreshCart();
    location.hash = '#/orders';
  } catch (e) {
    const msg = { 402: 'Payment declined by the bank', 409: e.message, 503: 'Payment system unavailable — try again shortly' }[e.status] || e.message;
    toast(`Checkout failed: ${msg}`, 'bad', 5000);
  } finally {
    btn.textContent = 'Checkout';
    btn.disabled = false;
  }
};

// ---------- flash sale bar ----------
let promos = [];
async function loadPromos() {
  try { promos = (await api('GET', '/api/catalog/promotions', undefined, { quiet: true })).items; } catch (_) { promos = []; }
  renderPromos();
}
function renderPromos() {
  const bar = $('#promoBar');
  const active = promos.filter((p) => new Date(p.ends_at) > Date.now());
  bar.hidden = !active.length;
  bar.innerHTML = active.map((p) => `<span>⚡ <strong>${esc(p.title)}</strong> — ${p.percent}% off ${esc(p.category)} · ends in <span class="mono">${timeLeft(p.ends_at)}</span></span>`).join('<span class="sep">|</span>');
}
setInterval(renderPromos, 1000);
setInterval(loadPromos, 20000);

// ---------- search ----------
let searchTimer;
$('#q').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    if (location.hash.startsWith('#/product') || !['', '#/', '#'].includes(location.hash)) location.hash = '#/';
    shop.setQuery(e.target.value.trim());
  }, 250);
});
$('#search').addEventListener('submit', (e) => e.preventDefault());

// ---------- hood panel ----------
$('#session').textContent = `session ${session.slice(0, 8)}…`;
$('#hoodToggle').onclick = () => {
  const hood = $('#hood');
  const collapsed = hood.classList.toggle('collapsed');
  $('#hoodToggle').setAttribute('aria-expanded', String(!collapsed));
};

window.addEventListener('hashchange', route);
renderAccount();
refreshCart();
loadPromos();
startLive();
route();
