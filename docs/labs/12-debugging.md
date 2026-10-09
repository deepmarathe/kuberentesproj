# Lab 12 – Debugging toolkit: three mystery incidents

Have a friend run the "break" command while you're not looking, or run it yourself and then forget about it for a day. Diagnose using only symptoms. Answers are hidden.

## The method (use it every time)

1. **What's the user-visible symptom?** Which URL, what error, since when?
2. **What changed?** `kubectl rollout history`, recent `kubectl get events`, Cloud Build history, Cloud Audit Logs.
3. **Walk the request path from the outside in:**
   LB → Ingress backends → Service → EndpointSlices → Pods (Ready?) → container logs → dependencies.
4. **Mitigate first** (roll back, scale up), **then** find the root cause.

---

## Mystery 1: "Checkout is broken, everything else works"

Break it: `kubectl -n shopfleet patch service payment -p '{"spec":{"selector":{"app":"paymnt"}}}'`

<details><summary>Hint</summary>Checkout fails with 503 payment_unavailable. Are the payment pods healthy? Is the Service routing to them?</details>

<details><summary>Diagnosis & fix</summary>

```bash
kubectl -n shopfleet get pods -l app=payment                                   # healthy!
kubectl -n shopfleet get endpointslices -l kubernetes.io/service-name=payment  # ENDPOINTS: <unset>
kubectl -n shopfleet get svc payment -o jsonpath='{.spec.selector}'            # {"app":"paymnt"}: typo
kubectl -n shopfleet patch service payment -p '{"spec":{"selector":{"app":"payment"}}}'
```

A Service with **no endpoints** is the #1 "the pods are fine but nothing works" cause: a wrong selector, a wrong `targetPort`, or no Ready pods.
</details>

---

## Mystery 2: Debug a container that has no shell

The payment image is **distroless**: no shell, no curl, no `ls`.

```bash
POD=$(kubectl -n shopfleet get pod -l app=payment -o jsonpath='{.items[0].metadata.name}')
kubectl -n shopfleet exec -it $POD -- sh          # fails: no such file
```

Attach an **ephemeral debug container** that shares the target's process namespace:

```bash
cat > /tmp/debug-profile.json <<'EOF'
{ "securityContext": { "runAsUser": 65532, "runAsNonRoot": true, "allowPrivilegeEscalation": false,
  "capabilities": { "drop": ["ALL"] }, "seccompProfile": { "type": "RuntimeDefault" } } }
EOF
kubectl -n shopfleet debug -it $POD --image=busybox:1.36 --target=payment --profile=restricted --custom=/tmp/debug-profile.json -- sh
# inside:
ps                                            # you can see /payment (PID namespace shared)
wget -qO- localhost:8080/admin/chaos          # same network namespace as the app
cat /proc/1/environ | tr '\0' '\n' | grep -i chaos
```

The custom profile is needed because the namespace enforces Pod Security `restricted`: the debug container must run as non-root too. If your kubectl is too old for `--custom`, temporarily relax the namespace with `kubectl label ns shopfleet pod-security.kubernetes.io/enforce=baseline --overwrite`. Restore `restricted` afterwards. Loosening guardrails "just for debugging" and forgetting is a real-world incident in itself.

---

## Mystery 3: "The load generator says everything fails"

Break it: `kubectl -n loadgen set env deploy/loadgen TARGET_HOST=http://frontend:8080`

<details><summary>Hint</summary>Locust UI → Failures tab. The error is a name-resolution error. Which namespace is loadgen in?</details>

<details><summary>Diagnosis & fix</summary>

Short service names only resolve **within the same namespace**. The search path is `<ns>.svc.cluster.local`. From `loadgen`, `frontend` looks up `frontend.loadgen.svc.cluster.local`, which doesn't exist.

```bash
kubectl -n loadgen exec deploy/loadgen -- cat /etc/resolv.conf     # see the search domains
kubectl -n loadgen exec deploy/loadgen -- python -c "import socket;print(socket.gethostbyname('frontend.shopfleet'))"
kubectl -n loadgen set env deploy/loadgen TARGET_HOST=http://frontend.shopfleet.svc.cluster.local:8080
```
</details>

---

## Everyday tools

```bash
# Reach any ClusterIP service from Cloud Shell
kubectl -n shopfleet port-forward svc/catalog 8081:8080 &
curl -s localhost:8081/admin/info; curl -s localhost:8081/metrics | grep http_requests_total | head

# What exactly is deployed? (rendered from git, before applying)
DRY_RUN=true ./scripts/deploy.sh | less

# What's live, compared with git?
DRY_RUN=true ./scripts/deploy.sh | kubectl diff -f -

# Status details hide in YAML
kubectl -n shopfleet get pod POD -o jsonpath='{.status.containerStatuses[*].lastState}'
kubectl -n shopfleet get deploy catalog -o jsonpath='{.status.conditions}' | python3 -m json.tool

# Who deleted / changed something? (Admin Activity audit logs are always on)
# Logs Explorer:  protoPayload.serviceName="k8s.io" protoPayload.methodName:"delete"
```

## Exam takeaways
- `kubectl describe` (Events), then `kubectl logs` (and `--previous`), then `kubectl get -o yaml` (status). Learn that order.
- `kubectl debug`: ephemeral containers (`--target`), pod copies (`--copy-to`), and node debugging (`kubectl debug node/NODE -it --image=busybox`).
- `kubectl diff` before `apply` in production.
