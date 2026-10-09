# CKA Lab 02 – Upgrade the cluster with kubeadm

**Rules:** one minor version at a time. Upgrade the control plane first, then the workers. Nodes may lag the API server, never lead it. Keep the store open in a browser: ShopFleet should keep serving the whole time, thanks to 2 replicas, PodDisruptionBudgets, and graceful shutdown.

Set the versions first: `CUR` is what you run now (`kubectl get nodes`), and `NEXT` is CUR + 1.

## 1. Control plane (cp1)

```bash
sudo -i
NEXT=1.35          # <- adjust
sed -i "s#/v1\.[0-9]*/#/v${NEXT}/#" /etc/apt/sources.list.d/kubernetes.list
apt-get update
apt-cache madison kubeadm | head -3                  # e.g. 1.35.2-1.1
apt-mark unhold kubeadm && apt-get install -y kubeadm=1.35.2-1.1 && apt-mark hold kubeadm
kubeadm version
kubeadm upgrade plan                                 # shows what will change + the exact command
kubeadm upgrade apply v1.35.2                        # control-plane static pods are replaced one by one
exit
```

```bash
kubectl drain cp1 --ignore-daemonsets                # as student
sudo apt-mark unhold kubelet kubectl && sudo apt-get install -y kubelet=1.35.2-1.1 kubectl=1.35.2-1.1 && sudo apt-mark hold kubelet kubectl
sudo systemctl daemon-reload && sudo systemctl restart kubelet
kubectl uncordon cp1
kubectl get nodes                                    # cp1 v1.35.2, workers still v1.34.x  (allowed)
```

Check your work: `./exam/trainer check cka-02`

## 2. Each worker

```bash
ssh node01
sudo -i
sed -i "s#/v1\.[0-9]*/#/v1.35/#" /etc/apt/sources.list.d/kubernetes.list && apt-get update
apt-mark unhold kubeadm && apt-get install -y kubeadm=1.35.2-1.1 && apt-mark hold kubeadm
kubeadm upgrade node                                 # (workers: "upgrade node", not "apply")
exit; exit                                           # back to cp1

kubectl drain node01 --ignore-daemonsets --delete-emptydir-data     # PDBs make this wait for replacements

ssh node01 'sudo apt-mark unhold kubelet kubectl && sudo apt-get install -y kubelet=1.35.2-1.1 kubectl=1.35.2-1.1 && sudo apt-mark hold kubelet kubectl && sudo systemctl daemon-reload && sudo systemctl restart kubelet'
kubectl uncordon node01
```

Repeat for node02.

## What to watch while you do it
- `kubectl get pods -n shopfleet -o wide -w`: pods are evicted and rescheduled, and PDBs keep at least 1 of each running.
- `postgres-0` has no PDB and only one replica, so the store has a ~30 s database blip when its node drains. Why would Cloud SQL or an HA operator avoid that?
- The **Cluster** page in the store shows the node versions changing live.

## Exam notes
- The exam usually asks for just the control plane, or one worker. Read the question.
- If you forget the drain, the upgrade still works, but you lose points and, in production, requests.
- `kubeadm upgrade plan` prints the exact `apply` command. Copy it.
