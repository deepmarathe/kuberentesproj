# Lab 07 – Architecture tour: every exam concept, live in ShopFleet

ShopFleet uses almost every CKAD concept and many CKA ones. For each row, run the command and find the YAML. Then explain out loud why it's there. That's how you make the concepts stick.

## The application

```
browser ──▶ Ingress/Gateway ──▶ frontend (nginx + access-log sidecar)
                │ /api/catalog ─▶ catalog  (products, reviews, flash-sale prices, recommendations)
                │ /api/cart ────▶ cart     (Redis)
                │ /api/orders ──▶ orders   (checkout saga → payment, catalog, Redis stream)
                │ /api/auth ────▶ auth     (bcrypt + JWT; key from a Secret FILE)
                │ /api/notify ──▶ notifications (Server-Sent Events, Redis pub/sub)
                │ /api/ops ─────▶ ops-api  (Kubernetes API via ServiceAccount + RBAC → the Cluster page)
worker (Redis stream consumer) · flashsale-operator (CRD controller, 2 replicas + Lease)
node-agent (DaemonSet + headless Service) · CronJobs: restock, order-reaper, recommender, daily-report, chaos-monkey
Postgres + Redis StatefulSets (PVCs)
```

## Concept → where → command

| Concept | Where in ShopFleet | Look at it |
|---|---|---|
| Init container | `wait-for-db` in catalog/orders/auth/worker | `k -n shopfleet get deploy catalog -o yaml \| grep -A6 initContainers` |
| Native sidecar | frontend `access-log` (initContainer + `restartPolicy: Always`) | `k -n shopfleet logs deploy/frontend -c access-log -f` |
| Shared emptyDir + `sizeLimit` | frontend `logs` volume | exceed it → eviction |
| startup / readiness / liveness | every app; worker uses an **exec** probe | `k describe pod -l app=worker` |
| Graceful shutdown | `preStop: sleep`, SIGTERM handlers; notifications drains SSE clients | `k delete pod -l app=notifications` and watch the browser reconnect |
| ConfigMap as env vs. file | `shopfleet-config` (env, needs restart) vs. `payment-chaos` (file, hot reload) | [../../labs/03-payment-outage.md](../../labs/03-payment-outage.md) |
| Secret as a file | `jwt-signing-key` → `/etc/shopfleet/jwt` | task cka-13 |
| Downward API | `POD_NAME`, `NODE_NAME`, `POD_NAMESPACE` env | the "served by" column in Under the hood |
| Projected volume (SA token + CA + namespace) | `ops-api` | `k -n shopfleet get deploy ops-api -o yaml \| grep -A15 projected` |
| ServiceAccount + Role + ClusterRole + subresources | `ops-api.yaml`, `operator.yaml` | Cluster page → "Who is this dashboard?" |
| automountServiceAccountToken: false | all apps that don't call the API | |
| Pod Security Admission `restricted` | namespace labels; every securityContext | `k get ns shopfleet --show-labels` |
| Requests / limits / QoS | every container | `k get pods -o custom-columns=N:.metadata.name,QOS:.status.qosClass` |
| ResourceQuota / LimitRange | `governance.yaml` | `k describe quota -n shopfleet` |
| PriorityClasses / preemption | critical (DBs, node-agent), standard, low (loadgen, jobs) | `k get pc` |
| Deployment strategy | `maxSurge: 1, maxUnavailable: 0` everywhere | |
| HPA | catalog, cart, orders, auth, frontend, worker | `k get hpa -n shopfleet` |
| PDB | every multi-replica Deployment; operator uses `maxUnavailable` | `k get pdb -n shopfleet` |
| topologySpreadConstraints / podAntiAffinity | apps / operator | |
| StatefulSet + volumeClaimTemplates + headless Service | postgres, redis | `k get sts,pvc -n shopfleet` |
| DaemonSet + tolerate-everything + headless discovery | node-agent | `k -n shopfleet get ds`; Cluster page shows per-node load |
| Job / CronJob options | `db-migrate` (backoffLimit, activeDeadlineSeconds, ttlSecondsAfterFinished), CronJobs (concurrencyPolicy, startingDeadlineSeconds, suspend) | `k get cj -n shopfleet` |
| CRD + operator + finalizer + status + Events + Lease | FlashSale | [lab 06](06-crds-and-operators.md) |
| NetworkPolicies | default-deny + allow-lists (payment ← orders only, postgres ← db-clients) | [../../labs/10-rbac-and-network-policy.md](../../labs/10-rbac-and-network-policy.md) |
| Ingress / Gateway API | chart `ingress.enabled` / `gateway.enabled`, `k8s/extras/gateway.yaml` | |
| Long-lived connections vs. LB timeouts | notifications SSE + `stream` BackendConfig (GKE) / nginx `proxy_read_timeout` | |
| Helm hooks, tests, lookup | `charts/shopfleet` | [lab 05](05-helm-and-kustomize.md) |
| Kustomize generators, patches, overlays | `k8s/` | [lab 05](05-helm-and-kustomize.md) |

## Watch it all together

Log in as admin (the password is in Secret `admin-credentials`) and open **Cluster**. Then, in a terminal:

```bash
./scripts/chaos.sh catalog-crash          # a chip gets ↻1
k -n shopfleet scale deploy cart --replicas=4
k drain <worker> --ignore-daemonsets --delete-emptydir-data     # watch pods move, node turns "cordoned"
k uncordon <worker>
```
