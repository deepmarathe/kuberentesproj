# Lab 04 – Rollouts & rollbacks

**Scenario:** You ship many times a day. Some releases are broken. Learn to release safely, spot a bad rollout, and undo it in seconds.

Setup for this lab:

```bash
source scripts/common.sh          # sets REGISTRY, PROJECT_ID...
OLD=$(cat .last-tag); echo "current tag: $OLD"
```

## 1. A normal rolling update

Build a new version without deploying it:

```bash
TAG=v2 DEPLOY=false ./scripts/build-and-deploy.sh
```

Then roll **only catalog**, and record why:

```bash
kubectl -n shopfleet set image deployment/catalog catalog=$REGISTRY/catalog:v2
kubectl -n shopfleet annotate deployment/catalog kubernetes.io/change-cause="catalog v2: lab 04" --overwrite
kubectl -n shopfleet rollout status deployment/catalog
```

**Observe** (in a second terminal):

```bash
kubectl -n shopfleet get rs -l app=catalog -w
```

- A new ReplicaSet scales up one pod at a time (`maxSurge: 1`). The old one scales down only after each new pod is **Ready** (`maxUnavailable: 0`).
- In the store's **Under the hood** panel, `X-Service` shows `catalog@v2` mixing with the old version during the rollout.

```bash
kubectl -n shopfleet rollout history deployment/catalog
```

## 2. Bad image → ImagePullBackOff

```bash
./scripts/chaos.sh bad-image
kubectl -n shopfleet get pods -l app=catalog
```

**Observe:**
- One new pod sits in `ErrImagePull` / `ImagePullBackOff`.
- **Both old pods keep serving**, so there's no outage. That's `maxUnavailable: 0` protecting you.
- `kubectl -n shopfleet rollout status deployment/catalog` hangs. After `progressDeadlineSeconds` (default 600s) the Deployment condition becomes `Progressing=False, reason=ProgressDeadlineExceeded`. CI systems watch for exactly that.
- `kubectl describe pod <new-pod>` → `Failed to pull image ... not found` (or `403` when it's a permissions issue).

Fix: `kubectl -n shopfleet rollout undo deployment/catalog`

## 3. Bad config → stuck rollout

```bash
./scripts/chaos.sh bad-config            # sets DB_HOST=postgres-typo on orders
kubectl -n shopfleet get pods -l app=orders
kubectl -n shopfleet logs <new-orders-pod> | tail -3
kubectl -n shopfleet exec deploy/orders -- python -c "import urllib.request as u;print(u.urlopen('http://localhost:8080/readyz').read())"
```

(That `exec` lands on an *old* pod. Note the difference: old pods are ready, the new one isn't.)

The new pod runs but never becomes Ready (`db unavailable`). The rollout stalls and the old pods carry on. **Readiness probes are your deployment safety net.** Without one, the broken pod would have received traffic immediately.

Fix: `kubectl -n shopfleet rollout undo deployment/orders`

## 4. Canary release

Send ~1/3 of catalog traffic to a new version and compare before promoting.

```bash
CM=$(kubectl -n shopfleet get deploy catalog -o jsonpath='{.spec.template.spec.containers[0].envFrom[0].configMapRef.name}')
sed -e "s|__IMAGE__|$REGISTRY/catalog:v2|" -e "s|__CONFIGMAP__|$CM|" k8s/extras/canary.yaml | kubectl apply -f -
kubectl -n shopfleet get pods -l app=catalog -L track
```

The `catalog` Service selects `app=catalog`, so it now load-balances across 2 stable pods and 1 canary pod. Compare error rates per version:

```promql
sum by (pod) (rate(http_requests_total{service="catalog",status=~"5.."}[5m]))
```

- **Promote:** `kubectl -n shopfleet set image deployment/catalog catalog=$REGISTRY/catalog:v2 && kubectl -n shopfleet delete -f k8s/extras/canary.yaml`
- **Abort:** `kubectl -n shopfleet delete deployment catalog-canary service catalog-canary`

Replica-ratio canaries are coarse: 1 of 3 pods = 33%. For precise percentages, use Gateway API weights (lab 14) or Cloud Deploy.

## 5. Other rollout tools

```bash
kubectl -n shopfleet rollout restart deployment/cart       # re-create pods (e.g. to pick up a changed Secret)
kubectl -n shopfleet rollout pause deployment/cart         # batch several changes...
kubectl -n shopfleet set resources deployment/cart --requests=cpu=60m
kubectl -n shopfleet rollout resume deployment/cart        # ...into a single rollout
kubectl -n shopfleet rollout undo deployment/cart --to-revision=1
```

**Blue/green:** run two complete Deployments (`version: blue` and `version: green`) and switch the **Service selector** from one to the other. That's instant, with an instant switch-back, at double the cost while both run.

Finally, return everything to git's state: `TAG=$OLD ./scripts/deploy.sh`

## Exam takeaways
- `RollingUpdate` (default) vs `Recreate` (kill all, then start: downtime, but never two versions at once, which is useful for some DB migrations).
- `maxSurge` / `maxUnavailable` control speed vs. safety vs. extra capacity.
- `rollout undo` switches back to the previous ReplicaSet, which is why `revisionHistoryLimit` matters.
- Rollback ≠ git revert. After an `undo`, the cluster no longer matches git. Fix git, or the next deploy brings the bug back.

<details><summary>Check yourself</summary>

1. *maxSurge 0 and maxUnavailable 0?* Invalid: the rollout could never make progress.
2. *Your new image works but uses 2× memory. How would a canary catch it?* Compare `kubernetes_io:container_memory_used_bytes` for canary vs. stable pods before promoting.
3. *Why tag images with a timestamp or git SHA instead of `latest`?* So you know exactly what's running, can roll back to a precise version, and so `kubectl set image` actually triggers a rollout (same tag = no change).
</details>
