# 04 – Curriculum map

Each official curriculum competency is mapped to trainer tasks (`./exam/trainer start <id>`), labs, and the ShopFleet files where you can see it in use. Bullets are paraphrased from the CNCF curricula (2025 revisions). Re-check the official PDFs before your exam.

## CKAD

| Domain / competency | Trainer tasks | Labs & ShopFleet references |
|---|---|---|
| **Application Design and Build (20%)** | | |
| Define, build and modify container images | ckad-05 | `services/*/Dockerfile` (multi-stage, distroless, non-root) |
| Choose and use the right workload resource (Deployment, DaemonSet, CronJob…) | ckad-03, ckad-04 | `k8s/base/node-agent.yaml` (DaemonSet), `jobs.yaml` |
| Multi-container pod patterns (sidecar, init…) | ckad-01, ckad-02 | frontend `access-log` native sidecar, `wait-for-db` init containers; [lab 07](labs/07-architecture-tour.md) |
| Persistent and ephemeral volumes | ckad-06, ckad-17 | postgres PVC, emptyDir `sizeLimit`, projected volume in `ops-api.yaml` |
| **Application Deployment (20%)** | | |
| Deployment strategies (blue/green, canary) | ckad-08, ckad-09 | `k8s/extras/canary.yaml`, Gateway weights |
| Rolling updates | ckad-07 | maxSurge/maxUnavailable in every Deployment |
| Helm | ckad-10, cka-07 | `charts/shopfleet`; [lab 05](labs/05-helm-and-kustomize.md) |
| Kustomize | ckad-11 | `k8s/base`, `k8s/overlays/*`; [lab 05](labs/05-helm-and-kustomize.md) |
| **Application Observability and Maintenance (15%)** | | |
| API deprecations | ckad-13 | `exam/files/legacy.yaml` |
| Probes and health checks | ckad-12 | startup/readiness/liveness/exec probes across `k8s/base` |
| Built-in CLI tools to monitor apps | ckad-16, cka-29 | `kubectl top`, ops-api Cluster page |
| Container logs | ckad-14, ckad-01 | `kubectl logs --previous`, sidecar logs |
| Debugging | ckad-14, ckad-15, ckad-26 | [../labs/12-debugging.md](../labs/12-debugging.md) |
| **Application Environment, Configuration and Security (25%)** | | |
| Discover and use resources that extend Kubernetes (CRD, operators) | ckad-21, cka-05 | FlashSale CRD and operator; [lab 06](labs/06-crds-and-operators.md) |
| Authentication, authorization and admission control | ckad-19, cka-01 | RBAC in `ops-api.yaml`, Pod Security Admission labels |
| Requests, limits, quotas | ckad-20, ckad-22 | `governance.yaml` |
| ConfigMaps | ckad-17, ckad-14 | `payment-chaos` (file, hot reload) vs `shopfleet-config` (env) |
| Secrets | ckad-17, cka-13 | `jwt-signing-key` mounted as a file |
| ServiceAccounts | ckad-19 | `ops-api`, `flashsale-operator`, `chaos-monkey` SAs |
| SecurityContexts | ckad-18 | every pod runs `restricted` |
| **Services and Networking (20%)** | | |
| NetworkPolicies | ckad-24, cka-16 | `networkpolicies.yaml` |
| Services | ckad-23, ckad-26, cka-17 | headless `node-agent`, `postgres` |
| Ingress | ckad-25 | `charts/shopfleet/templates/ingress.yaml` |

## CKA

| Domain / competency | Trainer tasks | Labs & references |
|---|---|---|
| **Cluster Architecture, Installation and Configuration (25%)** | | |
| Manage RBAC | cka-01, ckad-19 | [../labs/10-rbac-and-network-policy.md](../labs/10-rbac-and-network-policy.md) |
| Prepare infrastructure for installing a cluster | — | `envs/kubeadm-gce/node-init.sh.tpl`; [lab 01](labs/01-kubeadm-install.md) |
| Create and manage clusters with kubeadm | — | [lab 01](labs/01-kubeadm-install.md) (manual mode) |
| Manage the cluster lifecycle (upgrades) | cka-02 | [lab 02](labs/02-cluster-upgrade.md) |
| Highly available control plane | — | [lab 01](labs/01-kubeadm-install.md#ha-control-planes-theory) (theory) |
| Install cluster components with Helm and Kustomize | cka-07 | `envs/common/platform.sh` (metrics-server, Traefik via Helm) |
| Extension interfaces (CNI, CSI, CRI) | cka-06, cka-27 | Calico, local-path, containerd/crictl |
| CRDs and operators | cka-05, ckad-21 | [lab 06](labs/06-crds-and-operators.md) |
| etcd backup and restore | cka-03, cka-04 | [lab 03](labs/03-etcd-backup-restore.md) |
| Static pods | cka-08, cka-22 | [lab 04](labs/04-control-plane-troubleshooting.md) |
| **Workloads and Scheduling (15%)** | | |
| Deployments, rolling updates, rollbacks | ckad-07 | |
| ConfigMaps and Secrets | cka-13, ckad-17 | |
| Workload autoscaling | cka-12 | HPAs in `k8s/base`, VPA in `k8s/extras` |
| Self-healing primitives | ckad-12 | [../labs/01-self-healing-and-probes.md](../labs/01-self-healing-and-probes.md) |
| Pod admission and scheduling (limits, affinity, taints, priority) | cka-09, cka-10, cka-11, ckad-20 | PriorityClasses, spot tolerations, topology spread |
| **Services and Networking (20%)** | | |
| Pod-to-pod connectivity | cka-16, cka-23 | Calico |
| NetworkPolicies | cka-16, ckad-24 | |
| Service types and endpoints | cka-17, ckad-23, ckad-26 | |
| Gateway API | cka-14 | `k8s/extras/gateway.yaml`, chart `gateway.enabled` |
| Ingress controllers and resources | ckad-25 | Traefik |
| CoreDNS | cka-15 | |
| **Storage (10%)** | | |
| StorageClasses and dynamic provisioning | cka-19 | local-path, GKE pd-balanced |
| Volume types, access modes, reclaim policies | cka-18, cka-20 | |
| PVs and PVCs | cka-18, ckad-06 | postgres/redis `volumeClaimTemplates` |
| **Troubleshooting (30%)** | | |
| Clusters and nodes | cka-21, cka-28 | [lab 04](labs/04-control-plane-troubleshooting.md) |
| Cluster components | cka-22, cka-26 | |
| Resource usage | cka-29, cka-24, cka-25 | |
| Container output streams | cka-27, ckad-14 | |
| Services and networking | cka-23, cka-15, ckad-26 | [../labs/12-debugging.md](../labs/12-debugging.md) |
