# Lab 10 – RBAC & NetworkPolicy: who can do what, and who can talk to whom

## Part A – RBAC

**Scenario:** A new developer and an on-call contractor join. Give them exactly the access they need, and prove it.

### 1. Make yourself cluster-admin inside Kubernetes

As project Owner you can already do almost anything through IAM, but **impersonation** (`--as`) needs explicit RBAC:

```bash
kubectl create clusterrolebinding me-cluster-admin --clusterrole=cluster-admin --user="$(gcloud config get-value account)"
```

### 2. Inspect what a ServiceAccount can do

```bash
kubectl auth can-i --list -n shopfleet --as=system:serviceaccount:shopfleet:chaos-monkey
kubectl auth can-i delete pods        -n shopfleet --as=system:serviceaccount:shopfleet:chaos-monkey   # yes
kubectl auth can-i delete deployments -n shopfleet --as=system:serviceaccount:shopfleet:chaos-monkey   # no
kubectl auth can-i get secrets        -n shopfleet --as=system:serviceaccount:shopfleet:catalog        # no
```

That's least privilege: the chaos monkey can list and delete pods in one namespace, and nothing else.

### 3. Grant a human a Role

```bash
kubectl auth can-i get pods -n shopfleet --as=alice@example.com              # no
kubectl -n shopfleet create rolebinding alice-dev --role=developer --user=alice@example.com
kubectl auth can-i get pods        -n shopfleet --as=alice@example.com       # yes
kubectl auth can-i create pods/exec -n shopfleet --as=alice@example.com      # yes
kubectl auth can-i get secrets     -n shopfleet --as=alice@example.com       # no
kubectl auth can-i get pods        -n kube-system --as=alice@example.com     # no: namespaced Role
kubectl get pods -n shopfleet --as=alice@example.com                          # actually do it
```

Read [k8s/base/rbac.yaml](../../k8s/base/rbac.yaml): `oncall-viewer` is read-only, and `developer` can deploy, exec, and port-forward.

### 4. IAM vs RBAC on GKE

For a real colleague to reach the cluster, they need **both**:
1. **IAM**: at least `roles/container.clusterViewer` to run `gcloud container clusters get-credentials` and authenticate.
2. **RBAC**: a RoleBinding granting rights inside the namespace.

GKE authorises a request if **either** IAM or RBAC allows it. So `roles/container.developer` in IAM already grants broad read/write in *all* namespaces. For fine-grained, per-namespace access, give a minimal IAM role and do the rest with RBAC. For teams, bind **Google Groups** in RBAC (requires "Google Groups for RBAC" set up on the cluster).

Clean up: `kubectl -n shopfleet delete rolebinding alice-dev`

## Part B – NetworkPolicy

**Scenario:** A security review asks, "If the catalog service is compromised, can it call the payment API or the database?" Prove the answers.

### 5. Test the policy matrix

Use Python as an HTTP client inside pods:

```bash
probe='import sys,urllib.request as u
try: print(u.urlopen(sys.argv[1], timeout=3).status)
except Exception as e: print("BLOCKED/FAILED:", e)'

kubectl -n shopfleet exec deploy/orders  -- python -c "$probe" http://payment:8080/healthz   # 200: allowed
kubectl -n shopfleet exec deploy/catalog -- python -c "$probe" http://payment:8080/healthz   # timeout: blocked
kubectl -n shopfleet exec deploy/catalog -- python -c "$probe" http://cart:8080/healthz      # 200: cart is public
```

Read [networkpolicies.yaml](../../k8s/base/networkpolicies.yaml): `default-deny-ingress`, then `payment-from-orders-only`.

### 6. Break the database policy

```bash
./scripts/chaos.sh netpol-break
kubectl -n shopfleet get pods -w        # catalog/orders/worker drop to 0/1 within ~10s
kubectl -n shopfleet logs deploy/catalog | tail -3
```

Symptoms: readiness fails with `db unavailable ... timeout`. There's no "connection refused", only a **timeout**. Silent drops are the signature of a network policy or firewall.

**Turn on policy logging** (Dataplane V2) to *see* the drops:

```bash
kubectl apply -f - <<'EOF'
apiVersion: networking.gke.io/v1alpha1
kind: NetworkLogging
metadata:
  name: default
spec:
  cluster:
    allow: { log: false, delegate: false }
    deny:  { log: true,  delegate: false }
EOF
```

Logs Explorer: `logName:"policy-action"`, then look at `jsonPayload.disposition="deny"`, `jsonPayload.src.pod_name`, and `jsonPayload.dest.pod_name`.

Fix: `./scripts/chaos.sh netpol-fix`

## Exam takeaways
- NetworkPolicies are **allow-lists**, and they're additive. Once any policy selects a pod for Ingress, everything not explicitly allowed is denied.
- They need an enforcing dataplane. On GKE that's Dataplane V2 (default in new clusters) or the legacy Calico "network policy enforcement" option.
- Selectors are labels: a policy with a typo'd label silently matches nothing.
- RBAC: Role/RoleBinding are namespaced, ClusterRole/ClusterRoleBinding are cluster-wide. A RoleBinding can reference a ClusterRole to reuse it in one namespace.

<details><summary>Check yourself</summary>

1. *Default-deny Ingress only. Can catalog still call the internet?* Yes. We set no Egress policy. Add `policyTypes: [Egress]` to restrict it (and remember to allow DNS on port 53!).
2. *Can someone with `pods/exec` read Secrets?* Effectively yes, if a pod mounts them as env vars or files. Exec is a powerful permission.
3. *`kubectl auth can-i` says yes but the action fails with 403. Why?* Admission (e.g. PodSecurity), a validating webhook, or a quota rejected it. RBAC isn't the only gate.
</details>
