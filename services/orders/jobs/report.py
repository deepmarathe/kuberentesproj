"""Daily sales report (CronJob).

Aggregates the last 24h of orders and, if REPORT_BUCKET is set, uploads the JSON
report to Cloud Storage. Authentication uses Workload Identity: the pod's Kubernetes
ServiceAccount is mapped to a Google service account -- no key files anywhere.
See docs/labs/09-workload-identity.md.
"""
import datetime as dt
import json
import os
import socket
import sys

import psycopg
from psycopg.rows import dict_row

DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=5"
)
BUCKET = os.getenv("REPORT_BUCKET", "")
WINDOW_HOURS = int(os.getenv("REPORT_WINDOW_HOURS", "24"))


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": "daily-report", **fields},
                     default=str), flush=True)


def build_report():
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        by_status = conn.execute(
            "SELECT status, count(*) AS orders, coalesce(sum(total_cents),0) AS total_cents FROM orders "
            "WHERE created_at > now() - make_interval(hours => %s) GROUP BY status ORDER BY status",
            (WINDOW_HOURS,)).fetchall()
        failures = conn.execute(
            "SELECT failure_reason, count(*) AS orders FROM orders WHERE status = 'FAILED' "
            "AND created_at > now() - make_interval(hours => %s) GROUP BY failure_reason ORDER BY 2 DESC",
            (WINDOW_HOURS,)).fetchall()
        top = conn.execute(
            "SELECT i.name, sum(i.qty) AS units, sum(i.qty * i.price_cents) AS revenue_cents "
            "FROM order_items i JOIN orders o ON o.id = i.order_id "
            "WHERE o.status IN ('PAID','FULFILLING','SHIPPED') AND o.created_at > now() - make_interval(hours => %s) "
            "GROUP BY i.name ORDER BY revenue_cents DESC LIMIT 10", (WINDOW_HOURS,)).fetchall()
        low_stock = conn.execute(
            "SELECT sku, name, stock FROM products WHERE stock < 25 ORDER BY stock").fetchall()
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "generated_by": socket.gethostname(),
        "window_hours": WINDOW_HOURS,
        "orders_by_status": by_status,
        "failure_reasons": failures,
        "top_products": top,
        "low_stock": low_stock,
    }


def main():
    try:
        report = build_report()
    except Exception as e:
        log("ERROR", "report query failed", error=str(e))
        sys.exit(1)
    log("INFO", "daily report", report=report)
    if not BUCKET:
        log("INFO", "REPORT_BUCKET not set; skipping upload")
        return
    try:
        from google.cloud import storage  # imported lazily so local runs don't need GCP creds
        name = f"reports/{dt.date.today().isoformat()}/sales-{dt.datetime.now().strftime('%H%M%S')}.json"
        storage.Client().bucket(BUCKET).blob(name).upload_from_string(
            json.dumps(report, default=str, indent=2), content_type="application/json")
        log("INFO", "report uploaded", uri=f"gs://{BUCKET}/{name}")
    except Exception as e:
        # The classic failure here is a 403: Workload Identity binding missing or wrong KSA.
        log("ERROR", "report upload failed", error=str(e)[:500], bucket=BUCKET)
        sys.exit(2)


if __name__ == "__main__":
    main()
