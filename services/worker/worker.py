"""Fulfillment worker: consumes order events from a Redis Stream (consumer group).

Concepts it demonstrates:
- Consumer groups: N worker replicas share the stream; each message goes to one worker.
- At-least-once delivery: messages are ACKed only after the DB update succeeds.
- Crash recovery: XAUTOCLAIM steals messages stuck with a dead consumer (pod killed mid-work).
- Dead-letter queue: messages failing MAX_ATTEMPTS times go to `orders-dlq`.
- Graceful shutdown: on SIGTERM, finish the current message, then exit.
- Liveness via heartbeat file (exec probe) since there's no HTTP API.
"""
import hashlib
import json
import os
import pathlib
import random
import signal
import socket
import string
import time

import psycopg
import redis
from prometheus_client import Counter, Gauge, Histogram, start_http_server

SERVICE = "worker"
POD = os.getenv("POD_NAME", socket.gethostname())
VERSION = os.getenv("APP_VERSION", "dev")
STREAM = os.getenv("ORDER_STREAM", "orders")
DLQ = STREAM + "-dlq"
UPDATES_CHANNEL = os.getenv("ORDER_UPDATES_CHANNEL", "order-updates")
GROUP = os.getenv("CONSUMER_GROUP", "fulfillment")
FULFILL_SECONDS = float(os.getenv("FULFILL_SECONDS", "2"))
WORK_ITERATIONS = int(os.getenv("WORK_ITERATIONS", "200000"))  # CPU work per order (drives HPA)
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
CLAIM_IDLE_MS = int(os.getenv("CLAIM_IDLE_MS", "60000"))
HEARTBEAT = pathlib.Path(os.getenv("HEARTBEAT_FILE", "/tmp/heartbeat"))
DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=5"
)

PROCESSED = Counter("worker_messages_total", "Messages handled", ["result"])
DURATION = Histogram("worker_processing_seconds", "Time to fulfil one order",
                     buckets=(0.5, 1, 2, 3, 5, 8, 13, 21))
LAG = Gauge("worker_stream_lag", "Entries in the stream not yet delivered to the group")
PENDING = Gauge("worker_stream_pending", "Delivered but un-ACKed entries")

stopping = False


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": SERVICE,
                      "pod": POD, "version": VERSION, **fields}), flush=True)


def on_sigterm(signum, frame):
    global stopping
    stopping = True
    log("INFO", "SIGTERM received; finishing current message then exiting")


def ensure_group(r):
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        log("INFO", "created consumer group", group=GROUP)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def notify(r, order_id, status, row, **extra):
    try:
        r.publish(UPDATES_CHANNEL, json.dumps({"order_id": order_id, "status": status, "session_id": row[1],
                                               "user_id": row[2], "ts": time.time(), **extra}))
    except redis.RedisError:
        pass


def fulfil(db, r, order_id):
    """Idempotent: re-processing an already-shipped order is a no-op."""
    with db.transaction():
        row = db.execute("UPDATE orders SET status='FULFILLING', updated_at=now() "
                         "WHERE id=%s AND status IN ('PAID','FULFILLING') RETURNING id, session_id, user_id",
                         (order_id,)).fetchone()
    if row is None:
        return "skipped"
    notify(r, order_id, "FULFILLING", row)
    # Simulated work: some CPU (so the HPA has something to react to) plus some waiting.
    h = order_id.encode()
    for _ in range(WORK_ITERATIONS):
        h = hashlib.sha256(h).digest()
    time.sleep(FULFILL_SECONDS * random.uniform(0.5, 1.5))
    tracking = "SF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    with db.transaction():
        db.execute("UPDATE orders SET status='SHIPPED', tracking_number=%s, updated_at=now() "
                   "WHERE id=%s AND status='FULFILLING'", (tracking, order_id))
    notify(r, order_id, "SHIPPED", row, tracking_number=tracking)
    return "shipped"


def handle(r, db, msg_id, fields):
    order_id = fields.get("order_id", "")
    start = time.time()
    try:
        result = fulfil(db, r, order_id)
        r.xack(STREAM, GROUP, msg_id)
        r.delete(f"attempts:{msg_id}")
        PROCESSED.labels(result).inc()
        DURATION.observe(time.time() - start)
        log("INFO", "order processed", order_id=order_id, result=result, msg_id=msg_id,
            ms=round((time.time() - start) * 1000))
    except (psycopg.Error, ValueError) as e:
        attempts = r.incr(f"attempts:{msg_id}")
        r.expire(f"attempts:{msg_id}", 86400)
        if attempts >= MAX_ATTEMPTS:
            r.xadd(DLQ, {**fields, "error": str(e)[:300], "msg_id": msg_id})
            r.xack(STREAM, GROUP, msg_id)
            PROCESSED.labels("dead_lettered").inc()
            log("ERROR", "message dead-lettered", order_id=order_id, attempts=attempts, error=str(e)[:300])
        else:
            PROCESSED.labels("retry").inc()
            log("WARNING", "processing failed; will retry", order_id=order_id, attempts=attempts, error=str(e)[:300])
        if isinstance(e, psycopg.OperationalError):
            raise  # connection is broken -> reconnect in main loop


def update_gauges(r):
    try:
        for g in r.xinfo_groups(STREAM):
            if g["name"] == GROUP:
                LAG.set(g.get("lag") or 0)
                PENDING.set(g.get("pending") or 0)
    except redis.RedisError:
        pass


def main():
    signal.signal(signal.SIGTERM, on_sigterm)
    start_http_server(int(os.getenv("METRICS_PORT", "9100")))
    r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=int(os.getenv("REDIS_PORT", "6379")),
                    decode_responses=True, socket_timeout=10)
    log("INFO", "worker starting", stream=STREAM, group=GROUP)
    db = None
    last_claim = 0.0
    while not stopping:
        HEARTBEAT.touch()
        try:
            if db is None or db.closed:
                db = psycopg.connect(DSN, autocommit=True)
                ensure_group(r)
            # Recover messages abandoned by crashed/killed consumers.
            if time.time() - last_claim > 30:
                last_claim = time.time()
                _, claimed, *_ = r.xautoclaim(STREAM, GROUP, POD, min_idle_time=CLAIM_IDLE_MS, start_id="0-0", count=20)
                if claimed:
                    log("WARNING", "claimed abandoned messages", count=len(claimed))
                for msg_id, fields in claimed:
                    if fields:  # entry may have been trimmed
                        handle(r, db, msg_id, fields)
                update_gauges(r)
            resp = r.xreadgroup(GROUP, POD, {STREAM: ">"}, count=5, block=5000)
            for _, messages in resp or []:
                for msg_id, fields in messages:
                    handle(r, db, msg_id, fields)
                    if stopping:
                        break
        except redis.RedisError as e:
            log("ERROR", "redis error; backing off", error=str(e))
            time.sleep(3)
        except psycopg.Error as e:
            log("ERROR", "database error; reconnecting", error=str(e)[:300])
            if db is not None:
                db.close()
            db = None
            time.sleep(3)
    if db is not None:
        db.close()
    log("INFO", "worker stopped cleanly")


if __name__ == "__main__":
    main()
