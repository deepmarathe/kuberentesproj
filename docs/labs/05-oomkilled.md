# Lab 05 – OOMKilled: memory limits in practice

**Scenario:** A catalog pod keeps restarting every few minutes. There are no errors in the logs. Classic memory leak.

## 1. Baseline

```bash
kubectl -n shopfleet top pods -l app=catalog
kubectl -n shopfleet get deploy catalog -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'
kubectl -n shopfleet get pod <catalog-pod> -o jsonpath='{.status.qosClass}{"\n"}'     # Burstable
```

The request is 192Mi (what the scheduler reserves) and the limit is 384Mi (the hard cap).

## 2. Start a leak

```bash
./scripts/chaos.sh catalog-leak 100        # +100 MB/min in one pod
watch -n5 kubectl -n shopfleet top pods -l app=catalog
```

After ~2–3 minutes the pod hits 384Mi and the Linux kernel's OOM killer ends the process.

## 3. Diagnose it the way you would in production

```bash
kubectl -n shopfleet get pods -l app=catalog           # RESTARTS = 1
kubectl -n shopfleet describe pod <pod> | grep -A5 "Last State"
#   Last State: Terminated
#     Reason:   OOMKilled
#     Exit Code: 137
kubectl -n shopfleet logs <pod> --previous | tail       # nothing useful: the kernel killed it instantly
```

Memory over time, in Metrics Explorer (PromQL):

```promql
max by (pod_name) (kubernetes_io:container_memory_used_bytes{namespace_name="shopfleet", container_name="catalog", memory_type="non-evictable"})
```

You'll see a sawtooth: memory climbs, hits the limit, drops to zero, and climbs again. In production that shape *is* the diagnosis.

Logs Explorer (GKE also records an event): `jsonPayload.reason="OOMKilling"` or search `"OOMKilled"`.

## 4. Mitigate, then fix

- **Mitigate:** the restart already freed the memory (the leak is in-process state). Here, the reset after restart is the mitigation. In real life you might temporarily raise the limit: `kubectl -n shopfleet set resources deploy/catalog --limits=memory=512Mi`. That's drift from git, so record it.
- **Fix:** the real fix is in code. In this lab, `./scripts/chaos.sh catalog-reset` (or the restart itself) clears it.
- **Right-size:** look at real usage over days (VPA recommendations, lab 08) and set requests near typical usage and limits with headroom.

## Why it works: QoS classes

| QoS | Rule | Evicted under node memory pressure |
|---|---|---|
| **Guaranteed** | requests == limits for CPU *and* memory, in every container | last |
| **Burstable** | some requests/limits set (our apps) | middle |
| **BestEffort** | nothing set | first |

Two different things kill pods for memory:
1. **Container OOMKill**: the container exceeded *its own* limit. The kernel kills it and the kubelet restarts it. Same pod.
2. **Node-pressure eviction**: the *node* is running low. The kubelet evicts whole pods (BestEffort first, then Burstable pods using more than their request). The pod is rescheduled elsewhere. `kubectl get pods` shows `Evicted`.

## Exam takeaways
- Exit code **137** = SIGKILL, most often OOMKilled. Exit code **143** = SIGTERM (graceful stop).
- Memory limits are enforced (kill). CPU limits are throttled (slow).
- `kubectl top` needs metrics-server. GKE provides it.

<details><summary>Check yourself</summary>

1. *Request 192Mi, limit 384Mi, node has 300Mi free. Can the pod schedule?* Yes. Scheduling uses **requests** only.
2. *A pod's memory request is far above its real usage. Why does that cost money?* The scheduler reserves it, so nodes fill up "on paper" and the cluster autoscaler adds nodes you don't need.
3. *How do you make a pod Guaranteed?* Set requests = limits for both CPU and memory in every container.
</details>
