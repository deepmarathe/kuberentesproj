# Lab 01 – Self-healing & probes

**Scenario:** Pods crash, hang, and get deleted all the time in production. Learn exactly what Kubernetes does in each case and how to tell which case you're in.

## 1. Baseline

```bash
kubectl -n shopfleet get deploy,rs,pods -l app=catalog
kubectl -n shopfleet get endpointslices -l kubernetes.io/service-name=catalog -o wide
```

You'll see the chain **Deployment → ReplicaSet (hash in its name) → 2 Pods**. The EndpointSlice lists the IPs of the *ready* pods, which are what the `catalog` Service sends traffic to.

## 2. Delete a pod (the "node died" case)

```bash
kubectl -n shopfleet get pods -l app=catalog -w        # terminal 1
kubectl -n shopfleet delete pod <one-catalog-pod>      # terminal 2
```

**Observe:** a **new pod with a new name** appears within seconds. The ReplicaSet controller saw 1 of 2 and created another. The old one goes `Terminating`: it receives SIGTERM, runs the `preStop` sleep, then exits.

## 3. Crash the process (the "bug" case)

```bash
./scripts/chaos.sh catalog-crash
```

**Observe:** the **same pod name** stays, and `RESTARTS` goes to 1. The *kubelet* restarted the container inside the existing pod (`restartPolicy: Always`). Nothing was rescheduled.

```bash
kubectl -n shopfleet describe pod <that-pod> | sed -n '/Last State/,/Ready/p'   # Reason: Error, Exit Code: 1
kubectl -n shopfleet logs <that-pod> --previous | tail -3                       # "chaos: crashing on purpose"
```

Crash it 4–5 times in a row and you'll see **CrashLoopBackOff**: the kubelet waits 10s, 20s, 40s… (capped at 5 minutes) between restarts.

## 4. Make a pod unready (the "warming up / overloaded" case)

```bash
./scripts/chaos.sh catalog-unready
kubectl -n shopfleet get pods -l app=catalog              # READY 0/1 but STATUS Running
kubectl -n shopfleet get endpointslices -l kubernetes.io/service-name=catalog -o yaml | grep -A3 conditions
```

**Observe:**
- The pod is **not restarted**. It's removed from load balancing.
- Shop in the browser: the **Under the hood** panel now only ever shows the *other* catalog pod.
- On GKE the load balancer also stops sending to it, via the NEG health check.

Fix: `./scripts/chaos.sh catalog-reset`

## 5. Break the liveness probe (the "misconfiguration" case)

```bash
kubectl -n shopfleet patch deployment catalog --type=json \
  -p '[{"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/httpGet/path","value":"/nope"}]'
kubectl -n shopfleet get pods -l app=catalog -w
```

**Observe:**
- The new pods start and become Ready (readiness is fine).
- About 30s later liveness fails 3×, and they get restarted again and again.
- `kubectl describe pod` → Events: `Liveness probe failed: HTTP probe failed with statuscode: 404`.

Fix: `kubectl -n shopfleet rollout undo deployment/catalog`

## 6. Chaos monkey (optional, ongoing)

```bash
./scripts/chaos.sh monkey-on     # deletes a random app pod every 10 minutes
```

Keep shopping while it runs. With 2 replicas and readiness gates, users shouldn't notice. Find each kill in Logs Explorer: `jsonPayload.message="chaos monkey deleted a pod"`. Turn it off with `./scripts/chaos.sh monkey-off`.

## Why it works

| Probe | Failure means | Action |
|---|---|---|
| startupProbe | still starting | wait. Liveness/readiness are paused until it passes |
| readinessProbe | can't serve right now | remove from Service endpoints; **no restart** |
| livenessProbe | stuck/broken | **restart the container** |

- Our liveness endpoint `/healthz` checks *nothing but the process*. Readiness `/readyz` checks the database.
- If liveness checked the DB, a 30-second DB blip would restart **every** pod at once. That turns a blip into an outage, which is the most common probe mistake in production.

## Exam takeaways
- Pod deleted → controller creates a replacement (new name). Container crashed → kubelet restarts it in place (same name, RESTARTS+1).
- CrashLoopBackOff is a *state*, not a cause. Find the cause with `describe` (Last State, exit code) and `logs --previous`.
- PodDisruptionBudgets do **not** protect against `kubectl delete pod` or crashes, only against *evictions* (drain, upgrades, autoscaler).

<details><summary>Check yourself</summary>

1. *A pod shows `Running` but `0/1` READY. Is it receiving traffic?* No. Not ready means it's out of the endpoints.
2. *Exit code 137?* Killed by SIGKILL (128+9): usually OOMKilled, or it didn't stop within the grace period.
3. *Why `startupProbe` instead of a long `initialDelaySeconds` on liveness?* A startup probe adapts: liveness starts as soon as the app is up, while a slow start still gets up to 60s.
</details>
