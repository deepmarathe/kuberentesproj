# Lab 07 – Worker backlog: queues, consumers, and Spot nodes

**Scenario:** Customers complain their orders say "PAID" for hours and never ship. The fulfillment worker is involved.

## 1. Baseline

```bash
kubectl -n shopfleet get deploy worker -o wide
kubectl -n shopfleet get pods -l app=worker -o wide          # which node? (should be the spot pool)
kubectl get nodes -L pool
kubectl -n shopfleet exec redis-0 -- redis-cli XINFO GROUPS orders   # lag, pending, consumers
```

PromQL: `max(worker_stream_lag)` and `sum by (result) (rate(worker_messages_total[5m]))`.

## 2. Stop the worker, and meet the HPA

```bash
./scripts/chaos.sh worker-stop
kubectl -n shopfleet get pods -l app=worker -w
```

**Observe:** the pod terminates, then **comes back** within ~15–30s. The HPA has `minReplicas: 1` and owns the replica count, so your `kubectl scale` lost. This is a common real-world surprise.

To really stop it:

```bash
kubectl -n shopfleet delete hpa worker
kubectl -n shopfleet scale deployment/worker --replicas=0
```

Now watch the backlog grow:

```bash
watch -n10 "kubectl -n shopfleet exec redis-0 -- redis-cli XINFO GROUPS orders | paste - - | head -12"
```

- In the store, your new orders stay **PAID**.
- After ~5 minutes with lag > 50, the **"fulfillment backlog growing"** alert fires.

## 3. Recover and watch it catch up

```bash
./scripts/deploy.sh     # restores the HPA and replicas from git
kubectl -n shopfleet get hpa worker -w
```

The backlog drains. Each order costs real CPU (`WORK_ITERATIONS`), so worker CPU rises and the HPA scales it toward 5 replicas. If the spot pool is full, the cluster autoscaler adds spot nodes (lab 08). Consumer groups split the stream between replicas automatically.

## 4. Kill a worker mid-message (crash recovery)

With at least 2 workers running (scale up with `kubectl -n shopfleet scale deploy/worker --replicas=2` while the HPA allows it):

```bash
kubectl -n shopfleet delete pod <worker-pod> --grace-period=0 --force
kubectl -n shopfleet logs -l app=worker -f --prefix | grep -E "claimed|SIGTERM|stopped"
```

- Messages that worker had *received but not ACKed* stay **pending** under its name.
- After 60s idle, another worker reclaims them with `XAUTOCLAIM` and processes them. That's at-least-once delivery.
- The DB update is idempotent (`WHERE status IN ('PAID','FULFILLING')`), so a double-process is harmless.

Compare with a **graceful** delete (no `--force`): the logs show `SIGTERM received; finishing current message` → `worker stopped cleanly`. `terminationGracePeriodSeconds: 60` gives it time.

## 5. Poison messages → dead-letter queue

```bash
kubectl -n shopfleet exec redis-0 -- redis-cli XADD orders '*' order_id not-a-uuid total_cents 1
# ~3-5 min later (each retry waits for the 60s reclaim):
kubectl -n shopfleet exec redis-0 -- redis-cli XRANGE orders-dlq - +
```

Without a DLQ, a single bad message would be retried forever.

## 6. Spot node reclaimed (simulated)

```bash
SPOT=$(kubectl get nodes -l pool=spot -o jsonpath='{.items[0].metadata.name}')
kubectl drain $SPOT --ignore-daemonsets --delete-emptydir-data
kubectl -n shopfleet get pods -l app=worker -o wide -w
```

- The worker and loadgen get evicted and rescheduled. The worker may land on a `general` node, since it only *prefers* spot (soft affinity) and tolerates the taint.
- Real Spot reclamation gives just **~30 seconds** of notice. Only interruptible, idempotent work belongs there.

Run `kubectl uncordon $SPOT` afterwards. (The autoscaler may remove an empty spot node on its own; that's fine.)

## Exam takeaways
- Taint on the node + toleration on the pod = *allowed* there. Node affinity = *wants or needs* to be there. You usually want both, to steer workloads onto special pools.
- Spot VMs: cheapest, no SLA, ~30s notice. Good for batch, CI, stateless workers. Bad for databases and singletons.
- An HPA overrides manual `kubectl scale`. Delete or adjust the HPA to take manual control.

<details><summary>Check yourself</summary>

1. *Why does the worker have no Service?* Nothing calls it. It *pulls* work from Redis. Metrics are scraped directly from pods via PodMonitoring.
2. *How would you autoscale on backlog instead of CPU?* An HPA with an External/Pods metric (`worker_stream_lag` via the Managed Prometheus adapter), or KEDA.
3. *Liveness for a worker without HTTP?* An exec probe on a heartbeat file (see `worker.yaml`).
</details>
