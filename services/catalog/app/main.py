"""Catalog service: product listing, search, and inventory reservations.

Owns the `products` table. Uses Redis as a cache-aside layer (the service keeps
working if Redis is down -- that's deliberate, see docs/labs/06-dependency-failure.md).
Exposes chaos endpoints under /admin/chaos/* that are never routed by the Ingress.
"""
import asyncio
import json
import logging
import os
import socket
import threading
import time

import jwt
import redis
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

SERVICE = "catalog"
VERSION = os.getenv("APP_VERSION", "dev")
POD = os.getenv("POD_NAME", socket.gethostname())
CHAOS_ENABLED = os.getenv("CHAOS_ENABLED", "false").lower() == "true"
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "30"))
SLOW_REQUEST_MS = int(os.getenv("SLOW_REQUEST_MS", "1000"))


# ---------- logging (JSON lines -> Cloud Logging parses severity/message) ----------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "service": SERVICE,
            "pod": POD,
            "version": VERSION,
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[_handler], force=True)
log = logging.getLogger(SERVICE)

# ---------- metrics ----------
REQS = Counter("http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
LAT = Histogram("http_request_duration_seconds", "HTTP latency", ["service", "method", "route"],
                buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
CACHE = Counter("catalog_cache_requests_total", "Cache lookups", ["result"])
RESERVE = Counter("catalog_reservations_total", "Inventory reservations", ["result"])
LEAKED = Gauge("catalog_chaos_leaked_bytes", "Bytes intentionally leaked by chaos endpoint")

# ---------- dependencies ----------
DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=3"
)
pool = ConnectionPool(DSN, min_size=1, max_size=int(os.getenv("DB_POOL_SIZE", "10")),
                      kwargs={"row_factory": dict_row}, open=False)
cache = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=int(os.getenv("REDIS_PORT", "6379")),
                    socket_timeout=0.5, socket_connect_timeout=0.5, decode_responses=True)

# ---------- auth: verify JWTs issued by the auth service (shared key from a Secret file) ----------
def _jwt_key() -> str:
    path = os.getenv("JWT_SECRET_FILE", "")
    if path:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            pass
    return os.getenv("JWT_SECRET", "")


def current_user(authorization: str | None, required: bool = True):
    if not authorization or not authorization.startswith("Bearer "):
        if required:
            raise HTTPException(401, "login required")
        return None
    try:
        claims = jwt.decode(authorization[7:], _jwt_key(), algorithms=["HS256"], issuer="shopfleet-auth")
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")
    return {"id": int(claims["sub"]), "name": claims.get("name", ""), "role": claims.get("role", "customer")}


# ---------- chaos state ----------
chaos = {"ready": True, "latency_ms": 0, "leak": [], "leak_rate_mb_per_min": 0}

app = FastAPI(title="catalog")


@app.on_event("startup")
def startup():
    # open(wait=False): the pod starts even if Postgres isn't up yet; readiness gates traffic.
    pool.open(wait=False)
    threading.Thread(target=_leak_loop, daemon=True).start()
    log.info("catalog started", extra={"fields": {"chaos_enabled": CHAOS_ENABLED}})


@app.on_event("shutdown")
def shutdown():
    log.info("catalog shutting down (SIGTERM received)")
    pool.close()


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    if chaos["latency_ms"] and request.url.path.startswith("/api/"):
        await asyncio.sleep(chaos["latency_ms"] / 1000)
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        log.exception("unhandled error", extra={"fields": {"path": request.url.path}})
        response = JSONResponse({"error": "internal error"}, status_code=500)
        status = 500
    elapsed = time.perf_counter() - start
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if route not in ("/metrics", "/healthz", "/readyz"):
        REQS.labels(SERVICE, request.method, route, str(status)).inc()
        LAT.labels(SERVICE, request.method, route).observe(elapsed)
    if status >= 500 or elapsed * 1000 > SLOW_REQUEST_MS:
        log.warning("request", extra={"fields": {
            "httpRequest": {"requestMethod": request.method, "requestUrl": str(request.url.path),
                            "status": status, "latency": f"{elapsed:.3f}s"}}})
    response.headers["X-Served-By"] = POD
    response.headers["X-Service"] = f"{SERVICE}@{VERSION}"
    return response


# ---------- health ----------
@app.get("/healthz")
def healthz():
    # Liveness: "is the process alive?" Never check dependencies here, or a DB blip
    # makes Kubernetes restart every pod at once.
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    # Readiness: "should I get traffic?" Checks the DB, which catalog can't work without.
    if not chaos["ready"]:
        return JSONResponse({"status": "not ready (chaos)"}, status_code=503)
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1 FROM products LIMIT 1")
    except Exception as e:
        return JSONResponse({"status": "db unavailable", "error": str(e)[:200]}, status_code=503)
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------- cache helpers ----------
def _cache_version() -> str:
    try:
        return cache.get("products:ver") or "0"
    except redis.RedisError:
        return "nocache"


def _cache_get(key):
    try:
        val = cache.get(key)
        CACHE.labels("hit" if val else "miss").inc()
        return val
    except redis.RedisError:
        CACHE.labels("error").inc()
        return None


def _cache_set(key, value):
    try:
        cache.setex(key, CACHE_TTL, value)
    except redis.RedisError:
        pass


def _invalidate():
    try:
        cache.incr("products:ver")
    except redis.RedisError:
        pass


# ---------- public API ----------
# Effective price = best active promotion for the product's category (set by the FlashSale operator).
PRODUCT_SELECT = """
SELECT p.id, p.sku, p.name, p.description, p.category, p.price_cents, p.emoji, p.stock,
       pr.percent AS discount_percent, pr.title AS promotion,
       CASE WHEN pr.percent IS NULL THEN p.price_cents
            ELSE p.price_cents * (100 - pr.percent) / 100 END AS sale_price_cents,
       coalesce(r.rating, 0)::float8 AS rating, coalesce(r.reviews, 0) AS review_count
FROM products p
LEFT JOIN LATERAL (SELECT percent, title FROM promotions
                   WHERE category = p.category AND ends_at > now()
                   ORDER BY percent DESC LIMIT 1) pr ON TRUE
LEFT JOIN LATERAL (SELECT round(avg(rating)::numeric, 1) AS rating, count(*) AS reviews
                   FROM reviews WHERE product_id = p.id) r ON TRUE
"""


@app.get("/api/catalog/products")
def list_products(q: str | None = None, category: str | None = None, limit: int = 100):
    ver = _cache_version()
    key = f"products:{ver}:{q or ''}:{category or ''}:{limit}"
    cached = _cache_get(key) if ver != "nocache" else None
    if cached:
        return JSONResponse(json.loads(cached), headers={"X-Cache": "HIT"})

    sql = PRODUCT_SELECT + " WHERE TRUE"
    args = []
    if q:
        sql += " AND (p.name ILIKE %s OR p.description ILIKE %s)"
        args += [f"%{q}%", f"%{q}%"]
    if category:
        sql += " AND p.category = %s"
        args.append(category)
    sql += " ORDER BY p.id LIMIT %s"
    args.append(min(limit, 500))
    with pool.connection(timeout=5) as conn:
        rows = conn.execute(sql, args).fetchall()
    body = {"items": rows, "count": len(rows)}
    if ver != "nocache":
        _cache_set(key, json.dumps(body))
    return JSONResponse(body, headers={"X-Cache": "MISS"})


@app.get("/api/catalog/products/{product_id}")
def get_product(product_id: int):
    with pool.connection(timeout=5) as conn:
        row = conn.execute(PRODUCT_SELECT + " WHERE p.id=%s", (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, "product not found")
    return row


@app.get("/api/catalog/categories")
def categories():
    with pool.connection(timeout=5) as conn:
        rows = conn.execute(
            "SELECT category, count(*) AS products FROM products GROUP BY category ORDER BY category").fetchall()
    return {"items": rows}


@app.get("/api/catalog/promotions")
def active_promotions():
    with pool.connection(timeout=5) as conn:
        rows = conn.execute("SELECT name, title, category, percent, ends_at FROM promotions "
                            "WHERE ends_at > now() ORDER BY percent DESC").fetchall()
    return {"items": [{**r, "ends_at": r["ends_at"].isoformat()} for r in rows]}


@app.get("/api/catalog/products/{product_id}/reviews")
def list_reviews(product_id: int, limit: int = 20):
    with pool.connection(timeout=5) as conn:
        rows = conn.execute(
            "SELECT r.id, r.rating, r.title, r.body, r.verified, r.created_at, u.name AS author "
            "FROM reviews r JOIN users u ON u.id = r.user_id WHERE r.product_id=%s "
            "ORDER BY r.created_at DESC LIMIT %s", (product_id, min(limit, 100))).fetchall()
    return {"items": [{**r, "created_at": r["created_at"].isoformat()} for r in rows]}


class ReviewRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=2000)


@app.post("/api/catalog/products/{product_id}/reviews", status_code=201)
def add_review(product_id: int, body: ReviewRequest, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    with pool.connection(timeout=5) as conn:
        if not conn.execute("SELECT 1 FROM products WHERE id=%s", (product_id,)).fetchone():
            raise HTTPException(404, "product not found")
        # "Verified purchase" = this user has a paid/shipped order containing the product.
        verified = conn.execute(
            "SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.user_id=%s "
            "AND i.product_id=%s AND o.status IN ('PAID','FULFILLING','SHIPPED') LIMIT 1",
            (user["id"], product_id)).fetchone() is not None
        row = conn.execute(
            "INSERT INTO reviews (product_id, user_id, rating, title, body, verified) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (product_id, user_id) DO UPDATE SET rating=EXCLUDED.rating, title=EXCLUDED.title, "
            "body=EXCLUDED.body, verified=EXCLUDED.verified, created_at=now() RETURNING id",
            (product_id, user["id"], body.rating, body.title, body.body, verified)).fetchone()
    _invalidate()
    return {"id": row["id"], "verified": verified}


@app.get("/api/catalog/products/{product_id}/recommendations")
def recommendations(product_id: int):
    """"Customers also bought" -- precomputed into Redis by the recommender CronJob.
    Falls back to top-rated products in the same category."""
    ids, source = [], "co-purchase"
    try:
        raw = cache.get(f"reco:{product_id}")
        ids = json.loads(raw) if raw else []
    except redis.RedisError:
        pass
    with pool.connection(timeout=5) as conn:
        if ids:
            rows = conn.execute(PRODUCT_SELECT + " WHERE p.id = ANY(%s)", (ids,)).fetchall()
            rows.sort(key=lambda r: ids.index(r["id"]))
        else:
            source = "same-category"
            rows = conn.execute(
                PRODUCT_SELECT + " WHERE p.category = (SELECT category FROM products WHERE id=%s) AND p.id <> %s "
                "ORDER BY rating DESC, p.id LIMIT 4", (product_id, product_id)).fetchall()
    return {"items": rows[:4], "source": source}


class ProductPatch(BaseModel):
    price_cents: int | None = Field(default=None, gt=0, le=10_000_000)
    stock: int | None = Field(default=None, ge=0, le=100_000)


@app.patch("/api/catalog/admin/products/{product_id}")
def admin_update_product(product_id: int, body: ProductPatch, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    sets, args = [], []
    for col in ("price_cents", "stock"):
        if getattr(body, col) is not None:
            sets.append(f"{col} = %s")
            args.append(getattr(body, col))
    if not sets:
        raise HTTPException(400, "nothing to update")
    with pool.connection(timeout=5) as conn:
        row = conn.execute(f"UPDATE products SET {', '.join(sets)}, updated_at=now() WHERE id=%s RETURNING id",
                           [*args, product_id]).fetchone()
    if not row:
        raise HTTPException(404, "product not found")
    _invalidate()
    log.info("product updated by admin", extra={"fields": {"product_id": product_id, "admin": user["id"]}})
    return get_product(product_id)


# ---------- internal API (service-to-service only; not exposed by Ingress) ----------
class PromotionRequest(BaseModel):
    title: str = Field(max_length=120)
    category: str
    percent: int = Field(ge=1, le=90)
    ends_at: str  # RFC3339


@app.put("/internal/promotions/{name}")
def upsert_promotion(name: str, body: PromotionRequest):
    """Called by the FlashSale operator when a sale becomes active."""
    with pool.connection(timeout=5) as conn:
        conn.execute(
            "INSERT INTO promotions (name, title, category, percent, ends_at) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (name) DO UPDATE SET title=EXCLUDED.title, category=EXCLUDED.category, "
            "percent=EXCLUDED.percent, ends_at=EXCLUDED.ends_at, updated_at=now()",
            (name, body.title, body.category, body.percent, body.ends_at))
    _invalidate()
    log.info("promotion upserted", extra={"fields": {"promotion": name, "category": body.category, "percent": body.percent}})
    return {"name": name, "applied": True}


@app.delete("/internal/promotions/{name}")
def delete_promotion(name: str):
    with pool.connection(timeout=5) as conn:
        deleted = conn.execute("DELETE FROM promotions WHERE name=%s RETURNING name", (name,)).fetchone()
    _invalidate()
    log.info("promotion removed", extra={"fields": {"promotion": name, "existed": deleted is not None}})
    return {"name": name, "deleted": deleted is not None}



class Item(BaseModel):
    product_id: int
    qty: int = Field(gt=0, le=100)


class ReserveRequest(BaseModel):
    order_id: str
    items: list[Item]


@app.post("/internal/inventory/reserve")
def reserve(body: ReserveRequest):
    try:
        with pool.connection(timeout=5) as conn, conn.transaction():
            # Lock rows in a stable order to avoid deadlocks between concurrent checkouts.
            for it in sorted(body.items, key=lambda i: i.product_id):
                row = conn.execute(
                    "UPDATE products SET stock = stock - %s, updated_at = now() "
                    "WHERE id = %s AND stock >= %s RETURNING id",
                    (it.qty, it.product_id, it.qty)).fetchone()
                if row is None:
                    raise HTTPException(409, f"insufficient stock for product {it.product_id}")
    except HTTPException:
        RESERVE.labels("out_of_stock").inc()
        raise
    RESERVE.labels("ok").inc()
    _invalidate()
    return {"reserved": True, "order_id": body.order_id}


@app.post("/internal/inventory/release")
def release(body: ReserveRequest):
    with pool.connection(timeout=5) as conn, conn.transaction():
        for it in body.items:
            conn.execute("UPDATE products SET stock = stock + %s, updated_at = now() WHERE id = %s",
                         (it.qty, it.product_id))
    RESERVE.labels("released").inc()
    _invalidate()
    return {"released": True, "order_id": body.order_id}


# ---------- chaos endpoints (only reachable via kubectl exec / port-forward) ----------
def _require_chaos():
    if not CHAOS_ENABLED:
        raise HTTPException(404)


def _leak_loop():
    while True:
        time.sleep(6)
        rate = chaos["leak_rate_mb_per_min"]
        if rate:
            chaos["leak"].append(b"\x01" * int(rate * 1024 * 1024 / 10))
            LEAKED.set(sum(len(b) for b in chaos["leak"]))


@app.post("/admin/chaos/leak")
def chaos_leak(mb: int = 0, rate_mb_per_min: float = 0):
    """Allocate `mb` MB now and/or keep leaking at a rate -> eventually OOMKilled."""
    _require_chaos()
    if mb:
        chaos["leak"].append(b"\x01" * (mb * 1024 * 1024))
    chaos["leak_rate_mb_per_min"] = rate_mb_per_min
    leaked = sum(len(b) for b in chaos["leak"])
    LEAKED.set(leaked)
    log.warning("chaos: memory leak", extra={"fields": {"leaked_mb": leaked // 2**20, "rate": rate_mb_per_min}})
    return {"leaked_mb": leaked // 2**20, "rate_mb_per_min": rate_mb_per_min}


@app.post("/admin/chaos/burn")
def chaos_burn(seconds: int = 60, threads: int = 1):
    """Spin CPU so the HPA scales the deployment out."""
    _require_chaos()
    deadline = time.time() + seconds

    def spin():
        x = 0
        while time.time() < deadline:
            x = (x * 31 + 7) % 1_000_003

    for _ in range(max(1, min(threads, 4))):
        threading.Thread(target=spin, daemon=True).start()
    log.warning("chaos: cpu burn", extra={"fields": {"seconds": seconds, "threads": threads}})
    return {"burning_seconds": seconds, "threads": threads}


@app.post("/admin/chaos/ready")
def chaos_ready(state: bool = True):
    _require_chaos()
    chaos["ready"] = state
    log.warning("chaos: readiness toggled", extra={"fields": {"ready": state}})
    return {"ready": state}


@app.post("/admin/chaos/latency")
def chaos_latency(ms: int = 0):
    _require_chaos()
    chaos["latency_ms"] = max(0, ms)
    return {"latency_ms": chaos["latency_ms"]}


@app.post("/admin/chaos/crash")
def chaos_crash():
    """Kill the process -> container restart, RESTARTS column increments."""
    _require_chaos()
    log.critical("chaos: crashing on purpose")
    os._exit(1)


@app.post("/admin/chaos/reset")
def chaos_reset():
    _require_chaos()
    chaos.update({"ready": True, "latency_ms": 0, "leak": [], "leak_rate_mb_per_min": 0})
    LEAKED.set(0)
    return {"reset": True}


@app.get("/admin/info", response_class=PlainTextResponse)
def info():
    return f"service={SERVICE} version={VERSION} pod={POD} chaos_enabled={CHAOS_ENABLED}\n"
