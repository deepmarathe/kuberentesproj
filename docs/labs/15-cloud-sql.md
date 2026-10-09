# Lab 15 – Migrate Postgres to Cloud SQL

**Scenario:** Running your own database in the cluster means you own backups, HA, upgrades, and tuning. Move it to a managed service with minimal downtime.

**Cost:** the smallest instance (`db-f1-micro`) is ~$0.35/day. Delete it at the end.

## 1. Create the instance (~10 min)

```bash
P=$(gcloud config get-value project); R=us-central1
gcloud services enable sqladmin.googleapis.com
gcloud sql instances create shopfleet-sql --database-version=POSTGRES_16 --edition=ENTERPRISE \
  --tier=db-f1-micro --region=$R --storage-size=10 --storage-auto-increase
gcloud sql databases create shopfleet --instance=shopfleet-sql
gcloud sql users create shop --instance=shopfleet-sql --password='shopfleet-lab-password-change-me'
```

## 2. Identity: let the pods connect via Workload Identity

The Cloud SQL Auth Proxy authenticates with IAM. It needs a Google SA with `roles/cloudsql.client`:

```bash
gcloud iam service-accounts create shopfleet-sql
gcloud projects add-iam-policy-binding $P --role=roles/cloudsql.client \
  --member=serviceAccount:shopfleet-sql@$P.iam.gserviceaccount.com
for ksa in catalog orders worker batch; do
  gcloud iam service-accounts add-iam-policy-binding shopfleet-sql@$P.iam.gserviceaccount.com \
    --role=roles/iam.workloadIdentityUser --member="serviceAccount:$P.svc.id.goog[shopfleet/$ksa]"
done
# the daily-report job runs as "reports", which is already mapped to shopfleet-reports:
gcloud projects add-iam-policy-binding $P --role=roles/cloudsql.client \
  --member=serviceAccount:shopfleet-reports@$P.iam.gserviceaccount.com
```

## 3. Copy the data

Pause writes (the maintenance window starts here), dump, and import:

```bash
kubectl -n loadgen scale deploy/loadgen --replicas=0
kubectl -n shopfleet exec postgres-0 -- pg_dump -U shop -d shopfleet --no-owner --no-acl > /tmp/shopfleet.sql
BUCKET=gs://$P-shopfleet-reports
gcloud storage cp /tmp/shopfleet.sql $BUCKET/migration/shopfleet.sql
SQL_SA=$(gcloud sql instances describe shopfleet-sql --format='value(serviceAccountEmailAddress)')
gcloud storage buckets add-iam-policy-binding $BUCKET --member=serviceAccount:$SQL_SA --role=roles/storage.objectViewer
gcloud sql import sql shopfleet-sql $BUCKET/migration/shopfleet.sql --database=shopfleet --user=shop --quiet
```

## 4. Switch the apps over

Read [k8s/overlays/gke-cloudsql/](../../k8s/overlays/gke-cloudsql/kustomization.yaml). It builds on the GKE overlay and:
- sets `DB_HOST=127.0.0.1`
- adds the **Cloud SQL Auth Proxy as a native sidecar** (`initContainers` + `restartPolicy: Always`) to every DB client, including the Job and the CronJobs
- annotates their KSAs with the `shopfleet-sql` GSA

```bash
OVERLAY=gke-cloudsql ./scripts/deploy.sh
kubectl -n shopfleet get pods                        # DB clients now show READY 2/2
kubectl -n shopfleet logs deploy/orders -c cloud-sql-proxy | tail -3
kubectl -n loadgen scale deploy/loadgen --replicas=1  # end of maintenance window
```

Buy something in the store, then confirm it landed in Cloud SQL:

```bash
gcloud sql connect shopfleet-sql --user=shop --database=shopfleet   # (Cloud Shell) then: select count(*) from orders;
```

## 5. Retire the in-cluster database

```bash
kubectl -n shopfleet scale statefulset postgres --replicas=0
```

Exercise: make this permanent in the overlay (a patch with `$patch: delete` for the postgres StatefulSet and Service), and remember the PVC/disk still exists until you delete it. From now on, deploy with `OVERLAY=gke-cloudsql`.

## Why native sidecars matter

- Classic sidecars (a second entry in `containers:`) have no start order, so the app may start before the proxy is listening.
- In a Job, a classic sidecar never exits, so **the Job never completes**.
- Native sidecars (Kubernetes 1.29+) start *before* the app containers, waiting for their startupProbe, and are stopped automatically when the main containers finish.

## Clean up

```bash
gcloud sql instances delete shopfleet-sql --quiet
./scripts/deploy.sh               # back to the in-cluster postgres overlay (scales it back to 1)
```

## Exam takeaways
- Connecting GKE to Cloud SQL means the Auth Proxy (sidecar) + Workload Identity + `roles/cloudsql.client`. Alternatively, a private IP with the Cloud SQL language connectors.
- Cloud SQL HA = a regional instance with a standby in another zone and automatic failover. Read replicas are for scaling reads, not HA.
- `gcloud sql import/export` go through Cloud Storage, and the *instance's* service account needs bucket access.
