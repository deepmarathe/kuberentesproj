# CKA Lab 04 – Control-plane & node troubleshooting (30% of the CKA)

## The map: where everything lives on a kubeadm cluster

| Component | Runs as | Config | Logs |
|---|---|---|---|
| kubelet | systemd service on every node | `/var/lib/kubelet/config.yaml`, `/etc/kubernetes/kubelet.conf`, `/usr/lib/systemd/system/kubelet.service.d/10-kubeadm.conf` | `journalctl -u kubelet` |
| containerd | systemd service | `/etc/containerd/config.toml` | `journalctl -u containerd` |
| kube-apiserver, controller-manager, scheduler, etcd | **static pods** on cp1 | `/etc/kubernetes/manifests/*.yaml` | `kubectl -n kube-system logs <pod>`, or if the API is down: `crictl ps -a` + `crictl logs <id>`, `/var/log/pods/` |
| kube-proxy, CoreDNS, Calico | DaemonSets / Deployments | ConfigMaps in kube-system / calico-system | `kubectl logs` |
| Certificates | files | `/etc/kubernetes/pki/` | `kubeadm certs check-expiration` |

The kubelet watches `/etc/kubernetes/manifests`. Save a change there and the static pod is recreated within seconds. A typo makes the pod crash or disappear, which is why "`kubectl` stopped working" is a common exam scenario.

## A systematic approach

```
Symptom ─▶ which layer?
  kubectl can't connect   → API server down  → ssh cp1: crictl ps -a | grep apiserver; crictl logs; check manifest + certs + etcd
  node NotReady           → kubelet/runtime/CNI on that node → systemctl status kubelet; journalctl -u kubelet; crictl ps
  pods Pending, no events → scheduler down   → kube-system scheduler pod; its manifest
  pods Pending, events    → resources/taints/affinity/PVC/quota → kubectl describe pod
  Deployment not creating pods → controller-manager or quota → kube-system controller-manager; describe rs
  pods can't reach a Service → endpoints? selector? targetPort? NetworkPolicy? DNS? kube-proxy?
```

## Practice: trainer scenarios (all on real ShopFleet)

| Task | Breaks | Typical evidence |
|---|---|---|
| cka-21 | kubelet stopped + disabled on a worker | `NodeStatusUnknown`, "Kubelet stopped posting node status" |
| cka-22 | scheduler manifest points to a missing kubeconfig | new pods Pending with no events; scheduler CrashLoop |
| cka-23 | Postgres Service selector typo | catalog/orders/auth unready, `db unavailable`, Service has no endpoints |
| cka-24 | worker memory limit too low | `OOMKilled`, exit code 137 |
| cka-25 | ResourceQuota exhausted | ReplicaSet events `exceeded quota` |
| cka-26 | certificates | `kubeadm certs check-expiration` / `renew` |
| cka-27 | runtime-level inspection | `crictl ps`, `crictl inspect` |
| cka-28 | node maintenance | drain, cordon, PDB interaction |

Plus the ShopFleet chaos scenarios (`./scripts/chaos.sh list`). They also work on kubeadm and kind.

## Extra drills (do each by hand, then undo)

```bash
# 1. Break the API server: on cp1, add a bogus flag to kube-apiserver.yaml (e.g. --this-flag-does-not-exist).
#    kubectl now fails. Find the error with crictl:  crictl ps -a | grep apiserver ; crictl logs <id>
# 2. Wrong CA path for the kubelet on node02 (edit /var/lib/kubelet/config.yaml authentication.x509.clientCAFile).
#    Watch node02 go NotReady, read journalctl, fix, restart kubelet.
# 3. Delete the CoreDNS pods and scale coredns to 0: what breaks in ShopFleet? (Hint: every service name.)
# 4. Stop containerd on node01: what does the node status say?
```
