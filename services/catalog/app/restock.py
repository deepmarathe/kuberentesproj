"""Restock job (run by a CronJob). The load generator buys things all day,
so without this the store would sell out. Exits non-zero on failure so the
Job's backoffLimit/retry behaviour is visible."""
import json
import os
import sys
import time

import psycopg

DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=5"
)
THRESHOLD = int(os.getenv("RESTOCK_THRESHOLD", "25"))
TARGET = int(os.getenv("RESTOCK_TARGET", "200"))


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": "restock", **fields}), flush=True)


def main():
    start = time.time()
    try:
        with psycopg.connect(DSN) as conn:
            cur = conn.execute(
                "UPDATE products SET stock = %s, updated_at = now() WHERE stock < %s RETURNING sku",
                (TARGET, THRESHOLD))
            skus = [r[0] for r in cur.fetchall()]
            conn.commit()
    except Exception as e:
        log("ERROR", "restock failed", error=str(e))
        sys.exit(1)
    log("INFO", "restock complete", restocked=len(skus), skus=skus[:20], seconds=round(time.time() - start, 2))


if __name__ == "__main__":
    main()
