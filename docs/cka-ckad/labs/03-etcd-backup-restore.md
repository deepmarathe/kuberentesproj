# CKA Lab 03 – etcd backup & restore

etcd holds **every Kubernetes object**: Deployments, Secrets, FlashSales, and so on. It does **not** hold your application's data. ShopFleet's orders live in Postgres on a PersistentVolume. This lab makes that difference concrete.

## 1. Back up

```bash
ssh cp1 (or you're already there); sudo -i
grep -E 'listen-client-urls|cert-file|key-file|trusted-ca-file|data-dir' /etc/kubernetes/manifests/etcd.yaml
mkdir -p /opt/backup
ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  snapshot save /opt/backup/etcd-$(date +%F-%H%M).db
etcdutl snapshot status /opt/backup/etcd-*.db -w table
```

Memorise the four flags and where to find their values. The exam gives you the paths in the question, or you read them from the manifest.

## 2. Make a change you'll lose and one you'll keep

```bash
exit   # back to student
kubectl -n shopfleet apply -f ~/shopfleet/k8s/examples/flashsale.yaml     # created BEFORE the backup? -> it will come back
kubectl -n shopfleet delete flashsale book-week                            # "incident"
kubectl create namespace after-backup                                      # created AFTER the backup -> will vanish
# Buy something in the store now: a new order row in POSTGRES
```

## 3. Restore

```bash
sudo -i
etcdutl snapshot restore /opt/backup/etcd-<time>.db --data-dir /var/lib/etcd-restored
# (older exam environments: ETCDCTL_API=3 etcdctl snapshot restore … --data-dir …)
vi /etc/kubernetes/manifests/etcd.yaml
#   volumes:
#   - hostPath: { path: /var/lib/etcd-restored, type: DirectoryOrCreate }   <- was /var/lib/etcd
#     name: etcd-data
watch crictl ps            # etcd restarts, then kube-apiserver reconnects (1-2 min). kubectl fails meanwhile: normal
```

Best practice in production: stop the API server first (move `kube-apiserver.yaml` out of the manifests directory), restore, then move it back. That way no writes race the restore.

## 4. Observe

```bash
kubectl get flashsale -n shopfleet        # book-week is back
kubectl get ns after-backup               # NotFound: it never existed in the snapshot
```

Now check the store: **your new order is still there.** Postgres data isn't in etcd. Restoring etcd rolls back cluster *configuration*, not application data, and that's why lab [../../labs/18-backup-dr.md](../../labs/18-backup-dr.md) backs up Postgres separately.

Trainer tasks: `cka-03` (backup), `cka-04` (restore incident, kubeadm environment).
