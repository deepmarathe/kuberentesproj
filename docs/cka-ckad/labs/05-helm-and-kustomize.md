# Lab 05 – Helm & Kustomize with the ShopFleet chart

ShopFleet ships both ways: **Kustomize** (`k8s/base` + overlays, used for GKE) and **Helm** (`charts/shopfleet`, used for kubeadm and kind). Both exams test both tools.

## Helm: the commands you need

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami && helm repo update
helm search repo nginx                      # find charts;  helm search hub …  for Artifact Hub
helm show values bitnami/nginx | less       # what can I configure?
helm install web bitnami/nginx -n web --create-namespace --set replicaCount=2
helm list -A                                # releases in all namespaces
helm upgrade web bitnami/nginx -n web --reuse-values --set service.type=NodePort
helm history web -n web
helm rollback web 1 -n web
helm get values web -n web [--all]          # what this release is using
helm get manifest web -n web                # the YAML it applied
helm template web bitnami/nginx | less      # render locally, apply nothing
helm uninstall web -n web
```

Exam gotchas:
- **Namespace:** `helm list` with no `-n` only shows the current namespace.
- `--create-namespace` is needed if the namespace doesn't exist.
- `upgrade` without `--reuse-values` **resets everything you didn't set**.
- `--version` pins a chart version (often asked for).

## Reading a real chart: `charts/shopfleet`

| File | Teaches |
|---|---|
| `Chart.yaml` | chart vs. app version, `kubeVersion` constraint |
| `values.yaml` + `values.schema.json` | defaults + validation (`helm install --set apps.cart.replicas=99` fails the schema) |
| `templates/_helpers.tpl` | named templates, `include`, `nindent`, the `lookup` trick that keeps generated passwords stable across upgrades |
| `templates/apps.yaml` | `range` over a map: one template renders 8 services. Also the `checksum/config` annotation that restarts pods when the ConfigMap changes |
| `crds/` | CRDs installed first, and **never upgraded or deleted** by Helm (deliberate: deleting a CRD deletes every custom resource) |
| `templates/jobs.yaml` → db-migrate | **hooks**: `post-install,pre-upgrade`, `hook-delete-policy` |
| `templates/tests/` | `helm test` |
| `templates/NOTES.txt` | what users see after install |

Exercises:

```bash
helm lint charts/shopfleet
helm template sf charts/shopfleet --show-only templates/apps.yaml | grep -c "kind: Deployment"     # 8
helm template sf charts/shopfleet --set apps.cart.replicas=99          # schema error
helm upgrade shopfleet charts/shopfleet -n shopfleet --reuse-values --set config.logLevel=DEBUG
kubectl -n shopfleet get rs        # new ReplicaSets everywhere: the config checksum changed
helm rollback shopfleet -n shopfleet
helm get values shopfleet -n shopfleet --all | head -30
```

## Kustomize: the essentials

```bash
kubectl kustomize k8s/overlays/gke | less     # render
kubectl apply -k k8s/overlays/local           # apply
kubectl diff -k k8s/overlays/local            # what would change
```

`kustomization.yaml` features used in this repo:

| Feature | Where |
|---|---|
| `resources`, `namespace`, `namePrefix`, `labels` | `k8s/base/kustomization.yaml`; task ckad-11 |
| `configMapGenerator` / `secretGenerator` (hash suffix → automatic rollouts) | base |
| `disableNameSuffixHash` (for hot-reloaded files) | `payment-chaos`, `jwt-signing-key` |
| `images` (registry/tag swap per environment) | `overlays/gke`, `overlays/local` |
| `patches` with JSON6902 and target selectors (incl. regex `name: "catalog\|orders"`) | `overlays/gke`, `overlays/gke-cloudsql` |
| overlay-of-an-overlay | `overlays/gke-cloudsql` builds on `overlays/gke` |

Trainer: ckad-10, ckad-11, cka-07.
