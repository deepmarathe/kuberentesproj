# Lab 09 – Workload Identity: pods calling Google APIs without keys

**Scenario:** The daily sales report must be saved to Cloud Storage. Do it without JSON key files: keys leak, never expire, and are a top cause of cloud breaches.

## How it's wired (read along in the code)

```
CronJob daily-report  →  runs as KSA  shopfleet/reports          (k8s/base/jobs.yaml)
KSA annotation        →  iam.gke.io/gcp-service-account: shopfleet-reports@PROJECT.iam.gserviceaccount.com
                                                                  (k8s/overlays/gke/kustomization.yaml)
IAM binding on GSA    →  roles/iam.workloadIdentityUser for
                         serviceAccount:PROJECT.svc.id.goog[shopfleet/reports]   (terraform/main.tf)
GSA permissions       →  roles/storage.objectCreator on the reports bucket only
Node pool             →  workload_metadata_config.mode = GKE_METADATA
```

The pod asks the metadata server (169.254.169.254) for a token. On GKE that request is intercepted by the **GKE metadata server**, which checks the pod's KSA and returns a short-lived token for the mapped GSA.

## 1. Run the report

```bash
kubectl -n shopfleet create job --from=cronjob/daily-report report-now
kubectl -n shopfleet logs -f job/report-now
gcloud storage ls -r gs://$(gcloud config get-value project)-shopfleet-reports/reports/
gcloud storage cat "gs://$(gcloud config get-value project)-shopfleet-reports/reports/**" | head -40
```

## 2. See identity from inside a pod

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata: { name: whoami, namespace: shopfleet }
spec:
  serviceAccountName: reports
  restartPolicy: Never
  securityContext: { runAsNonRoot: true, runAsUser: 10001, seccompProfile: { type: RuntimeDefault } }
  containers:
    - name: c
      image: python:3.12-slim
      command: ["python", "-c", "import urllib.request as u; r=u.Request('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/email', headers={'Metadata-Flavor':'Google'}); print(u.urlopen(r).read().decode())"]
      securityContext: { allowPrivilegeEscalation: false, capabilities: { drop: ["ALL"] } }
EOF
kubectl -n shopfleet logs whoami        # shopfleet-reports@PROJECT.iam.gserviceaccount.com
kubectl -n shopfleet delete pod whoami
```

Change `serviceAccountName` to `batch` (no annotation) and repeat. You'll get `PROJECT.svc.id.goog`, the workload pool identity with no GCP permissions by default.

## 3. Break it: missing annotation

```bash
kubectl -n shopfleet annotate serviceaccount reports iam.gke.io/gcp-service-account-
kubectl -n shopfleet create job --from=cronjob/daily-report report-broken
kubectl -n shopfleet logs -f job/report-broken
```

**Observe:** `report upload failed ... 403 ... does not have storage.objects.create access`. The job exits 2 and retries per `backoffLimit: 3` (watch `kubectl -n shopfleet get pods -l job-name=report-broken`), then is marked **Failed**.

Fix: `./scripts/deploy.sh` (re-applies the annotation from git).

## 4. Break it: IAM binding removed (and Terraform drift)

```bash
P=$(gcloud config get-value project)
gcloud iam service-accounts remove-iam-policy-binding shopfleet-reports@$P.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser --member "serviceAccount:$P.svc.id.goog[shopfleet/reports]"
kubectl -n shopfleet create job --from=cronjob/daily-report report-broken2
kubectl -n shopfleet logs -f job/report-broken2       # different error: can't get a token at all
```

Fix it the infrastructure-as-code way:

```bash
cd terraform && terraform plan      # shows the missing binding: DRIFT detected
terraform apply
```

Clean up the jobs: `kubectl -n shopfleet delete job report-now report-broken report-broken2`

## Exam takeaways
- Workload Identity is the recommended way for GKE workloads to access Google APIs.
  1. Enable it on the cluster (workload pool).
  2. Set the node pool to `GKE_METADATA`.
  3. Bind KSA → GSA with `roles/iam.workloadIdentityUser`.
  4. Annotate the KSA.
- Newer alternative: grant IAM roles **directly to the KSA principal**, with no GSA needed. The member looks like `principal://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/PROJECT_ID.svc.id.goog/subject/ns/shopfleet/sa/reports`. Know that both exist.
- The **node** service account is a different identity: it's what the kubelet uses to pull images and write logs. Keep it minimal and never let apps borrow it.

<details><summary>Check yourself</summary>

1. *Pod gets a 403 from GCS. List four things to check.*
   - KSA name in the pod spec
   - the annotation on the KSA
   - the workloadIdentityUser binding (exact namespace/KSA)
   - the GSA's role on the bucket
   - (and that the node pool runs GKE_METADATA)
2. *Why is a JSON key in a Kubernetes Secret worse?* It's long-lived, copyable, readable by anyone who can read Secrets, and has to be rotated by hand.
</details>
