# 01 – Practice environments

| | **kubeadm on GCE** (recommended) | **kind** (free, local) | **GKE** (the main ShopFleet setup) |
|---|---|---|---|
| Looks like the exam | ✅ cp1/node01/node02, `ssh node01`, systemd kubelet, containerd | ⚠️ nodes are Docker containers (`docker exec` instead of ssh) | ❌ no control-plane access |
| CKAD tasks | all | all | all except ckad-05 |
| CKA tasks | all 29 | all except kubeadm-specific (upgrade, etcd restore, certs) | none of the node-level ones |
| Cost | ~$3/day running, ~$0.15/day stopped | free (needs Docker, ~8 GB RAM) | ~$4–5/day |
| Setup time | ~15 min (auto) | ~10 min | ~30 min |

`./exam/trainer env` tells you which environment it detected and which tasks are available.

---

## A. kubeadm on 3 GCE VMs (recommended)

Run from **Cloud Shell**, which already has gcloud, terraform, and kubectl.

```bash
gcloud config set project YOUR_PROJECT_ID
./envs/kubeadm-gce/up.sh              # 3 VMs + kubeadm cluster + Calico + metrics-server + storage + Gateway API + Traefik
./envs/kubeadm-gce/install-app.sh     # builds images in Cloud Build, creates an image-pull Secret, helm installs ShopFleet
./envs/kubeadm-gce/ssh.sh             # you are now student@cp1 -- exactly like the exam terminal
```

On `cp1`:

```bash
cd shopfleet
kubectl get nodes -o wide             # cp1 (control-plane), node01, node02
./exam/trainer env
ssh node01                            # passwordless, then: sudo -i
```

The store is at `http://<node01 public IP>:30080` (printed by `install-app.sh`). Log in as admin and open **Cluster** to watch your exam work live.

**Manual mode (strongly recommended once):** `BOOTSTRAP=manual ./envs/kubeadm-gce/up.sh` installs only the prerequisites: containerd, kubeadm, kubelet, kubectl, and kernel settings. You then build the cluster yourself with [labs/01-kubeadm-install.md](labs/01-kubeadm-install.md). It's a classic CKA skill.

**Kubernetes version:** set `k8s_minor` in `envs/kubeadm-gce/terraform.tfvars`. Pick the minor **one below** the version the CKA currently uses, so the upgrade lab and task cka-02 take you to the exam's version.

**Save money:**

```bash
./envs/kubeadm-gce/stop.sh            # stop VMs (disks kept, cluster survives)
./envs/kubeadm-gce/stop.sh start      # resume; give it 2-3 minutes
./envs/kubeadm-gce/down.sh            # delete everything
```

How the pieces work (and what you'd learn by reading them):
- [envs/kubeadm-gce/main.tf](../../envs/kubeadm-gce/main.tf):
  - VPC with SSH only via IAP
  - static internal IPs
  - a shared bootstrap token
  - an ed25519 key so `student@cp1` can `ssh` to the workers
- [envs/kubeadm-gce/node-init.sh.tpl](../../envs/kubeadm-gce/node-init.sh.tpl): every prerequisite the CKA "prepare the infrastructure" bullet means, commented
- [envs/common/platform.sh](../../envs/common/platform.sh): CNI, metrics-server, StorageClass, Gateway API, and ingress, installed the way you'd do it by hand

## B. kind on your machine (free)

Needs Docker, [kind](https://kind.sigs.k8s.io), kubectl, and helm.

```bash
./envs/kind/up.sh         # 3-node cluster (Calico), add-ons, builds + loads all images, helm installs ShopFleet
open http://localhost:8080
./exam/trainer env        # environment: kind
docker exec -it cka-worker bash     # the kind equivalent of "ssh node01"
./envs/kind/down.sh
```

## C. GKE

The main ShopFleet setup ([../01-setup.md](../01-setup.md)) works for all CKAD tasks and the non-node CKA tasks. It's ideal if you're also studying for Google Cloud certifications.

## Resetting

```bash
./exam/trainer reset all                              # undo every task's changes
helm upgrade shopfleet charts/shopfleet -n shopfleet --reuse-values   # restore the app (kubeadm/kind)
```
