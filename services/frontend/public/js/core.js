// Shared state + helpers: API client (records every call for the "under the hood" panel),
// session/auth handling, formatting, toasts.
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (_) { /* blocked */ } },
};

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const money = (cents) => `$${(Number(cents || 0) / 100).toFixed(2)}`;
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---------- session (guest) + auth (JWT) ----------
function newId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return Date.now().toString(16) + Math.random().toString(16).slice(2, 14);
}
export const session = store.get('shopfleet.session') || newId();
store.set('shopfleet.session', session);

export const auth = {
  token: store.get('shopfleet.token'),
  get user() {
    if (!this.token) return null;
    try {
      const p = JSON.parse(atob(this.token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
      if (p.exp * 1000 < Date.now()) { this.logout(); return null; }
      return { id: Number(p.sub), name: p.name, email: p.email, role: p.role };
    } catch (_) { return null; }
  },
  get isAdmin() { return this.user?.role === 'admin'; },
  login(token) { this.token = token; store.set('shopfleet.token', token); emit('auth'); },
  logout() { this.token = null; store.set('shopfleet.token', null); emit('auth'); },
};

// ---------- tiny event bus ----------
const listeners = {};
export const on = (evt, fn) => { (listeners[evt] ||= []).push(fn); };
export const emit = (evt, data) => (listeners[evt] || []).forEach((fn) => fn(data));

// ---------- API ----------
export class ApiError extends Error {
  constructor(status, message, body) { super(message); this.status = status; this.body = body; }
}

export async function api(method, path, body, { quiet = false } = {}) {
  const started = performance.now();
  const headers = { 'X-Session-Id': session };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch (err) {
    hood(method, path, 'network', performance.now() - started);
    throw new ApiError(0, 'Network error — is the cluster reachable?');
  }
  if (!quiet) hood(method, path, res.status, performance.now() - started, res.headers);
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (res.status === 401 && auth.token) auth.logout();
  if (!res.ok) {
    const detail = data && (data.error || data.detail);
    const msg = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

// ---------- "under the hood" panel ----------
function hood(method, path, status, ms, headers) {
  const tr = document.createElement('tr');
  const cls = status === 'network' || status >= 500 ? 'st-bad' : status >= 400 ? 'st-warn' : 'st-ok';
  const h = (k) => (headers && headers.get(k)) || '-';
  tr.innerHTML = `<td>${new Date().toLocaleTimeString()}</td><td>${esc(method)} ${esc(path)}</td>`
    + `<td class="${cls}">${esc(status)}</td><td>${Math.round(ms)}</td><td>${esc(h('X-Service'))}</td>`
    + `<td>${esc(h('X-Served-By'))}</td><td>${esc(headers?.get('X-Cache') || '')}</td>`;
  const tbody = $('#hoodRows');
  tbody.prepend(tr);
  while (tbody.children.length > 80) tbody.lastChild.remove();
}

// ---------- UI helpers ----------
export function toast(message, kind = 'info', ms = 3200) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  $('#toasts').appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, ms);
}

export function stars(rating, count) {
  const full = Math.round(Number(rating) || 0);
  const s = '★★★★★'.slice(0, full) + '☆☆☆☆☆'.slice(0, 5 - full);
  return `<span class="stars" aria-label="${rating} out of 5">${s}</span>${count !== undefined ? ` <span class="muted small">(${count})</span>` : ''}`;
}

export function priceHtml(p) {
  if (p.sale_price_cents != null && p.sale_price_cents < p.price_cents) {
    return `<span class="price sale">${money(p.sale_price_cents)}</span> <s class="muted small">${money(p.price_cents)}</s> <span class="badge sale">-${p.discount_percent}%</span>`;
  }
  return `<span class="price">${money(p.price_cents)}</span>`;
}

export function timeLeft(iso) {
  const s = Math.max(0, Math.floor((new Date(iso) - Date.now()) / 1000));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (d > 0) return `${d}d ${h}h`;
  return `${h ? `${h}h ` : ''}${String(m).padStart(2, '0')}m ${String(sec).padStart(2, '0')}s`;
}

export const ago = (secs) => {
  if (secs == null) return '-';
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
};

export function setTitle(t) { document.title = t ? `${t} · ShopFleet` : 'ShopFleet'; }
