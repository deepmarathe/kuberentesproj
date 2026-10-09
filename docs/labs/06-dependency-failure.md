# Lab 06 – Dependency failure: Redis and Postgres go down

**Scenario:** Your stateful backbone restarts unexpectedly. What breaks, what degrades gracefully, and is any data lost?

## 1. Know your state

```bash
kubectl -n shopfleet get statefulsets,pvc
kubectl get pv
gcloud compute disks list --filter="name~pvc"      # the real Persistent Disks behind the PVCs
kubectl get storageclass                            # standard-rwo (default, pd-balanced), premium-rwo (SSD)
```

Each StatefulSet pod (`postgres-0`, `redis-0`) has its **own** PVC (`data-postgres-0`). When the pod is deleted and recreated, it re-attaches **the same disk**.

## 2. Redis dies

Add something to your cart in the browser first. Then:

```bash
./scripts/chaos.sh kill-redis
kubectl -n shopfleet get pods -w
```

**Observe, service by service:**

| Service | What happens | Why |
|---|---|---|
| catalog | **keeps working**. `X-Cache: MISS` on every call, slightly slower | Redis is only a cache, and errors are swallowed (`_cache_get`) |
| cart | goes **0/1 Ready**. `/api/cart` errors at the LB | Redis is its database, so readiness fails |
| orders | checkout fails (it can't read the cart). Paid orders can't publish events | reads the cart via the cart service, and `XADD` fails |
| worker | logs `redis error; backing off`, then reconnects | retry loop |

After ~20–30s, `redis-0` is back. **Your cart is still there**: Redis AOF persistence plus the same PVC.

```bash
kubectl -n shopfleet exec redis-0 -- redis-cli XLEN orders      # the event stream survived too
```

**Lost events:** orders that were PAID while Redis was down never reached the worker. Find them:

```bash
kubectl -n shopfleet exec postgres-0 -- psql -U shop -d shopfleet -c \
  "select id, status, updated_at from orders where status='PAID' and updated_at < now() - interval '2 minutes';"
```

The `order-reaper` CronJob republishes them within ~10–15 minutes. Speed it up with `kubectl -n shopfleet create job --from=cronjob/order-reaper reaper-now`.

The proper fix is the **transactional outbox pattern**: write the event to an `outbox` table *in the same DB transaction* as the order, and have a relay publish it. That makes losing the event impossible. A good stretch exercise: implement it in `services/orders`.

## 3. Postgres dies

```bash
./scripts/chaos.sh kill-postgres
kubectl -n shopfleet get pods -w
```

**Observe:**
- catalog and orders go **0/1**, and the LB returns errors for `/api/catalog` and `/api/orders`. The store is down. There's no meaningful degraded mode without the product DB.
- `postgres-0` comes back with the same disk. Readiness passes again within seconds.
- No data is lost: `select count(*) from orders;` before and after.

## 4. Discussion: readiness depends on a dependency. Good or bad?

- cart's readiness checks Redis, so when Redis dies, *all* cart pods go unready at once. Users get an LB error rather than an app error. Some argue the pod should stay "ready" and return a clean 503 JSON error instead, because that's easier to debug and shows on dashboards as an app error.
- There's no universally right answer. The rule of thumb: **never in liveness, carefully in readiness**. Know the trade-off and be able to explain it.

## Exam takeaways
- StatefulSet = stable identity + stable storage. Deployment pods sharing one RWO PVC can't work across nodes.
- PVC `reclaimPolicy: Delete` (the default StorageClass) means **deleting the PVC deletes the disk**. `kubectl delete statefulset` does *not* delete PVCs. That's deliberate, for safety.
- Zonal PDs can only attach to nodes in the same zone. For zone failure tolerance, use regional PDs (`replication-type: regional-pd`) or a managed DB (Cloud SQL, lab 15).

<details><summary>Check yourself</summary>

1. *You scale the postgres StatefulSet to 0, then back to 1. Is data kept?* Yes. The PVC remains and is re-attached.
2. *Why does the catalog stay up when Redis is down but not when Postgres is?* Redis is an optimisation (cache) for the catalog, and Postgres is its source of truth.
3. *How would you make Postgres itself highly available on GKE?* Cloud SQL with HA (regional), or an operator such as CloudNativePG with replicas plus regional disks.
</details>
