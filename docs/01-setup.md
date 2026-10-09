# 01 – Setup: from empty project to running store

Everything below runs in **Google Cloud Shell**, so you install nothing locally.

## 0. Prerequisites

- A GCP project with **billing enabled**. Check: Console → Billing → "My projects".
- You're **Owner** on the project. A personal account is fine.
- Strongly recommended: Billing → Budgets & alerts → create a budget (e.g. $50) with email alerts.

## 1. Get the code into Cloud Shell

Open https://shell.cloud.google.com. Pick one option:

- **GitHub (recommended, and needed for lab 13 CI/CD):** push this folder to a new private GitHub repo, then run `git clone https://github.com/YOU/shopfleet && cd shopfleet`.
- **Upload:** zip the folder on your computer, then in Cloud Shell use ⋮ → Upload, followed by `unzip shopfleet.zip && cd shopfleet`.

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud config set compute/zone us-central1-a      # optional default
chmod +x scripts/*.sh
```

> Cloud Shell sessions time out after ~1 hour idle, and `$HOME` (5 GB) persists. When you come back, run `make creds` to reconnect kubectl.

## 2. Create the infrastructure: `./scripts/bootstrap.sh`

What happens (read [terraform/main.tf](../terraform/main.tf) while it runs):

1. Enables APIs: GKE, Compute, Artifact Registry, Cloud Build, Monitoring, Logging, IAM, Storage.
2. Creates a **VPC** with a subnet plus secondary ranges for pods (`10.20.0.0/16`) and services (`10.30.0.0/20`).
3. Creates **Cloud Router + Cloud NAT**, because private nodes have no public IP.
4. Creates a **GKE Standard zonal cluster** with private nodes, Workload Identity, Dataplane V2, Managed Prometheus, the REGULAR release channel, and a maintenance window.
5. Creates two **node pools**: `general` (2 nodes, autoscale 1–4) and `spot` (1 node, autoscale 0–3, tainted).
6. Creates the **Artifact Registry** repo, the **reports bucket**, and **service accounts**: nodes, reports (Workload Identity), and Cloud Build.
7. Creates **alert policies** and an **uptime check**.
8. Runs `gcloud container clusters get-credentials`, which writes `~/.kube/config` so kubectl talks to your cluster.

It takes about 10–15 minutes. Afterwards:

```bash
kubectl get nodes -L pool          # 3 nodes: 2 general, 1 spot
kubectl get ns                     # gmp-system = Managed Prometheus, kube-system = GKE add-ons
```

> The "storefront is down" uptime alert will fire until step 3 finishes. Nothing is serving on the IP yet, so that's expected.

<details><summary>Troubleshooting</summary>

| Error | Fix |
|---|---|
| `API ... has not been used in project` / `SERVICE_DISABLED` | API enablement takes a minute to propagate. Re-run `./scripts/bootstrap.sh` (Terraform is idempotent) |
| `Quota 'CPUS' exceeded` / `IN_USE_ADDRESSES` | IAM & Admin → Quotas, or use a smaller region load: `general_max_nodes = 2` in `terraform/terraform.tfvars` |
| `billing account ... is disabled` | Link a billing account to the project |
| Constraint violation (org policy) | Your organization restricts something, e.g. external IPs or SA creation. Use a personal project |
| `Error acquiring the state lock` | A previous apply was interrupted: `terraform force-unlock LOCK_ID` |
</details>

## 3. Build and deploy: `./scripts/build-and-deploy.sh`

This runs [cloudbuild.yaml](../cloudbuild.yaml) in **Cloud Build**:

1. Uploads the source, minus anything in `.gcloudignore`.
2. Builds 8 images **in parallel** and pushes them to Artifact Registry, tagged with a timestamp.
3. Runs [scripts/deploy.sh](../scripts/deploy.sh) inside the build:
   - renders `k8s/overlays/gke` with your registry, tag, and project
   - `kubectl apply`
   - waits for Postgres → the migration Job → each Deployment rollout

Watch it in Console → Cloud Build → History. Then:

```bash
./scripts/status.sh
kubectl -n shopfleet get pods -w        # Ctrl-C to stop watching
kubectl -n shopfleet get ingress        # ADDRESS = your static IP
make ip
```

The **load balancer takes 5–10 minutes** after the first deploy. Until then you'll see 404/502 from Google's front end. That's normal: the health checks are warming up. Check progress with:

```bash
kubectl -n shopfleet describe ingress shopfleet     # look at Events + backend annotations: HEALTHY/UNHEALTHY/Unknown
```

<details><summary>Troubleshooting</summary>

| Symptom | Likely cause / fix |
|---|---|
| Cloud Build: `does not have permission to act as service account` | Your user needs `roles/iam.serviceAccountUser` on `shopfleet-cloudbuild`. Owners have it; others need to grant it |
| Cloud Build: `PERMISSION_DENIED` on `storage.objects.get` | IAM propagation delay right after Terraform. Wait 2 minutes and retry |
| Pods `ImagePullBackOff` | `kubectl describe pod` → is the image path right? Does the node SA have `artifactregistry.reader`? |
| Pods `Pending` | `kubectl describe pod` → Events. Usually insufficient CPU, and the autoscaler will add a node in ~1–2 min |
| `db-migrate` Job not completing | `kubectl -n shopfleet logs job/db-migrate`. Usually Postgres isn't ready yet; it retries automatically |
| Ingress has no ADDRESS after 10 min | `kubectl -n shopfleet describe ingress shopfleet` → Events. Was the static IP `shopfleet-ip` created? |
| Pod rejected: `violates PodSecurity "restricted"` | You added something that runs as root or keeps capabilities. Read the message; it lists the exact fields |
</details>

## 4. Look around: your first 15 minutes as the cluster's operator

1. Open the store at `make ip` and buy something. Watch the **Under the hood** panel: it shows which **pod** served each call. Refresh a few times and the pod names change. That's the Service/LB distributing traffic.
2. In **Orders**, watch your order go PAID → FULFILLING → SHIPPED. The worker did that asynchronously.
3. Look at the load generator:
   ```bash
   kubectl -n loadgen port-forward svc/loadgen 8089:8089
   ```
   In Cloud Shell, use **Web Preview** (top right) → "Change port" → 8089.
4. Console → Kubernetes Engine → Workloads: the same information as `kubectl get deploy`, in a GUI.
5. Console → Logging → Logs Explorer, then query:
   ```
   resource.type="k8s_container"
   resource.labels.namespace_name="shopfleet"
   jsonPayload.message="checkout"
   ```
6. Console → Monitoring → Metrics Explorer → **PromQL** tab:
   ```
   sum by (result) (rate(orders_checkout_total[5m]))
   ```

You're now running a production-shaped system. Continue with [02 – Day-2 operations](02-day2-operations.md), then the [labs](labs/README.md).

## Pausing and tearing down

```bash
make pause      # nodes → 0 (keeps cluster, disks, data). ~$1/day remains.
make resume     # nodes back; pods reschedule; data still there (PVCs!)
make teardown   # delete everything. Deletes Ingress + namespaces first so the LB and disks are released.
```

## Alternative: local, free

- **Docker only:** run `docker compose up --build`, then open http://localhost:8080.
- **kind (Kubernetes in Docker):** run `./scripts/local-kind.sh`. This gives you real Kubernetes for kubectl practice. GKE-specific pieces are skipped: LB, Workload Identity, Spot, and Managed Prometheus.
