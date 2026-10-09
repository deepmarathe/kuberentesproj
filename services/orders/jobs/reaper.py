"""Order reaper (CronJob, every 5 minutes).

1. Orders stuck in PENDING for > STUCK_MINUTES (e.g. the orders pod died mid-checkout)
   are marked FAILED.
2. Orders that are PAID but never picked up by the worker for > REPUBLISH_MINUTES
   (e.g. Redis was down when the event was published) get their event re-published.
"""
import json
import os
import sys
import time

import psycopg
import redis

DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=5"
)
STUCK_MINUTES = int(os.getenv("STUCK_MINUTES", "10"))
REPUBLISH_MINUTES = int(os.getenv("REPUBLISH_MINUTES", "10"))


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": "order-reaper", **fields}), flush=True)


def main():
    try:
        with psycopg.connect(DSN) as conn:
            failed = conn.execute(
                "UPDATE orders SET status = 'FAILED', failure_reason = 'reaped_stuck_pending', updated_at = now() "
                "WHERE status = 'PENDING' AND created_at < now() - make_interval(mins => %s) RETURNING id",
                (STUCK_MINUTES,)).fetchall()
            # Bump updated_at so the same order isn't re-published on every run.
            orphaned = conn.execute(
                "UPDATE orders SET updated_at = now() WHERE id IN ("
                "  SELECT id FROM orders WHERE status = 'PAID' "
                "  AND updated_at < now() - make_interval(mins => %s) LIMIT 500"
                ") RETURNING id, total_cents",
                (REPUBLISH_MINUTES,)).fetchall()
            conn.commit()
    except Exception as e:
        log("ERROR", "reaper failed", error=str(e))
        sys.exit(1)

    republished = 0
    if orphaned:
        r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=int(os.getenv("REDIS_PORT", "6379")))
        for order_id, total in orphaned:
            r.xadd(os.getenv("ORDER_STREAM", "orders"),
                   {"order_id": str(order_id), "total_cents": total, "ts": time.time(), "republished": 1})
            republished += 1
    log("WARNING" if (failed or republished) else "INFO", "reaper complete",
        reaped_pending=len(failed), republished_paid=republished)


if __name__ == "__main__":
    main()
