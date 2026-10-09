# 🛳️ ShopFleet: a production-style app for learning Kubernetes, GKE, CKA and CKAD

ShopFleet is a realistic online store: **10 services in 3 languages, a custom operator, and a DaemonSet**. It ships with:

- **its own customers**: a load generator shops, registers, and writes reviews 24/7
- **built-in ways to break it**: `scripts/chaos.sh`
- **an exam trainer with 55 auto-graded CKA/CKAD tasks** that run on the app itself
- **an exam-shaped kubeadm cluster** (`cp1`, `node01`, `node02`) built by Terraform

## Pick your path

| Goal | Start with |
|---|---|
| **Pass CKA / CKAD** | [docs/cka-ckad/README.md](docs/cka-ckad/README.md): environments, 6-week plan, trainer, labs |
| Learn GKE / Google Cloud operations | [docs/01-setup.md](docs/01-setup.md), then [docs/labs/](docs/labs/README.md) (18 incident labs) |
| Just understand the concepts | [docs/00-concepts.md](docs/00-concepts.md) |

## The app

```
                                   Ingress / Gateway API
          ┌──────────┬─────────────┬───────┼───────┬─────────────┬──────────────┐
          ▼          ▼             ▼       ▼       ▼             ▼              ▼
     ┌─────────┐ ┌─────────┐ ┌───────┐ ┌──────┐ ┌──────────┐ ┌─────────────┐ ┌─────────┐
     │frontend │ │ catalog │ │ cart  │ │ auth │ │  orders  │ │notifications│ │ ops-api │
     │nginx SPA│ │ Python  │ │ Node  │ │Python│ │  Python  │ │ Node (SSE)  │ │ Python  │
     │+sidecar │ │ reviews │ │       │ │bcrypt│ │  saga    │ │             │ │ k8s API │
     └─────────┘ │ sales   │ └───┬───┘ │ JWT  │ └──┬───┬───┘ └──────▲──────┘ └────┬────┘
                 └──┬───▲──┘     │     └──┬───┘    │   │ retry+breaker│pub/sub      │ RBAC
                    │   │        │        │        │   ▼             │             ▼
                    │   │internal│        │        │ ┌─────────┐     │      Kubernetes API
                    │   │  API   │        │        │ │ payment │ Go, distroless,       │
                    │   │        │        │        │ └─────────┘ chaos via ConfigMap   │
                    │ ┌─┴────────────────┐│        │                                   │
                    │ │flashsale-operator││        │      ┌─────────────────┐  ┌──────┴─────┐
                    │ │ watches FlashSale││        └─────▶│ Redis: cache,   │  │ node-agent │
                    │ │ CRs (Lease, 2x)  ││   XADD orders │ carts, stream,  │  │ DaemonSet  │
                    │ └──────────────────┘│               │ pub/sub         │  │ (headless) │
                    ▼                     ▼               └────────┬────────┘  └────────────┘
               ┌───────────────────────────────┐          XREADGROUP│
               │ Postgres (StatefulSet + PVC)  │◀──────────── worker (fulfilment, HPA, Spot)
               └───────────────────────────────┘
   CronJobs: restock · order-reaper · recommender · daily-report (Workload Identity → GCS) · chaos-monkey
   Job/Helm hook: db-migrate        loadgen: Locust shoppers (register, browse, review, checkout)
```

**Storefront features:**
- product catalog with search, categories, sorting, ratings, and reviews (marked "verified purchase" when you've bought the item)
- "customers also bought" recommendations
- cart, checkout with live order tracking (PAID → FULFILLING → SHIPPED, pushed over SSE)
- accounts
- flash sales with countdown banners

**Admin pages:**
- stats and a live order feed
- product editing
- a **FlashSale manager** that creates Kubernetes custom resources, showing the equivalent `kubectl` YAML
- a **Cluster page**: a live map of nodes and pods, workloads with restart/scale buttons, HPAs, warning events, and the dashboard's own RBAC permissions

An **Under the hood** panel shows which pod served every request.

## What you'll practise

| Area | In this repo |
|---|---|
| **CKA/CKAD exam tasks** | `exam/trainer`: 26 CKAD + 29 CKA tasks, auto-graded, timed mock exams |
| **Cluster lifecycle** | kubeadm install (auto or by hand), upgrades, etcd backup/restore, certificates, static pods → `envs/kubeadm-gce`, `docs/cka-ckad/labs` |
| **Workloads** | Deployments, StatefulSets, DaemonSet, Jobs, CronJobs, init containers, native sidecars |
| **Extending Kubernetes** | FlashSale CRD (schema + CEL validation) + Python operator (watch, reconcile, finalizers, status, Events, leader election) |
| **Packaging** | Helm chart (loops, helpers, hooks, `lookup`, schema, tests) + Kustomize (base/overlays/generators/patches) |
| **Security** | Pod Security `restricted`, RBAC (Roles, ClusterRoles, subresources), projected SA tokens, NetworkPolicies, Workload Identity, JWT key in a Secret file |
| **Networking** | Services (ClusterIP, NodePort, headless), Ingress, Gateway API, CoreDNS, SSE through load balancers |
| **Scaling & resilience** | HPA, VPA, Cluster Autoscaler, PDBs, priorities, topology spread, graceful shutdown, retries, circuit breaker, DLQ |
| **Observability** | JSON logs, Prometheus metrics, Managed Prometheus, alerts, SLOs, the in-app Cluster view |
| **Cloud** | Terraform (GKE + kubeadm VMs), Artifact Registry, Cloud Build CI/CD, Cloud SQL migration |

## Quick starts

```bash
# CKA/CKAD practice cluster (kubeadm on 3 GCE VMs) -- from Cloud Shell
./envs/kubeadm-gce/up.sh && ./envs/kubeadm-gce/install-app.sh && ./envs/kubeadm-gce/ssh.sh
cd shopfleet && ./exam/trainer list

# Free local cluster (needs Docker + kind)
./envs/kind/up.sh                       # store on http://localhost:8080

# GKE (managed, production-like)
./scripts/bootstrap.sh && ./scripts/build-and-deploy.sh
```

## 💸 Cost

| Environment | Running | Paused |
|---|---|---|
| kubeadm (3 × e2-standard-2) | ~$3/day | ~$0.15/day (`envs/kubeadm-gce/stop.sh`) |
| GKE | ~$4–5/day | ~$1/day (`make pause`) |
| kind | free | – |

Set a budget alert in Billing, and run `down.sh` / `make teardown` when you're done.

## Repo map

```
services/              10 services + migration job + load generator (each with a Dockerfile)
  frontend/            nginx + SPA (ES modules, no build step): shop, product, orders, admin, cluster pages
  catalog/ cart/ orders/ auth/ notifications/ payment/ worker/ ops-api/ operator/ node-agent/
charts/shopfleet/      Helm chart (used by kubeadm/kind environments)
k8s/base, overlays/    Kustomize (used by GKE); k8s/extras: canary, Gateway, HTTPS, VPA; examples/
exam/                  CKA/CKAD trainer, task banks, practice chart, kustomize base, files
envs/                  kubeadm-gce (Terraform + scripts), kind, common/platform.sh (CNI + add-ons)
terraform/             GKE infrastructure          scripts/  deploy, chaos, status, pause, teardown
docs/                  concepts, setup, day-2 ops, GKE labs, cka-ckad/ track
```
