// Cart service: per-session carts stored as Redis hashes.
// Calls catalog to validate products/prices (service-to-service over cluster DNS).
'use strict';
const express = require('express');
const Redis = require('ioredis');
const promClient = require('prom-client');
const os = require('os');

const SERVICE = 'cart';
const VERSION = process.env.APP_VERSION || 'dev';
const POD = process.env.POD_NAME || os.hostname();
const PORT = Number(process.env.PORT || 8080);
const CATALOG_URL = process.env.CATALOG_URL || 'http://catalog:8080';
const CART_TTL_SECONDS = Number(process.env.CART_TTL_SECONDS || 86400);
const SHUTDOWN_DRAIN_MS = Number(process.env.SHUTDOWN_DRAIN_MS || 5000);

// ---------- logging: one JSON object per line, Cloud Logging understands `severity` ----------
function log(severity, message, fields = {}) {
  console.log(JSON.stringify({ severity, message, service: SERVICE, pod: POD, version: VERSION, ...fields }));
}

// ---------- metrics ----------
promClient.collectDefaultMetrics();
const httpDuration = new promClient.Histogram({
  name: 'http_request_duration_seconds',
  help: 'HTTP latency',
  labelNames: ['service', 'method', 'route', 'status'],
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
});
const catalogCalls = new promClient.Counter({
  name: 'cart_catalog_calls_total',
  help: 'Calls from cart to catalog',
  labelNames: ['outcome'],
});

// ---------- redis ----------
const redis = new Redis({
  host: process.env.REDIS_HOST || 'redis',
  port: Number(process.env.REDIS_PORT || 6379),
  maxRetriesPerRequest: 1,
  enableOfflineQueue: false, // fail fast instead of queueing forever when Redis is down
  retryStrategy: (times) => Math.min(times * 200, 2000),
});
redis.on('error', (err) => log('ERROR', 'redis error', { error: err.message }));
redis.on('ready', () => log('INFO', 'redis connected'));

let shuttingDown = false;
const app = express();
app.use(express.json({ limit: '64kb' }));

app.use((req, res, next) => {
  const start = process.hrtime.bigint();
  res.set('X-Served-By', POD);
  res.set('X-Service', `${SERVICE}@${VERSION}`);
  res.on('finish', () => {
    const route = req.route ? req.baseUrl + req.route.path : 'unmatched';
    if (['/metrics', '/healthz', '/readyz'].includes(route)) return;
    const seconds = Number(process.hrtime.bigint() - start) / 1e9;
    httpDuration.labels(SERVICE, req.method, route, String(res.statusCode)).observe(seconds);
    if (res.statusCode >= 500 || seconds > 1) {
      log('WARNING', 'request', {
        httpRequest: { requestMethod: req.method, requestUrl: req.originalUrl, status: res.statusCode, latency: `${seconds.toFixed(3)}s` },
      });
    }
  });
  next();
});

const key = (sid) => `cart:${sid}`;

function sessionId(req, res) {
  const sid = req.get('X-Session-Id');
  if (!sid || !/^[A-Za-z0-9-]{8,64}$/.test(sid)) {
    res.status(400).json({ error: 'missing or invalid X-Session-Id header' });
    return null;
  }
  return sid;
}

async function readCart(sid) {
  const raw = await redis.hgetall(key(sid));
  const items = Object.entries(raw).map(([productId, v]) => ({ product_id: Number(productId), ...JSON.parse(v) }));
  items.sort((a, b) => a.product_id - b.product_id);
  const total_cents = items.reduce((sum, i) => sum + i.qty * i.price_cents, 0);
  return { items, total_cents, item_count: items.reduce((s, i) => s + i.qty, 0) };
}

async function fetchProduct(productId) {
  try {
    const r = await fetch(`${CATALOG_URL}/api/catalog/products/${productId}`, { signal: AbortSignal.timeout(2000) });
    catalogCalls.labels(r.ok ? 'ok' : `http_${r.status}`).inc();
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`catalog returned ${r.status}`);
    return await r.json();
  } catch (err) {
    if (err.name === 'TimeoutError') catalogCalls.labels('timeout').inc();
    throw err;
  }
}

// wrap async handlers so rejections become 5xx instead of crashing the process
const h = (fn) => (req, res, next) => fn(req, res, next).catch(next);

// ---------- public API ----------
app.get('/api/cart', h(async (req, res) => {
  const sid = sessionId(req, res); if (!sid) return;
  res.json(await readCart(sid));
}));

app.post('/api/cart/items', h(async (req, res) => {
  const sid = sessionId(req, res); if (!sid) return;
  const productId = Number(req.body.product_id);
  const qty = Number(req.body.qty || 1);
  if (!Number.isInteger(productId) || !Number.isInteger(qty) || qty < 1 || qty > 20) {
    return res.status(400).json({ error: 'product_id and qty (1-20) required' });
  }
  let product;
  try {
    product = await fetchProduct(productId);
  } catch (err) {
    log('ERROR', 'catalog unavailable', { error: err.message });
    return res.status(503).json({ error: 'catalog unavailable, try again' });
  }
  if (!product) return res.status(404).json({ error: 'product not found' });

  const existing = await redis.hget(key(sid), String(productId));
  const newQty = Math.min((existing ? JSON.parse(existing).qty : 0) + qty, 20);
  await redis.multi()
    .hset(key(sid), String(productId), JSON.stringify({
      qty: newQty,
      name: product.name,
      // Flash-sale price is locked in when the item is added (like most real shops).
      price_cents: product.sale_price_cents ?? product.price_cents,
      list_price_cents: product.price_cents,
      emoji: product.emoji,
    }))
    .expire(key(sid), CART_TTL_SECONDS)
    .exec();
  res.status(201).json(await readCart(sid));
}));

app.delete('/api/cart/items/:productId', h(async (req, res) => {
  const sid = sessionId(req, res); if (!sid) return;
  await redis.hdel(key(sid), String(req.params.productId));
  res.json(await readCart(sid));
}));

app.delete('/api/cart', h(async (req, res) => {
  const sid = sessionId(req, res); if (!sid) return;
  await redis.del(key(sid));
  res.status(204).end();
}));

// ---------- internal API (used by orders service; not routed by Ingress) ----------
app.get('/internal/cart/:sid', h(async (req, res) => {
  res.json(await readCart(req.params.sid));
}));

app.delete('/internal/cart/:sid', h(async (req, res) => {
  await redis.del(key(req.params.sid));
  res.status(204).end();
}));

// ---------- health + metrics ----------
app.get('/healthz', (req, res) => res.json({ status: 'ok' }));

app.get('/readyz', h(async (req, res) => {
  // Cart cannot work at all without Redis, so Redis is part of readiness.
  // Trade-off discussed in docs/labs/06-dependency-failure.md.
  if (shuttingDown) return res.status(503).json({ status: 'shutting down' });
  try {
    await redis.ping();
    res.json({ status: 'ready' });
  } catch (err) {
    res.status(503).json({ status: 'redis unavailable', error: err.message });
  }
}));

app.get('/metrics', h(async (req, res) => {
  res.set('Content-Type', promClient.register.contentType);
  res.end(await promClient.register.metrics());
}));

app.use((err, req, res, next) => {
  log('ERROR', 'unhandled error', { error: err.message, path: req.originalUrl });
  if (!res.headersSent) res.status(500).json({ error: 'internal error' });
});

const server = app.listen(PORT, () => log('INFO', `cart listening on ${PORT}`));

// ---------- graceful shutdown ----------
// Kubernetes sends SIGTERM, then waits terminationGracePeriodSeconds before SIGKILL.
// We fail readiness first, keep serving in-flight requests while endpoints update, then exit.
process.on('SIGTERM', () => {
  log('INFO', 'SIGTERM received, draining');
  shuttingDown = true;
  setTimeout(() => {
    server.close(() => {
      redis.quit().finally(() => process.exit(0));
    });
  }, SHUTDOWN_DRAIN_MS);
});
