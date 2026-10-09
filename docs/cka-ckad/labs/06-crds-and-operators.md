# Lab 06 – CRDs & operators: inside the FlashSale operator

ShopFleet extends Kubernetes with its own resource type. You create a `FlashSale` object, and an operator turns it into real discounts in the store. That's the operator pattern, and both exams now test it.

## 1. The CRD

```bash
kubectl get crd flashsales.shopfleet.io
kubectl api-resources | grep -i flash          # short name fs, namespaced, group shopfleet.io
kubectl explain flashsale.spec                 # documentation generated from the schema
kubectl explain flashsale.spec.discountPercent
```

Open [k8s/base/crd-flashsale.yaml](../../../k8s/base/crd-flashsale.yaml) and find:
- `names` (plural, singular, kind, shortNames, categories: try `kubectl get shopfleet`)
- `versions[].schema.openAPIV3Schema`: types, `enum`, `minimum/maximum`, `required`, `default`
- `x-kubernetes-validations`: **CEL rules** evaluated by the API server
- `subresources.status`: status is written separately from spec
- `additionalPrinterColumns`: what `kubectl get fs` shows

Try invalid objects. The API server rejects them before any controller sees them:

```bash
kubectl -n shopfleet apply -f - <<'EOF'
apiVersion: shopfleet.io/v1alpha1
kind: FlashSale
metadata: { name: bad }
spec: { category: Grocery, discountPercent: 70, endsAt: "2030-01-01T00:00:00Z" }
EOF
# -> grocery discounts are capped at 50%     (CEL)
```

## 2. The operator at work

```bash
kubectl -n shopfleet logs -l app=flashsale-operator -f --prefix &       # watch it
kubectl -n shopfleet apply -f k8s/examples/flashsale.yaml
kubectl -n shopfleet get fs -w                                          # Phase: Active
kubectl -n shopfleet describe fs book-week                              # Events written by the operator
```

Open the store: Books are 30% off, with a banner and countdown. The operator called catalog's internal API.

Read [services/operator/operator.py](../../../services/operator/operator.py). In about 300 lines it covers:

| Concept | In the code |
|---|---|
| list + watch, resume from `resourceVersion`, re-list on 410 Gone | `watch()`, `resync()` |
| level-triggered reconcile (periodic resync handles time-based transitions) | `reconcile()` |
| **finalizer** to clean up external state before deletion | `FINALIZER`, the deletion branch |
| status subresource (`PATCH …/status`) | `set_status()` |
| Kubernetes Events | `event()` |
| leader election with a Lease | `Leader` |
| RBAC it needs (incl. `flashsales/status`, `leases`, `events`) | [k8s/base/operator.yaml](../../../k8s/base/operator.yaml) |

## 3. Experiments

**Finalizers in action (a classic exam trap):**

```bash
kubectl -n shopfleet scale deploy flashsale-operator --replicas=0
kubectl -n shopfleet delete fs book-week          # hangs! object has deletionTimestamp but a finalizer
kubectl -n shopfleet get fs book-week -o jsonpath='{.metadata.finalizers}'   # Ctrl-C the delete first
# Option A (correct): bring the operator back -> it cleans up and removes the finalizer
kubectl -n shopfleet scale deploy flashsale-operator --replicas=2
# Option B (emergency only): remove the finalizer by hand -- the discount stays in catalog (orphaned state!)
#   kubectl -n shopfleet patch fs book-week --type=merge -p '{"metadata":{"finalizers":[]}}'
```

The same mechanism explains namespaces stuck in `Terminating` and PVCs stuck because of `kubernetes.io/pvc-protection`.

**Leader election:**

```bash
kubectl -n shopfleet get lease flashsale-operator -o jsonpath='{.spec.holderIdentity}'; echo
kubectl -n shopfleet delete pod <that pod>
# ~15 s later the other replica holds the lease (leaseTransitions +1)
```

**Time-based reconcile:** create a sale that starts in 2 minutes (Admin page → "Starts in"). Watch it go `Scheduled → Active` with no event from you. That's the periodic resync.

**RBAC:** remove `patch` on `flashsales/status` from the operator's Role. Reconciles now log 403 errors, and the phase stops updating. Restore it with `helm upgrade --reuse-values` or `./scripts/deploy.sh`.

Trainer: ckad-21, cka-05.
