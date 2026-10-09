# Lab 11 – Secrets & configuration (and a classic rotation outage)

## 1. How config reaches the pods

```bash
kubectl -n shopfleet get configmaps,secrets
kubectl -n shopfleet get deploy orders -o yaml | grep -A3 -E "envFrom|secretKeyRef"
```

Notice the **hash suffixes** (`shopfleet-config-5m57mf86gk`). Kustomize's generators create a *new* object whenever content changes and rewrite every reference. Changing any value therefore changes the pod template, which **triggers a rolling restart automatically**. That's the reliable way to roll out config that's read from env vars.

`payment-chaos` has *no* hash (`disableNameSuffixHash`), because payment hot-reloads it from a file (lab 03).

## 2. Secrets are not encrypted, just encoded

```bash
S=$(kubectl -n shopfleet get secret -o name | grep postgres-credentials)
kubectl -n shopfleet get $S -o jsonpath='{.data.DB_PASSWORD}' | base64 -d; echo
```

Anyone with `get secrets` RBAC can read it. Protections:
- **etcd encryption at rest**: GKE always encrypts. Add **Application-layer Secrets Encryption** with your own Cloud KMS key for an extra layer (`database_encryption` in Terraform).
- **RBAC**: our `oncall-viewer` and `developer` roles exclude secrets.
- **Don't keep secrets in git.** This repo does, for lab simplicity. Step 5 shows the production way.

## 3. A config change rolls everything that uses it

Edit `k8s/base/kustomization.yaml` and change `LOG_LEVEL=INFO` to `LOG_LEVEL=DEBUG`. Then:

```bash
DRY_RUN=true ./scripts/deploy.sh | grep -m3 "shopfleet-config-"     # new hash
./scripts/deploy.sh
kubectl -n shopfleet get rs        # new ReplicaSets for catalog, cart, orders, payment, worker, frontend
```

Set it back to INFO and redeploy.

## 4. The rotation outage (do this, it's worth it)

Rotate the DB password the naive way. In `k8s/base/kustomization.yaml`, change `DB_PASSWORD=...` to something new, then:

```bash
./scripts/deploy.sh
```

**What happens:**
- The Secret gets a new hash, so every DB client rolls.
- **New pods can't log in.** Postgres still has the *old* password, because `POSTGRES_PASSWORD` is only used when the data directory is first initialised.
- The new pods never become Ready. The rollouts stall, and `deploy.sh` times out.
- **The old pods keep serving**, thanks to `maxUnavailable: 0`. Users are fine. Your safety net worked.
- The postgres pod itself restarted with the new env var, which it ignores. It still accepts the old password.
- The CronJobs and migration Job fail.

**The correct rotation procedure:**
1. Change the password **in the database first**:
   ```bash
   kubectl -n shopfleet exec postgres-0 -- psql -U shop -d shopfleet -c "ALTER USER shop PASSWORD 'the-new-password';"
   ```
   Old pods keep their existing connections. New connections from old pods would now fail.
2. Roll the apps with the new Secret (already applied). The new pods become Ready.

In production you avoid even that brief window with **dual credentials**: create a second user, switch apps over, then drop the old one. Or use **IAM database authentication** (Cloud SQL), which needs no passwords at all.

Revert, or keep the new password. Either way, make git and the database match.

## 5. The production way: Secret Manager (optional)

```bash
P=$(gcloud config get-value project)
gcloud services enable secretmanager.googleapis.com
gcloud container clusters update shopfleet --zone us-central1-a --enable-secret-manager    # GKE add-on, a few minutes
printf 'super-secret-value' | gcloud secrets create shopfleet-demo --data-file=-
PN=$(gcloud projects describe $P --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding shopfleet-demo --role=roles/secretmanager.secretAccessor \
  --member="principal://iam.googleapis.com/projects/$PN/locations/global/workloadIdentityPools/$P.svc.id.goog/subject/ns/shopfleet/sa/batch"
```

```yaml
# kubectl apply -f this (replace PROJECT_ID)
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata: { name: demo, namespace: shopfleet }
spec:
  provider: gke
  parameters:
    secrets: |
      - resourceName: "projects/PROJECT_ID/secrets/shopfleet-demo/versions/latest"
        path: "demo.txt"
---
apiVersion: v1
kind: Pod
metadata: { name: secret-demo, namespace: shopfleet }
spec:
  serviceAccountName: batch
  restartPolicy: Never
  securityContext: { runAsNonRoot: true, runAsUser: 10001, seccompProfile: { type: RuntimeDefault } }
  containers:
    - name: c
      image: python:3.12-slim
      command: ["python", "-c", "print(open('/secrets/demo.txt').read())"]
      securityContext: { allowPrivilegeEscalation: false, capabilities: { drop: ["ALL"] } }
      volumeMounts: [{ name: s, mountPath: /secrets, readOnly: true }]
  volumes:
    - name: s
      csi: { driver: secrets-store-gke.csi.k8s.io, readOnly: true, volumeAttributes: { secretProviderClass: demo } }
```

`kubectl -n shopfleet logs secret-demo` prints the value. The secret never existed as a Kubernetes Secret object, and access is audited in Cloud Audit Logs. (If the add-on's API has changed since this was written, check "Use Secret Manager add-on with GKE" in the GKE docs.)

## Exam takeaways
- ConfigMap/Secret via **env** = read at start. Via **volume** = updates live (not with `subPath`).
- `kubectl create secret generic NAME --from-literal=k=v` / `--from-file`. Values are base64 in YAML `data:`, plain text in `stringData:`.
- Changing a Secret does **not** restart pods. Use generator hashes, a checksum annotation, or `kubectl rollout restart`.
- Rotating a credential means changing *both* sides, in the right order.
