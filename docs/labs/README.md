# Labs: break it, watch it, fix it, explain it

Each lab is a realistic incident or task a cloud engineer handles, built on ShopFleet. Do them in order the first time. Later, use `./scripts/chaos.sh` to set up incidents for yourself, and practise diagnosing them **before** reading the lab.

| # | Lab | You practise | Time |
|---|---|---|---|
| 01 | [Self-healing & probes](01-self-healing-and-probes.md) | ReplicaSets, restarts vs. reschedules, readiness vs. liveness, CrashLoopBackOff | 30m |
| 02 | [Autoscaling with HPA](02-autoscaling-hpa.md) | HPA math, metrics-server, scale-up/down behaviour, flash-sale traffic | 30m |
| 03 | [Payment outage](03-payment-outage.md) | Retries, timeouts, circuit breakers, ConfigMap hot reload, alerting | 40m |
| 04 | [Rollouts & rollbacks](04-rollouts-and-rollbacks.md) | Rolling updates, `rollout undo`, broken images/config, canary, blue/green | 45m |
| 05 | [OOMKilled](05-oomkilled.md) | Requests vs. limits, QoS classes, exit code 137, right-sizing | 25m |
| 06 | [Dependency failure](06-dependency-failure.md) | StatefulSets, PVC persistence, readiness design, degraded modes | 40m |
| 07 | [Worker backlog](07-worker-backlog.md) | Queue consumers, HPA vs. manual scaling, SIGTERM, Spot, dead letters | 40m |
| 08 | [Capacity planning](08-capacity.md) | Cluster Autoscaler, Pending pods, quotas, priority/preemption, PSA, VPA | 45m |
| 09 | [Workload Identity](09-workload-identity.md) | KSA ↔ GSA, metadata server, 403 debugging, Terraform drift | 30m |
| 10 | [RBAC & NetworkPolicy](10-rbac-and-network-policy.md) | IAM vs. RBAC, `auth can-i`, impersonation, policy debugging | 45m |
| 11 | [Secrets & config](11-secrets-and-config.md) | ConfigMap/Secret mechanics, rotation pitfalls, Secret Manager | 40m |
| 12 | [Debugging toolkit](12-debugging.md) | Mystery incidents, ephemeral containers, DNS, port-forward | 45m |
| 13 | [CI/CD](13-cicd.md) | Cloud Build triggers, image tags, vulnerability scanning, authorized networks | 45m |
| 14 | [HTTPS, Gateway API, CDN](14-https-gateway-cdn.md) | Managed certs, Gateway API, weighted canary, Cloud CDN | 60m |
| 15 | [Move to Cloud SQL](15-cloud-sql.md) | Managed DB migration, Auth Proxy native sidecar, WI | 60m |
| 16 | [Upgrades & node maintenance](16-upgrades-and-nodes.md) | Drain, PDBs, surge upgrades, node pool migration, release channels | 45m |
| 17 | [Observability & SLOs](17-observability-slos.md) | Dashboards, log-based metrics, SLOs, burn-rate alerts | 45m |
| 18 | [Backup & disaster recovery](18-backup-dr.md) | pg_dump, volume snapshots, Backup for GKE, RPO/RTO | 45m |

## Ground rules

- Every chaos scenario is reversible. If you get lost, `./scripts/deploy.sh` re-applies the known-good state from git.
- **Before fixing anything, write down:**
  1. the symptom
  2. how you'd detect it in production
  3. the root cause
  4. the fix
  5. how to prevent it

  That's a postmortem, and the habit is what turns labs into skill.
- Keep two terminals open (Cloud Shell supports tabs). Use one to **watch** (`kubectl get pods -w`) and one to **act**.

## Watch commands to keep open

```bash
watch -n2 'kubectl -n shopfleet get pods -o wide; echo; kubectl -n shopfleet get hpa'
kubectl -n shopfleet get events -w
kubectl -n shopfleet logs -l app=orders -f --prefix | grep -v '"severity": "INFO"'
```
