# 02 – Day-2 operations: the cloud engineer's routine

"Day 1" is building and deploying. "Day 2" is everything after: keeping it healthy, finding out why it isn't, changing it safely, and keeping costs down. This is most of the job.

## The daily check (5 minutes)

```bash
./scripts/status.sh            # or do it by hand:
kubectl get nodes                                         # all Ready? any new/removed (autoscaler)?
kubectl get pods -A | grep -vE 'Running|Completed'        # anything unhealthy anywhere?
kubectl -n shopfleet get pods                             # RESTARTS column climbing?
kubectl -n shopfleet get hpa                              # replicas near max? targets sane?
kubectl -n shopfleet get events --field-selector type=Warning --sort-by=.lastTimestamp | tail
kubectl -n shopfleet get cronjobs                         # LAST SCHEDULE recent?
kubectl -n shopfleet top pods                             # who's using what
```

Then Console → Monitoring → Alerting: are any incidents open?

## kubectl cheat sheet (memorise the shape of these)

```bash
# --- inspect ---
kubectl get pods -o wide                       # node + IP per pod
kubectl get pods -l app=catalog                # label selector
kubectl get pod POD -o yaml                    # full live object (status at the bottom!)
kubectl describe pod POD                       # human summary + EVENTS (read these first)
kubectl get events --sort-by=.lastTimestamp
kubectl get endpointslices -l kubernetes.io/service-name=catalog   # which pod IPs back a Service
kubectl explain deployment.spec.strategy       # built-in API docs (allowed in many exams)
kubectl api-resources                          # every kind the cluster knows (incl. GKE CRDs)

# --- logs ---
kubectl logs deploy/catalog                    # one pod of the deployment
kubectl logs -l app=catalog --all-containers --prefix -f   # all pods, follow
kubectl logs POD --previous                    # the CRASHED container's logs (after a restart)
kubectl logs job/db-migrate

# --- change ---
kubectl apply -f file.yaml / -k dir/           # declarative (preferred)
kubectl scale deploy/catalog --replicas=4
kubectl set image deploy/catalog catalog=IMAGE:TAG
kubectl set env deploy/orders LOG_LEVEL=DEBUG
kubectl rollout status|history|undo|restart deploy/catalog
kubectl rollout undo deploy/catalog --to-revision=3
kubectl edit deploy/catalog                    # live edit (drifts from git!, avoid in prod)
kubectl patch cronjob chaos-monkey -p '{"spec":{"suspend":true}}'
kubectl create job --from=cronjob/restock restock-now

# --- debug ---
kubectl exec -it POD -- sh                     # shell (not possible on distroless payment)
kubectl debug -it POD --image=busybox:1.36 --target=payment   # ephemeral debug container
kubectl run tmp --rm -it --image=curlimages/curl --restart=Never -- sh   # throwaway client pod
kubectl port-forward svc/catalog 8081:8080     # reach a ClusterIP service from your shell
kubectl auth can-i delete pods --as=system:serviceaccount:shopfleet:chaos-monkey -n shopfleet

# --- nodes ---
kubectl cordon NODE / uncordon NODE            # stop/allow new pods
kubectl drain NODE --ignore-daemonsets --delete-emptydir-data   # evict (respects PDBs)
kubectl top nodes
```

Productivity: put `alias k=kubectl` and `source <(kubectl completion bash)` in `~/.bashrc`. Use `kubectl config set-context --current --namespace=shopfleet` to stop typing `-n shopfleet`.

## gcloud essentials

```bash
gcloud container clusters list
gcloud container clusters describe shopfleet --zone us-central1-a
gcloud container clusters get-credentials shopfleet --zone us-central1-a
gcloud container node-pools list --cluster shopfleet --zone us-central1-a
gcloud container clusters resize shopfleet --node-pool general --num-nodes 3 --zone us-central1-a
gcloud container clusters upgrade shopfleet --master --cluster-version VERSION --zone us-central1-a
gcloud container get-server-config --zone us-central1-a      # available versions per channel
gcloud artifacts docker images list us-central1-docker.pkg.dev/PROJECT/shopfleet
gcloud builds list --limit 5
gcloud logging read 'resource.type="k8s_container" AND severity>=ERROR' --limit 20 --freshness 1h
```

## Logs (Cloud Logging)

Every service writes **one JSON object per line** with `severity` and `message`. GKE ships stdout to Cloud Logging, which turns JSON into searchable `jsonPayload.*` fields. Useful Logs Explorer queries:

```
# All errors in the app
resource.type="k8s_container" resource.labels.namespace_name="shopfleet" severity>=ERROR

# Failed checkouts and why
jsonPayload.message="checkout" jsonPayload.result!="paid"

# One pod's logs
resource.labels.pod_name="catalog-7c9d6f-abcde"

# OOM kills / probe failures / scheduling problems (Kubernetes events)
resource.type="k8s_pod" jsonPayload.reason=("OOMKilling" OR "Unhealthy" OR "FailedScheduling" OR "BackOff")

# Load balancer request logs (sampled 20%, see backendconfigs.yaml)
resource.type="http_load_balancer" httpRequest.status>=500

# Pod deleted by whom? (audit logs)
protoPayload.methodName="io.k8s.core.v1.pods.delete"
```

Create a **log-based metric** from the checkout query, then alert on it. That's a classic exam task.

## Metrics (Managed Prometheus, PromQL)

Monitoring → Metrics Explorer → PromQL:

```promql
# Request rate per service
sum by (service) (rate(http_requests_total[5m]))
# Error ratio per service
sum by (service) (rate(http_requests_total{status=~"5.."}[5m])) / sum by (service) (rate(http_requests_total[5m]))
# p95 latency per route
histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket[5m])))
# Checkout outcomes
sum by (result) (rate(orders_checkout_total[5m]))
# Revenue per minute ($)
sum(rate(orders_revenue_cents_total[5m])) * 60 / 100
# Is the payment breaker open?
max(orders_payment_circuit_open)
# Fulfillment backlog
max(worker_stream_lag)
# Catalog cache hit ratio
sum(rate(catalog_cache_requests_total{result="hit"}[5m])) / sum(rate(catalog_cache_requests_total[5m]))
# Container restarts (GKE system metric)
sum by (container) (increase(kubernetes_io:container_restart_count{namespace_name="shopfleet"}[1h]))
```

Build a dashboard from these: Monitoring → Dashboards → Create → add PromQL charts. That's lab 17.

## Golden rules (with the "why")

1. **Change via git + apply, not `kubectl edit`.** Live edits get overwritten by the next deploy and nobody knows they happened.
2. **Every container has requests.** Without them the scheduler is guessing, and the HPA can't compute utilisation.
3. **Memory limit yes, CPU limit usually no.**
   - Memory can't be reclaimed, so a limit protects the node.
   - CPU *can* be shared. A CPU limit throttles your app even when the node is idle, which shows up as mysterious latency.
   - Requests already guarantee each pod its share.
   - (Some orgs require CPU limits anyway, for example to get "Guaranteed" QoS. Know both sides.)
4. **Liveness ≠ readiness.** Liveness must not depend on the DB or other services.
5. **Run at least 2 replicas plus a PDB** for anything user-facing, and spread them across nodes or zones.
6. **Roll forward small, roll back fast.** `kubectl rollout undo` is your best friend. Labs 04 and 16.
7. **Immutable image tags.** Deploy `catalog:20250101-120000`, not `:latest`. Then you know exactly what's running and can roll back.

## Runbooks

### Storefront down
1. `kubectl -n shopfleet get pods`: are frontend/catalog pods Running and READY 1/1?
2. `kubectl -n shopfleet describe ingress shopfleet`: are the backends `HEALTHY`?
3. Are endpoints present? `kubectl -n shopfleet get endpointslices`. Empty means no ready pods, or the selector is wrong.
4. LB logs: `resource.type="http_load_balancer"`. Look for `statusDetails`, e.g. `failed_to_connect_to_backend` or `backend_timeout`.
5. Recent change? `kubectl -n shopfleet rollout history deploy/frontend`. Roll back first, investigate second.

### Error rate spike
1. Which service? Use the error-ratio PromQL above.
2. Its logs: `severity>=ERROR` filtered by `jsonPayload.service`.
3. Is it a dependency? Orders failing with `payment_unavailable` means payment. Readiness failing with `db unavailable` means Postgres or a NetworkPolicy.
4. Mitigate: roll back, scale up, or fail over. Then find the root cause.

### Pod CrashLoopBackOff
1. `kubectl describe pod`: check *Last State* (reason + exit code) and Events.
   - `OOMKilled` (exit 137): see lab 05.
   - Exit 1: application error.
   - Liveness failures: see lab 01.
2. `kubectl logs POD --previous` shows the logs of the run that crashed.
3. Config? `kubectl get pod POD -o yaml` → env. Did a ConfigMap or Secret change recently?

### Pods Pending
1. `kubectl describe pod`, then Events. Typical messages:
   - `Insufficient cpu`: the autoscaler should add a node. Check `kubectl get nodes`, and look in Logs for `cluster-autoscaler` "noScaleUp" reasons.
   - `didn't tolerate taint`: a missing toleration.
   - `unbound PersistentVolumeClaim`: storage problem.
   - `exceeded quota`: the ResourceQuota is full.

### Node NotReady
1. `kubectl describe node NODE`: look at Conditions (MemoryPressure, DiskPressure…).
2. GKE **auto-repair** recreates unhealthy nodes after ~10 minutes, and pods are rescheduled. Your PDBs and replica spread determine whether users notice.
3. Spot nodes: a reclaim looks exactly like this. That's why only worker and loadgen run there.

## Cost hygiene (a real part of the job)

- `make pause` when not studying.
- Billing → Reports, filtered to Kubernetes Engine and Compute Engine.
- GKE **cost allocation**: Console → Kubernetes Engine → Cost optimization tab. It shows requested vs. used CPU and memory per workload, so you can right-size requests.
- Spot for interruptible work (done). Smaller machine types if the cluster is underutilised.
- Delete unused static IPs and disks. Orphaned PVC disks are a classic leak; check Compute Engine → Disks after teardown.
