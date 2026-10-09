# Lab 16 – Upgrades & node maintenance

**Scenario:** A Kubernetes version is reaching end of support, and a node pool needs a new machine type. Do both with zero customer impact.

## 1. Versions and channels

```bash
gcloud container clusters describe shopfleet --zone us-central1-a \
  --format='value(currentMasterVersion,currentNodeVersion,releaseChannel.channel)'
gcloud container get-server-config --zone us-central1-a \
  --flatten=channels --filter='channels.channel=REGULAR' --format='yaml(channels)'
```

- **Release channels** (Rapid / Regular / Stable / Extended) decide *when* GKE upgrades you automatically.
- **Maintenance windows** (ours: weekends 02:00–10:00 UTC) decide *what time*.
- **Maintenance exclusions** block upgrades for up to 30 days, or longer for minor-version exclusions (e.g. Black Friday).

Order is always **control plane first, then nodes**. Nodes may lag the control plane by a couple of minor versions, never lead it.

## 2. Drain a node by hand (what upgrades do to each node)

```bash
kubectl get pods -n shopfleet -o wide           # pick a general node that runs catalog
NODE=<node-name>
kubectl cordon $NODE                            # no new pods
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data
```

**Observe:**
- Pods are **evicted** (not deleted) and recreated elsewhere.
- If both catalog pods were on this node, the drain evicts one, then **waits** for the PDB (`minAvailable: 1`) until the replacement is Ready. Look for `Cannot evict pod as it would violate the pod's disruption budget` while it waits.
- `postgres-0` has no PDB (a single replica can't have a useful one), so it's evicted immediately, giving a **brief outage**. That's the price of a singleton database, and an argument for Cloud SQL (lab 15).

Then run `kubectl uncordon $NODE`.

**Try the trap:** create a PDB for postgres with `minAvailable: 1`, then drain its node. The drain **blocks forever**, because one replica can never be disrupted. GKE's auto-upgrades respect PDBs for up to 1 hour, then proceed anyway.

```bash
kubectl -n shopfleet create pdb postgres --selector=app=postgres --min-available=1
# drain the node with postgres-0 → stuck. Ctrl-C, then:
kubectl -n shopfleet delete pdb postgres; kubectl uncordon <node>
```

## 3. Upgrade a node pool (surge upgrade)

Pick a valid version from step 1. It must be ≤ the control plane version:

```bash
gcloud container clusters upgrade shopfleet --zone us-central1-a --node-pool general --cluster-version VERSION
kubectl get nodes -w
```

With `max_surge = 1, max_unavailable = 0`, GKE adds one new node, cordons and drains one old node, deletes it, and repeats. Capacity never drops, and PDBs and graceful termination keep requests flowing. Keep the store open and the Locust UI running: the error rate should stay ~0.

(**Blue-green** node pool upgrades are another strategy: create the full new pool, migrate in batches, keep the old pool until you're sure. That gives faster rollback at more cost.)

## 4. Change machine type: node pool migration

Machine type is immutable on a pool, so you migrate:

```bash
gcloud container node-pools create general-v2 --cluster shopfleet --zone us-central1-a \
  --machine-type e2-standard-4 --num-nodes 1 --enable-autoscaling --min-nodes 1 --max-nodes 3 \
  --service-account shopfleet-nodes@$(gcloud config get-value project).iam.gserviceaccount.com \
  --workload-metadata=GKE_METADATA --node-labels=pool=general
for n in $(kubectl get nodes -l cloud.google.com/gke-nodepool=general -o name); do kubectl cordon $n; done
for n in $(kubectl get nodes -l cloud.google.com/gke-nodepool=general -o name); do
  kubectl drain $n --ignore-daemonsets --delete-emptydir-data; done
kubectl -n shopfleet get pods -o wide         # all on general-v2 now
```

Now **Terraform has drifted**: it doesn't know about `general-v2`. Either import it, or (simpler for this lab) undo:

```bash
for n in $(kubectl get nodes -l cloud.google.com/gke-nodepool=general -o name); do kubectl uncordon $n; done
gcloud container node-pools delete general-v2 --cluster shopfleet --zone us-central1-a --quiet
```

In real life you'd make this change in `terraform/main.tf` (a new `google_container_node_pool` resource), apply, migrate, then remove the old resource.

## Exam takeaways
- Upgrade order: control plane → node pools. Surge settings control speed vs. capacity.
- `kubectl drain` respects PDBs. `kubectl delete pod` doesn't.
- Zonal clusters have a single control plane, so the API is briefly unavailable during control-plane upgrades (workloads keep running). **Regional** clusters have 3 control-plane replicas, with no API downtime.
- `auto_repair` recreates unhealthy nodes. `auto_upgrade` follows the release channel.
