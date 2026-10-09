// Notifications: pushes live order updates to browsers with Server-Sent Events (SSE).
//   orders/worker --PUBLISH order-updates--> Redis --SUBSCRIBE--> this service --SSE--> browsers
// Customers receive events for their session/user; admins (JWT role=admin) receive every event.
//
// Kubernetes lessons inside: long-lived connections vs. load-balancer timeouts, draining
// connections on SIGTERM, and readiness that tracks a background subscription.
'use strict';
const http = require('http');
const fs = require('fs');
const os = require('os');
const Redis = require('ioredis');
const jwt = require('jsonwebtoken');
const promClient = require('prom-client');

const SERVICE = 'notifications';
const VERSION = process.env.APP_VERSION || 'dev';
const POD = process.env.POD_NAME || os.hostname();
const PORT = Number(process.env.PORT || 8080);
const CHANNEL = process.env.ORDER_UPDATES_CHANNEL || 'order-updates';
const HEARTBEAT_MS = Number(process.env.HEARTBEAT_MS || 15000);
const MAX_CLIENTS = Number(process.env.MAX_CLIENTS || 2000);

const log = (severity, message, fields = {}) =>
  console.log(JSON.stringify({ severity, message, service: SERVICE, pod: POD, version: VERSION, ...fields }));

promClient.collectDefaultMetrics();
const connected = new promClient.Gauge({ name: 'notifications_connected_clients', help: 'Open SSE streams', labelNames: ['kind'] });
const delivered = new promClient.Counter({ name: 'notifications_events_delivered_total', help: 'Events written to clients' });
const received = new promClient.Counter({ name: 'notifications_events_received_total', help: 'Events received from Redis' });

function jwtKey() {
  const file = process.env.JWT_SECRET_FILE;
  if (file) { try { return fs.readFileSync(file, 'utf8').trim(); } catch (_) { /* fall through */ } }
  return process.env.JWT_SECRET || '';
}

// ---------- Redis subscription ----------
let subscribed = false;
const sub = new Redis({
  host: process.env.REDIS_HOST || 'redis',
  port: Number(process.env.REDIS_PORT || 6379),
  retryStrategy: (times) => Math.min(times * 200, 3000),
});
sub.on('ready', () => sub.subscribe(CHANNEL).then(() => { subscribed = true; log('INFO', 'subscribed', { channel: CHANNEL }); }));
sub.on('end', () => { subscribed = false; });
sub.on('error', (err) => { subscribed = false; log('ERROR', 'redis error', { error: err.message }); });

const clients = new Set(); // { res, sessionId, userId, admin }

sub.on('message', (_channel, raw) => {
  received.inc();
  let evt;
  try { evt = JSON.parse(raw); } catch (_) { return; }
  const payload = `event: order\ndata: ${JSON.stringify(evt)}\n\n`;
  for (const c of clients) {
    if (c.admin || (c.sessionId && c.sessionId === evt.session_id) || (c.userId && c.userId === evt.user_id)) {
      c.res.write(payload);
      delivered.inc();
    }
  }
});

// ---------- HTTP ----------
let shuttingDown = false;

function stream(req, res, url) {
  if (shuttingDown) { res.writeHead(503).end(); return; }
  if (clients.size >= MAX_CLIENTS) { res.writeHead(503, { 'Retry-After': '5' }).end(); return; }
  const sessionId = url.searchParams.get('session') || '';
  let userId = null;
  let admin = false;
  const token = url.searchParams.get('token'); // EventSource can't send headers, so the JWT comes as a query param
  if (token) {
    try {
      const claims = jwt.verify(token, jwtKey(), { algorithms: ['HS256'], issuer: 'shopfleet-auth' });
      userId = Number(claims.sub);
      admin = claims.role === 'admin' && url.searchParams.get('scope') === 'all';
    } catch (_) {
      res.writeHead(401, { 'Content-Type': 'application/json' }).end('{"error":"invalid token"}');
      return;
    }
  }
  if (!sessionId && !userId) { res.writeHead(400).end('{"error":"session or token required"}'); return; }

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no', // tell nginx not to buffer
    'X-Served-By': POD,
    'X-Service': `${SERVICE}@${VERSION}`,
  });
  res.write(`retry: 3000\nevent: hello\ndata: ${JSON.stringify({ pod: POD, admin })}\n\n`);
  const client = { res, sessionId, userId, admin };
  clients.add(client);
  connected.labels(admin ? 'admin' : 'customer').inc();
  const beat = setInterval(() => res.write(`: heartbeat ${Date.now()}\n\n`), HEARTBEAT_MS);
  req.on('close', () => {
    clearInterval(beat);
    clients.delete(client);
    connected.labels(admin ? 'admin' : 'customer').dec();
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/healthz') return res.end('{"status":"ok"}');
  if (url.pathname === '/readyz') {
    const ok = subscribed && !shuttingDown;
    res.writeHead(ok ? 200 : 503, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ status: ok ? 'ready' : 'not ready', subscribed, shuttingDown }));
  }
  if (url.pathname === '/metrics') {
    res.setHeader('Content-Type', promClient.register.contentType);
    return res.end(await promClient.register.metrics());
  }
  if (url.pathname === '/api/notify/stream' && req.method === 'GET') return stream(req, res, url);
  if (url.pathname === '/api/notify/info') {
    res.writeHead(200, { 'Content-Type': 'application/json', 'X-Served-By': POD });
    return res.end(JSON.stringify({ pod: POD, clients: clients.size, subscribed }));
  }
  res.writeHead(404, { 'Content-Type': 'application/json' }).end('{"error":"not found"}');
});
server.keepAliveTimeout = 65000; // longer than typical LB idle timeouts
server.listen(PORT, () => log('INFO', `notifications listening on ${PORT}`));

// ---------- graceful shutdown ----------
// SSE connections never "finish", so server.close() alone would wait forever. Instead: fail readiness,
// tell clients to reconnect (they land on another pod), then end every stream.
process.on('SIGTERM', () => {
  shuttingDown = true;
  log('INFO', 'SIGTERM: draining SSE clients', { clients: clients.size });
  setTimeout(() => {
    for (const c of clients) { c.res.write('event: reconnect\ndata: {}\n\n'); c.res.end(); }
    server.close(() => { sub.quit().finally(() => process.exit(0)); });
    setTimeout(() => process.exit(0), 5000).unref();
  }, Number(process.env.SHUTDOWN_DRAIN_MS || 3000));
});
