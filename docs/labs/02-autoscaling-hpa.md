# Lab 02 – Autoscaling with the HorizontalPodAutoscaler

**Scenario:** Marketing just announced a flash sale. Will the platform keep up, and how fast?

## 1. Baseline

```bash
kubectl -n shopfleet get hpa
kubectl -n shopfleet describe hpa catalog
kubectl -n shopfleet top pods
```

`TARGETS` like `cpu: 23%/60%` means the average CPU *as a percentage of the pod's request* (catalog requests 100m, so 23% = 23m).

## 2. Burn CPU

```bash
watch -n5 kubectl -n shopfleet get hpa catalog      # terminal 1
./scripts/chaos.sh catalog-cpu 300                  # terminal 2: each catalog pod spins ~1 core for 5 min
```

**Observe:**
- `TARGETS` jumps to several hundred %.
- Within ~30–60s, replicas climb, up to doubling every 30s per our `scaleUp` policy, until `maxReplicas: 8`.
- `kubectl -n shopfleet describe hpa catalog` → Events: `New size: 4; reason: cpu resource utilization (percentage of request) above target`.
- New pods may go **Pending** if the nodes are full. The Cluster Autoscaler then adds a node (lab 08).
- When the burn ends, the HPA waits for the **stabilization window** (120s), then removes **one pod per minute**.

## 3. Real traffic: flash sale

```bash
./scripts/chaos.sh flash-sale         # 60–120 simulated users
kubectl -n loadgen port-forward svc/loadgen 8089:8089   # Locust UI via Cloud Shell Web Preview :8089
```

Watch several services scale *at the same time*: frontend, catalog, cart, orders. Look at p95 latency in Locust, or this PromQL query:

```promql
histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[2m])))
```

Back to normal: `./scripts/chaos.sh traffic-normal`

## 4. Create an HPA yourself (imperative, exam-style)

```bash
kubectl -n shopfleet autoscale deployment payment --cpu-percent=70 --min=2 --max=5
kubectl -n shopfleet get hpa payment -o yaml        # see what it generated (autoscaling/v2)
kubectl -n shopfleet delete hpa payment
```

## Why it works

```
desiredReplicas = ceil( currentReplicas × currentUtilization / targetUtilization )
                  e.g.  ceil( 2 × 450% / 60% ) = 15 → capped at maxReplicas 8
```

- CPU usage comes from **metrics-server**, which GKE runs for you.
- Utilisation is computed against **requests**. With no request set, the HPA can't compute it and shows `<unknown>`.
- `behavior.scaleDown.stabilizationWindowSeconds` prevents flapping: the HPA uses the highest recommendation from the last 120s.

## Exam takeaways
- **HPA** = more pods. **VPA** = bigger pods. **Cluster Autoscaler** = more nodes. They work together: HPA adds pods → pods go Pending → CA adds nodes.
- HPA can also scale on memory, custom metrics (e.g. requests per second from Managed Prometheus), or external metrics (e.g. Pub/Sub queue depth).
- `kubectl autoscale` creates a CPU-based HPA in one line.
- Don't set `replicas:` in git *and* use an HPA that fights it. (We do set it; `kubectl apply` resets replicas on every deploy. Discuss: how would you avoid that? Answer: remove `replicas` from the manifest once an HPA owns the Deployment.)

<details><summary>Check yourself</summary>

1. *HPA shows `<unknown>/60%`?* No metrics yet (pods just started), metrics-server is unavailable, or the containers have no CPU request.
2. *Why didn't it scale below 2 at night?* `minReplicas: 2`. That's for availability, not load.
3. *Scale-up stalls at 5 even though max is 8?* Check ResourceQuota (lab 08) and Pending pods.
</details>
