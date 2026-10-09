"""Orders service: checkout orchestration.

Checkout flow (a simple "saga"):
  1. read cart (cart service)          4. charge payment (payment service, retries + circuit breaker)
  2. insert order PENDING (Postgres)   5. mark PAID, clear cart, publish event to Redis stream
  3. reserve stock (catalog service)   on failure -> release stock, mark FAILED
The worker consumes the stream and moves orders to FULFILLING -> SHIPPED.
"""
import json
import logging
import os
import socket
import time
import uuid

import httpx
import jwt
import redis
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SERVICE = "orders"
VERSION = os.getenv("APP_VERSION", "dev")
POD = os.getenv("POD_NAME", socket.gethostname())
CART_URL = os.getenv("CART_URL", "http://cart:8080")
CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog:8080")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://payment:8080")
PAYMENT_TIMEOUT = float(os.getenv("PAYMENT_TIMEOUT_SECONDS", "3"))
PAYMENT_RETRIES = int(os.getenv("PAYMENT_RETRIES", "2"))
STREAM = os.getenv("ORDER_STREAM", "orders")
UPDATES_CHANNEL = os.getenv("ORDER_UPDATES_CHANNEL", "order-updates")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"severity": record.levelname, "message": record.getMessage(),
                   "service": SERVICE, "pod": POD, "version": VERSION}
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[_handler], force=True)
log = logging.getLogger(SERVICE)


def lf(**fields):
    return {"extra": {"fields": fields}}


REQS = Counter("http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
LAT = Histogram("http_request_duration_seconds", "HTTP latency", ["service", "method", "route"],
                buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
CHECKOUTS = Counter("orders_checkout_total", "Checkout attempts by result", ["result"])
CHECKOUT_LAT = Histogram("orders_checkout_duration_seconds", "End-to-end checkout latency",
                         buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16))
PAYMENT_CALLS = Counter("orders_payment_calls_total", "Calls to payment service", ["outcome"])
REVENUE = Counter("orders_revenue_cents_total", "Revenue from paid orders (cents)")
BREAKER_STATE = Gauge("orders_payment_circuit_open", "1 if the payment circuit breaker is open")

DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=3"
)
pool = ConnectionPool(DSN, min_size=1, max_size=int(os.getenv("DB_POOL_SIZE", "10")),
                      kwargs={"row_factory": dict_row}, open=False)
bus = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=int(os.getenv("REDIS_PORT", "6379")),
                  socket_timeout=1, socket_connect_timeout=1, decode_responses=True)
http = httpx.Client(timeout=httpx.Timeout(3.0, connect=1.0))


class CircuitBreaker:
    """After `threshold` consecutive failures, fail fast for `cooldown` seconds,
    then let one trial request through (half-open). Protects payment from a retry storm
    and keeps checkout latency bounded when payment is down."""

    def __init__(self, threshold=5, cooldown=15):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures, self.opened_at = 0, 0.0

    def allow(self):
        if self.failures < self.threshold:
            return True
        return time.time() - self.opened_at >= self.cooldown  # half-open trial

    def success(self):
        if self.failures >= self.threshold:
            log.warning("payment circuit closed")
        self.failures = 0
        BREAKER_STATE.set(0)

    def failure(self):
        self.failures += 1
        if self.failures >= self.threshold:
            if self.failures == self.threshold:
                log.error("payment circuit OPEN", **lf(cooldown=self.cooldown))
            self.opened_at = time.time()
            BREAKER_STATE.set(1)


breaker = CircuitBreaker(int(os.getenv("BREAKER_THRESHOLD", "5")), int(os.getenv("BREAKER_COOLDOWN_SECONDS", "15")))
app = FastAPI(title="orders")


@app.on_event("startup")
def startup():
    pool.open(wait=False)
    log.info("orders started")


@app.on_event("shutdown")
def shutdown():
    log.info("orders shutting down (SIGTERM received)")
    pool.close()
    http.close()


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        log.exception("unhandled error", **lf(path=request.url.path))
        response, status = JSONResponse({"error": "internal error"}, status_code=500), 500
    elapsed = time.perf_counter() - start
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if route not in ("/metrics", "/healthz", "/readyz"):
        REQS.labels(SERVICE, request.method, route, str(status)).inc()
        LAT.labels(SERVICE, request.method, route).observe(elapsed)
    response.headers["X-Served-By"] = POD
    response.headers["X-Service"] = f"{SERVICE}@{VERSION}"
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1 FROM orders LIMIT 1")
    except Exception as e:
        return JSONResponse({"status": "db unavailable", "error": str(e)[:200]}, status_code=503)
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _jwt_key() -> str:
    path = os.getenv("JWT_SECRET_FILE", "")
    if path:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            pass
    return os.getenv("JWT_SECRET", "")


def _user(authorization: str | None, required: bool = False):
    """Optional login: guests check out by session id, logged-in users also get user_id."""
    if not authorization or not authorization.startswith("Bearer "):
        if required:
            raise HTTPException(401, "login required")
        return None
    try:
        c = jwt.decode(authorization[7:], _jwt_key(), algorithms=["HS256"], issuer="shopfleet-auth")
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")
    return {"id": int(c["sub"]), "role": c.get("role", "customer")}


def _notify(order_id, status, sid, user_id, **extra):
    """Fire-and-forget live update for the notifications service (Redis pub/sub)."""
    try:
        bus.publish(UPDATES_CHANNEL, json.dumps({"order_id": order_id, "status": status, "session_id": sid,
                                                 "user_id": user_id, "ts": time.time(), **extra}))
    except redis.RedisError:
        pass


def _session(x_session_id: str | None) -> str:
    if not x_session_id or not (8 <= len(x_session_id) <= 64):
        raise HTTPException(400, "missing or invalid X-Session-Id header")
    return x_session_id


def _set_status(order_id, status, **cols):
    sets = ", ".join(["status = %s", "updated_at = now()"] + [f"{k} = %s" for k in cols])
    with pool.connection(timeout=5) as conn:
        conn.execute(f"UPDATE orders SET {sets} WHERE id = %s", [status, *cols.values(), order_id])


def _charge(order_id: str, amount_cents: int) -> tuple[str, str | None]:
    """Returns (outcome, payment_id). outcome in ok|declined|unavailable."""
    if not breaker.allow():
        PAYMENT_CALLS.labels("circuit_open").inc()
        return "unavailable", None
    for attempt in range(PAYMENT_RETRIES + 1):
        try:
            r = http.post(f"{PAYMENT_URL}/charge", timeout=PAYMENT_TIMEOUT,
                          json={"order_id": order_id, "amount_cents": amount_cents},
                          headers={"Idempotency-Key": order_id})  # retries are safe: same key
            if r.status_code == 200:
                PAYMENT_CALLS.labels("ok").inc()
                breaker.success()
                return "ok", r.json()["payment_id"]
            if r.status_code == 402:
                PAYMENT_CALLS.labels("declined").inc()
                breaker.success()  # a decline is a healthy response
                return "declined", None
            PAYMENT_CALLS.labels(f"http_{r.status_code}").inc()
        except httpx.TimeoutException:
            PAYMENT_CALLS.labels("timeout").inc()
        except httpx.HTTPError as e:
            PAYMENT_CALLS.labels("connect_error").inc()
            log.warning("payment call failed", **lf(error=str(e), attempt=attempt))
        breaker.failure()
        if attempt < PAYMENT_RETRIES and breaker.allow():
            time.sleep(0.2 * (2 ** attempt))  # exponential backoff
    return "unavailable", None


@app.post("/api/orders/checkout", status_code=201)
def checkout(x_session_id: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    sid = _session(x_session_id)
    user = _user(authorization)
    start = time.perf_counter()
    order_id = str(uuid.uuid4())
    try:
        result, body = _checkout(sid, order_id, user["id"] if user else None)
    finally:
        CHECKOUT_LAT.observe(time.perf_counter() - start)
    CHECKOUTS.labels(result).inc()
    log.info("checkout", **lf(order_id=order_id, result=result, session=sid,
                             ms=round((time.perf_counter() - start) * 1000)))
    if result != "paid":
        status = {"empty_cart": 400, "out_of_stock": 409, "payment_declined": 402}.get(result, 503)
        return JSONResponse({"order_id": order_id, "error": result, **body}, status_code=status)
    return body


def _checkout(sid: str, order_id: str, user_id: int | None = None):
    try:
        cart = http.get(f"{CART_URL}/internal/cart/{sid}").raise_for_status().json()
    except httpx.HTTPError as e:
        log.error("cart unavailable", **lf(error=str(e)))
        return "cart_unavailable", {}
    items = cart.get("items", [])
    if not items:
        return "empty_cart", {}
    total = sum(i["qty"] * i["price_cents"] for i in items)

    with pool.connection(timeout=5) as conn, conn.transaction():
        conn.execute("INSERT INTO orders (id, session_id, user_id, status, total_cents) VALUES (%s, %s, %s, 'PENDING', %s)",
                     (order_id, sid, user_id, total))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO order_items (order_id, product_id, name, qty, price_cents) VALUES (%s, %s, %s, %s, %s)",
                [(order_id, i["product_id"], i["name"], i["qty"], i["price_cents"]) for i in items])

    reserve_body = {"order_id": order_id, "items": [{"product_id": i["product_id"], "qty": i["qty"]} for i in items]}
    try:
        r = http.post(f"{CATALOG_URL}/internal/inventory/reserve", json=reserve_body)
    except httpx.HTTPError as e:
        _set_status(order_id, "FAILED", failure_reason="catalog_unavailable")
        log.error("catalog unavailable", **lf(error=str(e)))
        return "catalog_unavailable", {}
    if r.status_code == 409:
        _set_status(order_id, "FAILED", failure_reason="out_of_stock")
        return "out_of_stock", {"detail": r.json().get("detail")}
    if r.status_code != 200:
        _set_status(order_id, "FAILED", failure_reason=f"catalog_{r.status_code}")
        return "catalog_unavailable", {}

    outcome, payment_id = _charge(order_id, total)
    if outcome != "ok":
        # Compensating action: give the stock back.
        try:
            http.post(f"{CATALOG_URL}/internal/inventory/release", json=reserve_body)
        except httpx.HTTPError:
            log.error("failed to release stock -- inventory drift!", **lf(order_id=order_id))
        reason = "payment_declined" if outcome == "declined" else "payment_unavailable"
        _set_status(order_id, "FAILED", failure_reason=reason)
        _notify(order_id, "FAILED", sid, user_id, reason=reason, total_cents=total)
        return reason, {}

    _set_status(order_id, "PAID", payment_id=payment_id)
    REVENUE.inc(total)
    _notify(order_id, "PAID", sid, user_id, total_cents=total)
    try:
        http.delete(f"{CART_URL}/internal/cart/{sid}")
    except httpx.HTTPError:
        log.warning("could not clear cart", **lf(order_id=order_id))
    try:
        bus.xadd(STREAM, {"order_id": order_id, "total_cents": total, "ts": time.time()}, maxlen=100_000)
    except redis.RedisError as e:
        # Order is PAID but the event was lost. The real fix is the "transactional outbox"
        # pattern -- see docs/labs/06-dependency-failure.md. The reaper job re-publishes these.
        log.error("failed to publish order event", **lf(order_id=order_id, error=str(e)))
    return "paid", _get_order(order_id)


def _get_order(order_id: str, sid: str | None = None, user_id: int | None = None):
    with pool.connection(timeout=5) as conn:
        q, args = "SELECT * FROM orders WHERE id = %s", [order_id]
        if sid is not None:
            q += " AND (session_id = %s OR (user_id IS NOT NULL AND user_id = %s))"
            args += [sid, user_id]
        order = conn.execute(q, args).fetchone()
        if not order:
            return None
        order["items"] = conn.execute(
            "SELECT product_id, name, qty, price_cents FROM order_items WHERE order_id = %s ORDER BY product_id",
            (order_id,)).fetchall()
    return order


@app.get("/api/orders")
def list_orders(x_session_id: str | None = Header(default=None), authorization: str | None = Header(default=None),
                limit: int = 20):
    sid = _session(x_session_id)
    user = _user(authorization)
    with pool.connection(timeout=5) as conn:
        rows = conn.execute(
            "SELECT id, status, total_cents, failure_reason, tracking_number, created_at, updated_at "
            "FROM orders WHERE session_id = %s OR (user_id IS NOT NULL AND user_id = %s) "
            "ORDER BY created_at DESC LIMIT %s",
            (sid, user["id"] if user else None, min(limit, 100))).fetchall()
    return {"items": rows}


def _require_admin(authorization):
    user = _user(authorization, required=True)
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


@app.get("/api/orders/admin/stats")
def admin_stats(authorization: str | None = Header(default=None), minutes: int = 60):
    _require_admin(authorization)
    with pool.connection(timeout=5) as conn:
        by_status = conn.execute(
            "SELECT status, count(*) AS orders, coalesce(sum(total_cents),0) AS total_cents FROM orders "
            "WHERE created_at > now() - make_interval(mins => %s) GROUP BY status ORDER BY status",
            (min(minutes, 10080),)).fetchall()
        failures = conn.execute(
            "SELECT failure_reason, count(*) AS orders FROM orders WHERE status='FAILED' "
            "AND created_at > now() - make_interval(mins => %s) GROUP BY 1 ORDER BY 2 DESC",
            (min(minutes, 10080),)).fetchall()
        users = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
    return {"window_minutes": minutes, "by_status": by_status, "failures": failures, "registered_users": users}


@app.get("/api/orders/admin/recent")
def admin_recent(authorization: str | None = Header(default=None), limit: int = 30):
    _require_admin(authorization)
    with pool.connection(timeout=5) as conn:
        rows = conn.execute(
            "SELECT o.id, o.status, o.total_cents, o.failure_reason, o.created_at, u.email "
            "FROM orders o LEFT JOIN users u ON u.id = o.user_id ORDER BY o.created_at DESC LIMIT %s",
            (min(limit, 200),)).fetchall()
    return {"items": rows}


@app.get("/api/orders/{order_id}")
def get_order(order_id: uuid.UUID, x_session_id: str | None = Header(default=None),
              authorization: str | None = Header(default=None)):
    user = _user(authorization)
    order = _get_order(str(order_id), _session(x_session_id), user["id"] if user else None)
    if not order:
        raise HTTPException(404, "order not found")
    return order
