"""Tiny migration runner (run as a Kubernetes Job before/alongside app rollout).

- Applies migrations/*.sql in order, each exactly once (tracked in schema_migrations).
- Takes a Postgres advisory lock so two Jobs running at once can't double-apply.
- Retries the initial connection: in Kubernetes the DB pod may still be starting.
"""
import json
import os
import pathlib
import sys
import time

import psycopg

DSN = (
    f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'shopfleet')} user={os.getenv('DB_USER', 'shop')} "
    f"password={os.getenv('DB_PASSWORD', '')} connect_timeout=5"
)
LOCK_ID = 727274


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": "dbmigrate", **fields}), flush=True)


def connect(retries=30):
    for attempt in range(1, retries + 1):
        try:
            return psycopg.connect(DSN, autocommit=True)
        except psycopg.OperationalError as e:
            log("WARNING", "database not reachable yet", attempt=attempt, error=str(e).strip()[:200])
            time.sleep(min(2 * attempt, 10))
    log("ERROR", "giving up connecting to database")
    sys.exit(1)


def main():
    conn = connect()
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
        applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        files = sorted(pathlib.Path(__file__).with_name("migrations").glob("*.sql"))
        for f in files:
            if f.name in applied:
                continue
            log("INFO", "applying migration", file=f.name)
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
        log("INFO", "migrations complete", total=len(files), newly_applied=len(set(f.name for f in files) - applied))
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        conn.close()


if __name__ == "__main__":
    main()
