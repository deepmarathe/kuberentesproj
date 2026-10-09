// Live order updates over Server-Sent Events (EventSource reconnects automatically).
import { $, auth, emit, session, toast } from './core.js';

let source = null;
let adminSource = null;

function connect() {
  source?.close();
  const params = new URLSearchParams({ session });
  if (auth.token) params.set('token', auth.token);
  source = new EventSource(`/api/notify/stream?${params}`);
  const live = $('#live');
  source.addEventListener('hello', (e) => {
    const { pod } = JSON.parse(e.data);
    live.textContent = '● live';
    live.className = 'live on';
    live.title = `Live updates via notifications pod ${pod}`;
  });
  source.addEventListener('order', (e) => {
    const evt = JSON.parse(e.data);
    const msgs = { PAID: 'payment received', FULFILLING: 'being packed', SHIPPED: `shipped (${evt.tracking_number || ''})`, FAILED: `failed: ${evt.reason || ''}` };
    toast(`Order ${evt.order_id.slice(0, 8)} ${msgs[evt.status] || evt.status}`, evt.status === 'FAILED' ? 'bad' : 'good');
    emit('order-update', evt);
  });
  // The server asks us to reconnect when its pod is shutting down (graceful drain).
  source.addEventListener('reconnect', () => setTimeout(connect, 200));
  source.onerror = () => { live.textContent = '○ reconnecting'; live.className = 'live'; };
}

export function startLive() { connect(); }
export function restartLive() { connect(); }

// Admin firehose: every order event in the shop.
export function adminFeed(onEvent) {
  adminSource?.close();
  if (!auth.isAdmin) return () => {};
  adminSource = new EventSource(`/api/notify/stream?token=${encodeURIComponent(auth.token)}&scope=all`);
  adminSource.addEventListener('order', (e) => onEvent(JSON.parse(e.data)));
  return () => { adminSource?.close(); adminSource = null; };
}
