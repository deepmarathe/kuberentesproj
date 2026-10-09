"""Recommender (CronJob, every 30 min): "customers who bought X also bought Y".

Counts how often products appear in the same order over the last LOOKBACK_DAYS and stores
the top 4 per product in Redis (reco:<product_id>, 2h TTL). catalog serves them at
/api/catalog/products/{id}/recommendations and falls back to same-category items.
A classic batch job: reads Postgres, writes a cache, exits 0/non-zero.
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
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
TOP_N = int(os.getenv("TOP_N", "4"))


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": "recommender", **fields}), flush=True)


def main():
    start = time.time()
    try:
        with psycopg.connect(DSN) as conn:
            rows = conn.execute("""
                SELECT a.product_id, b.product_id AS other, count(*) AS together
                FROM order_items a
                JOIN order_items b ON a.order_id = b.order_id AND a.product_id <> b.product_id
                JOIN orders o ON o.id = a.order_id
                WHERE o.status IN ('PAID','FULFILLING','SHIPPED')
                  AND o.created_at > now() - make_interval(days => %s)
                GROUP BY 1, 2 ORDER BY 1, 3 DESC""", (LOOKBACK_DAYS,)).fetchall()
    except Exception as e:
        log("ERROR", "query failed", error=str(e))
        sys.exit(1)
    recos = {}
    for product_id, other, _ in rows:
        lst = recos.setdefault(product_id, [])
        if len(lst) < TOP_N:
            lst.append(other)
    r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=int(os.getenv("REDIS_PORT", "6379")))
    pipe = r.pipeline()
    for product_id, others in recos.items():
        pipe.setex(f"reco:{product_id}", 7200, json.dumps(others))
    pipe.execute()
    log("INFO", "recommendations computed", products=len(recos), pairs=len(rows), seconds=round(time.time() - start, 2))


if __name__ == "__main__":
    main()
