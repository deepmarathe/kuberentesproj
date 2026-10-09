# Lab 18 – Backup & disaster recovery

**Scenario:** Someone ran `DELETE FROM orders` in production. Or a zone went down. What's your RPO (how much data can you lose) and RTO (how long to recover)? Prove it with a drill. An untested backup is a hope, not a backup.

## 1. Logical backup (pg_dump) → Cloud Storage

```bash
P=$(gcloud config get-value project); TS=$(date +%Y%m%d-%H%M%S)
kubectl -n shopfleet exec postgres-0 -- pg_dump -U shop -d shopfleet --format=custom > /tmp/shopfleet-$TS.dump
gcloud storage cp /tmp/shopfleet-$TS.dump gs://$P-shopfleet-reports/backups/
```

Exercise: turn this into a CronJob. Hints:
- `postgres:16-alpine` has `pg_dump`.
- Upload with Workload Identity: reuse the `reports` KSA, which already has `objectCreator`.
- Watch for the NetworkPolicy (the pod needs the `shopfleet.io/db-client: "true"` label) and Pod Security (run as uid 70).

## 2. Restore drill (into a scratch database, never straight over production)

```bash
kubectl -n shopfleet exec postgres-0 -- createdb -U shop restore_test
kubectl -n shopfleet exec -i postgres-0 -- pg_restore -U shop -d restore_test --no-owner < /tmp/shopfleet-$TS.dump
kubectl -n shopfleet exec postgres-0 -- psql -U shop -d restore_test -c "select status, count(*) from orders group by 1;"
kubectl -n shopfleet exec postgres-0 -- dropdb -U shop restore_test
```

Time it. That's your RTO for this method. Your RPO is the time since the last dump.

## 3. Disk snapshot (crash-consistent, whole volume)

```bash
kubectl apply -f - <<'EOF'
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata: { name: pd-snapshots }
driver: pd.csi.storage.gke.io
deletionPolicy: Delete
---
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata: { name: postgres-snap-1, namespace: shopfleet }
spec:
  volumeSnapshotClassName: pd-snapshots
  source: { persistentVolumeClaimName: data-postgres-0 }
EOF
kubectl -n shopfleet get volumesnapshot postgres-snap-1 -w          # READYTOUSE: true
gcloud compute snapshots list
```

Restore = a new PVC with `dataSource: { kind: VolumeSnapshot, name: postgres-snap-1, apiGroup: snapshot.storage.k8s.io }`. Exercise: restore into a PVC and attach a temporary postgres pod to it to verify the data.

## 4. Backup for GKE (managed, cluster-level)

Backs up Kubernetes **objects and volume data** on a schedule, and restores to the same or another cluster.

```bash
gcloud services enable gkebackup.googleapis.com
gcloud container clusters update shopfleet --zone us-central1-a --update-addons=BackupRestore=ENABLED
gcloud container backup-restore backup-plans create shopfleet-daily \
  --location=us-central1 \
  --cluster=projects/$P/locations/us-central1-a/clusters/shopfleet \
  --selected-namespaces=shopfleet \
  --include-secrets --include-volume-data \
  --cron-schedule="0 3 * * *" --backup-retain-days=7
gcloud container backup-restore backups create manual-1 --backup-plan=shopfleet-daily --location=us-central1 --wait-for-completion
```

Then explore Console → Kubernetes Engine → Backup for GKE → create a restore plan and restore into a new namespace. (Flags evolve. If one is rejected, `gcloud container backup-restore backup-plans create --help` is authoritative.)

## 5. Think through DR scenarios

| Failure | Impact on ShopFleet as built | Better design |
|---|---|---|
| One node dies | Brief blip. Pods reschedule, PDB + 2 replicas cover stateless apps. Postgres down ~1 min | Cloud SQL HA |
| Zone outage | **Full outage**: zonal cluster, zonal disks | Regional cluster (nodes in 3 zones) + regional PD or Cloud SQL HA |
| Region outage | Full outage | Multi-region: second cluster + cross-region DB replica, global LB, runbook |
| Bad deploy | Rollback in seconds | (already good) |
| Data deleted by a human | Restore from the last dump or snapshot: RPO = backup age | PITR (Cloud SQL point-in-time recovery) |
| Project deleted | Everything gone (30-day recovery window for projects) | Backups in a *separate* project, IaC to rebuild |

Rebuild drill (the ultimate IaC test): `make teardown`, then `./scripts/bootstrap.sh && ./scripts/build-and-deploy.sh`, then restore data from GCS. How long did it take end to end? That's your RTO for "lost the whole cluster".

## Exam takeaways
- Know RPO vs RTO and which mechanisms improve which.
- Zonal vs. regional clusters: availability vs. cost (a regional cluster runs nodes in each zone, so node count is ×3).
- Snapshots are crash-consistent. Logical dumps are application-consistent and portable across versions.
