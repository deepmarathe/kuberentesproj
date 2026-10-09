"""Auth service: accounts, login, and JWT issuing.

- Passwords are hashed with bcrypt (deliberately CPU-heavy -> a good HPA target).
- Tokens are HS256 JWTs. The signing key is read from a FILE mounted from a Kubernetes
  Secret (JWT_SECRET_FILE), and re-read when it changes, so the key can be rotated
  without a restart. Other services verify tokens with the same Secret.
- An admin account is seeded from env vars (also from a Secret) once the DB is migrated.
"""
import json
import logging
import os
import re
import socket
import threading
import time

import bcrypt
import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

SERVICE = "auth"
VERSION = os.getenv("APP_VERSION", "dev")
POD = os.getenv("POD_NAME", socket.gethostname())
TOKEN_TTL = int(os.getenv("TOKEN_TTL_SECONDS", str(12 * 3600)))
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "10"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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

REQS = Counter("http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
LAT = Histogram("http_request_duration_seconds", "HTTP latency", ["service", "method", "route"],
                buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
AUTH_EVENTS = Counter("auth_events_total", "Auth events", ["event"])

DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=3"
)
pool = ConnectionPool(DSN, min_size=1, max_size=int(os.getenv("DB_POOL_SIZE", "10")),
                      kwargs={"row_factory": dict_row}, open=False)


class SigningKey:
    """Reads the JWT key from a mounted Secret file; picks up rotations automatically."""

    def __init__(self):
        self.path = os.getenv("JWT_SECRET_FILE", "")
        self._key, self._mtime = os.getenv("JWT_SECRET", ""), None

    def get(self) -> str:
        if self.path:
            try:
                mtime = os.stat(self.path).st_mtime
                if mtime != self._mtime:
                    with open(self.path) as f:
                        self._key = f.read().strip()
                    if self._mtime is not None:
                        log.warning("JWT signing key reloaded from file")
                    self._mtime = mtime
            except OSError:
                pass
        if not self._key:
            raise RuntimeError("no JWT key configured (JWT_SECRET_FILE / JWT_SECRET)")
        return self._key


signing_key = SigningKey()
app = FastAPI(title="auth")


def _seed_admin():
    """Create/refresh the admin account from env. Retries until the users table exists."""
    email, password = os.getenv("ADMIN_EMAIL", ""), os.getenv("ADMIN_PASSWORD", "")
    if not (email and password):
        return
    for attempt in range(60):
        try:
            with pool.connection(timeout=5) as conn:
                row = conn.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone()
                if row is None:
                    conn.execute("INSERT INTO users (email, name, password_hash, role) VALUES (%s, %s, %s, 'admin')",
                                 (email, "Admin", _hash(password)))
                    log.info("admin account created", extra={"fields": {"email": email}})
                else:
                    conn.execute("UPDATE users SET role='admin' WHERE id=%s", (row["id"],))
            return
        except Exception as e:
            if attempt % 10 == 0:
                log.warning("admin seed waiting for database", extra={"fields": {"error": str(e)[:150]}})
            time.sleep(5)


@app.on_event("startup")
def startup():
    pool.open(wait=False)
    threading.Thread(target=_seed_admin, daemon=True).start()
    log.info("auth started")


@app.on_event("shutdown")
def shutdown():
    pool.close()


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        log.exception("unhandled error")
        response, status = JSONResponse({"error": "internal error"}, status_code=500), 500
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if route not in ("/metrics", "/healthz", "/readyz"):
        REQS.labels(SERVICE, request.method, route, str(status)).inc()
        LAT.labels(SERVICE, request.method, route).observe(time.perf_counter() - start)
    response.headers["X-Served-By"] = POD
    response.headers["X-Service"] = f"{SERVICE}@{VERSION}"
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        signing_key.get()
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1 FROM users LIMIT 1")
    except Exception as e:
        return JSONResponse({"status": "not ready", "error": str(e)[:200]}, status_code=503)
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def _token(user) -> str:
    now = int(time.time())
    claims = {"sub": str(user["id"]), "email": user["email"], "name": user["name"], "role": user["role"],
              "iss": "shopfleet-auth", "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(claims, signing_key.get(), algorithm="HS256")


def _public(user):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}


class RegisterRequest(BaseModel):
    email: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterRequest):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "invalid email")
    try:
        with pool.connection(timeout=5) as conn:
            user = conn.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id, email, name, role",
                (email, body.name.strip(), _hash(body.password))).fetchone()
    except UniqueViolation:
        AUTH_EVENTS.labels("register_conflict").inc()
        raise HTTPException(409, "email already registered")
    AUTH_EVENTS.labels("register").inc()
    log.info("user registered", extra={"fields": {"user_id": user["id"]}})
    return {"token": _token(user), "user": _public(user)}


@app.post("/api/auth/login")
def login(body: LoginRequest):
    with pool.connection(timeout=5) as conn:
        user = conn.execute("SELECT id, email, name, role, password_hash FROM users WHERE email=%s",
                            (body.email.strip().lower(),)).fetchone()
    # Always run bcrypt, even for unknown emails, so response time doesn't reveal which emails exist.
    hashed = user["password_hash"].encode() if user else bcrypt.hashpw(b"x", bcrypt.gensalt(BCRYPT_ROUNDS))
    ok = bcrypt.checkpw(body.password.encode(), hashed)
    if not (user and ok):
        AUTH_EVENTS.labels("login_failed").inc()
        log.warning("login failed", extra={"fields": {"email": body.email[:100]}})
        raise HTTPException(401, "invalid email or password")
    AUTH_EVENTS.labels("login").inc()
    return {"token": _token(user), "user": _public(user)}


@app.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(authorization[7:], signing_key.get(), algorithms=["HS256"], issuer="shopfleet-auth")
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid token: {e}")
    with pool.connection(timeout=5) as conn:
        user = conn.execute("SELECT id, email, name, role FROM users WHERE id=%s", (int(claims["sub"]),)).fetchone()
    if not user:
        raise HTTPException(401, "user no longer exists")
    return _public(user)
