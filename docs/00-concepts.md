# 00 – Concepts primer: Docker → Kubernetes → GKE

This page assumes you know nothing yet. Each concept points at the real file in this repo where it's used. Read the concept, then open the file.

---

## 1. Containers (Docker)

**Problem:** "It works on my machine." Apps depend on a specific runtime (Python 3.12, Node 22), libraries, and OS packages.

**Solution:** a **container image** packages the app *and* everything it needs into one immutable artifact. A **container** is a running instance of an image: an isolated process on a shared Linux kernel. It's lighter than a VM because it has no guest OS.

| Term | Meaning | Where |
|---|---|---|
| `Dockerfile` | Recipe to build an image | [services/catalog/Dockerfile](../services/catalog/Dockerfile) |
| Layer | Each instruction = a cached layer. We `COPY requirements.txt` *before* the code so dependency installs are cached when only code changes | same file |
| Multi-stage build | Compile in a big image, copy only the binary into a tiny one | [services/payment/Dockerfile](../services/payment/Dockerfile) (golang → distroless, ~10 MB) |
| Distroless | Image with no shell or package manager: smaller attack surface, but you can't `exec` into it | payment |
| Non-root | `USER 10001`. If the app is compromised, the attacker isn't root | every Dockerfile |
| Registry | Where images live. We use **Artifact Registry** | `terraform/main.tf` |
| Tag | Version label of an image (`catalog:20250101-120000`). Never deploy `:latest` in production | `scripts/build-and-deploy.sh` |

Try it locally (if you have Docker): `docker compose up --build` runs all of ShopFleet on one machine. Compare [docker-compose.yml](../docker-compose.yml) with `k8s/base/`. Compose starts containers. Kubernetes keeps them running, scales them, heals them, and networks them across many machines.

---

## 2. Kubernetes: the core idea

Kubernetes is a **desired-state engine**. You declare *what* you want in YAML ("3 replicas of catalog, each with 100m CPU"). **Controllers** loop forever, comparing desired state with actual state and fixing the difference. Delete a pod and it comes back. Kill a node and its pods are rescheduled. That's *reconciliation*, and almost every lab is about watching it happen.

```
kubectl apply -f x.yaml ──▶ API server ──▶ etcd (stores desired state)
                                 ▲
     controllers (Deployment, ReplicaSet, HPA, Job...) watch & act
     scheduler picks a node for each new pod
     kubelet on each node starts containers and runs probes
```

### Objects you'll use daily

| Object | What it does | ShopFleet example |
|---|---|---|
| **Pod** | Smallest unit: 1+ containers sharing network/storage. Pods are *cattle*: disposable, replaced rather than repaired | every app |
| **Deployment** | Manages ReplicaSets → Pods for stateless apps. Handles rolling updates and rollbacks | [k8s/base/catalog.yaml](../k8s/base/catalog.yaml) |
| **ReplicaSet** | "Keep N identical pods running." Created by Deployments; one per version (that's how `rollout undo` works) | auto |
| **StatefulSet** | Like a Deployment, but pods get stable names (`postgres-0`) and their *own* persistent disk | [k8s/base/postgres.yaml](../k8s/base/postgres.yaml) |
| **Service** | Stable virtual IP + DNS name (`catalog`) that load-balances to the pods matching a label selector | every `*.yaml` with `kind: Service` |
| **Ingress** | HTTP(S) routing from outside the cluster. On GKE this creates a Google Cloud Load Balancer | [k8s/overlays/gke/ingress.yaml](../k8s/overlays/gke/ingress.yaml) |
| **ConfigMap / Secret** | Configuration and credentials, injected as env vars or mounted files | [k8s/base/kustomization.yaml](../k8s/base/kustomization.yaml) |
| **PersistentVolumeClaim** | "I need 5Gi of disk." GKE provisions a Persistent Disk automatically | postgres `volumeClaimTemplates` |
| **Job / CronJob** | Run-to-completion work, once or on a schedule | [k8s/base/jobs.yaml](../k8s/base/jobs.yaml) |
| **HorizontalPodAutoscaler** | Adds/removes pods based on metrics (CPU here) | bottom of `catalog.yaml` |
| **PodDisruptionBudget** | "Never voluntarily take me below N pods" (protects you during node drains/upgrades) | `catalog.yaml` |
| **NetworkPolicy** | Firewall rules between pods | [k8s/base/networkpolicies.yaml](../k8s/base/networkpolicies.yaml) |
| **ServiceAccount + Role + RoleBinding** | Identity for pods, and RBAC permissions | `jobs.yaml` (chaos-monkey), [rbac.yaml](../k8s/base/rbac.yaml) |
| **Namespace** | Folder for objects; scope for RBAC, quotas, policies | `shopfleet`, `loadgen` |
| **ResourceQuota / LimitRange** | Caps and defaults for resource requests in a namespace | [governance.yaml](../k8s/base/governance.yaml) |

### Labels and selectors: the glue

Nothing in Kubernetes references pods by name. A Service finds its pods with `selector: {app: catalog}`. A Deployment owns pods with matching labels. NetworkPolicies select pods by label (`shopfleet.io/db-client: "true"`). If your labels are wrong, things silently don't connect. Lab 10 shows this.

### Requests and limits

- **request** = what the scheduler *reserves* for you. It decides which node you fit on, and the HPA computes % against it.
- **limit** = the hard cap. Exceed the **memory** limit and the container is **OOMKilled**. Exceed a CPU limit and you're throttled. We deliberately set no CPU limits; see the day-2 doc.

### Probes

- **startupProbe**: "Has it finished starting?" Liveness and readiness wait until it passes.
- **readinessProbe**: "Can it take traffic?" Failing removes the pod from Service endpoints. It is **not** restarted.
- **livenessProbe**: "Is it hung?" Failing **restarts** the container. Never check dependencies here. If the DB blips, every pod restarts at once.

### Kustomize

`k8s/base` holds environment-neutral YAML. `k8s/overlays/gke` patches it for GKE: image names, Ingress, Workload Identity annotations. `kubectl kustomize k8s/overlays/gke` shows the final rendered YAML. Built into `kubectl -k`.

---

## 3. GKE: what Google adds

| GKE feature | Why it matters | Where |
|---|---|---|
| **Managed control plane** | Google runs the API server/etcd and upgrades them | — |
| **Standard vs Autopilot** | Standard (used here): you manage node pools. Autopilot: Google manages nodes and you pay per pod. Know the trade-offs for the exam | `terraform/main.tf` |
| **Node pools** | Groups of identical VMs. We have `general` (on-demand) and `spot` (cheap, can be reclaimed, tainted) | `terraform/main.tf` |
| **Cluster Autoscaler** | Adds nodes when pods are Pending, removes underused nodes | node pool `autoscaling {}` |
| **VPC-native networking** | Pods get real VPC IPs from a secondary range, so the LB can target pods directly (NEGs) | subnet `secondary_ip_range` |
| **Private nodes + Cloud NAT** | Nodes have no public IPs; NAT provides outbound internet for image pulls | `private_cluster_config`, `google_compute_router_nat` |
| **GKE Ingress** | Creates a global external Application Load Balancer from an Ingress object | `ingress.yaml`, `backendconfigs.yaml` |
| **Workload Identity** | Pods act as Google service accounts without key files | lab 09 |
| **Dataplane V2** | eBPF networking; enforces NetworkPolicy | `datapath_provider` |
| **Managed Prometheus** | Scrapes `/metrics` via `PodMonitoring` objects | `podmonitoring.yaml` |
| **Cloud Logging** | Container stdout → Logs Explorer automatically. JSON lines become structured fields | every service logs JSON |
| **Release channels + maintenance windows** | Automatic, controlled upgrades | `release_channel`, `maintenance_policy` |
| **Artifact Registry** | Private image registry with IAM | `google_artifact_registry_repository` |
| **Cloud Build** | Serverless CI: builds images without local Docker | `cloudbuild.yaml` |

IAM vs RBAC, which is a very common exam point:
- **IAM** (Google) controls who can reach the cluster and manage it via the GCP API (`roles/container.developer`, `container.admin`, …).
- **RBAC** (Kubernetes) controls what they can do *inside* it.

Both apply.

Next: [01 – Setup](01-setup.md)
