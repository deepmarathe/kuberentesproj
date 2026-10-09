# CKA Lab 01 – Install a cluster with kubeadm, by hand

**Goal:** build `cp1` + `node01` + `node02` into a working cluster yourself. The CKA expects you to know every step, and doing it twice cements it.

```bash
BOOTSTRAP=manual ./envs/kubeadm-gce/up.sh       # VMs with prerequisites only
./envs/kubeadm-gce/ssh.sh                       # student@cp1
```

## 1. Check the prerequisites (already done by the startup script; know them anyway)

On every node:

```bash
sudo swapon --show                    # empty: swap must be off
lsmod | grep -E 'overlay|br_netfilter'
sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables     # both = 1
systemctl is-active containerd
grep SystemdCgroup /etc/containerd/config.toml                     # true: must match the kubelet's cgroupDriver (systemd)
kubeadm version; kubelet --version; apt-mark showhold              # kubeadm/kubelet/kubectl held
```

Read [node-init.sh.tpl](../../../envs/kubeadm-gce/node-init.sh.tpl) to see how each one was set up. That file *is* the "prepare the infrastructure" checklist.

## 2. Initialise the control plane

```bash
sudo kubeadm init --pod-network-cidr=192.168.0.0/16 --apiserver-cert-extra-sans=cp1,10.240.0.10 --node-name cp1
```

Read the output. It tells you exactly what to do next:

```bash
mkdir -p $HOME/.kube
sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config
kubectl get nodes                      # cp1 NotReady -- no CNI yet
kubectl -n kube-system get pods        # coredns Pending -- same reason
```

Look at what kubeadm created:

```bash
ls /etc/kubernetes/manifests/          # etcd, kube-apiserver, kube-controller-manager, kube-scheduler = STATIC PODS
ls /etc/kubernetes/pki/                # the cluster's CA and certificates
sudo cat /var/lib/kubelet/config.yaml | grep -E 'staticPodPath|cgroupDriver|clusterDNS'
```

## 3. Install the CNI (Calico)

```bash
kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.29.1/manifests/tigera-operator.yaml
kubectl apply -f - <<'EOF'
apiVersion: operator.tigera.io/v1
kind: Installation
metadata: { name: default }
spec:
  calicoNetwork:
    ipPools:
      - { name: default-ipv4-ippool, cidr: 192.168.0.0/16, encapsulation: VXLAN, natOutgoing: Enabled, blockSize: 26, nodeSelector: all() }
EOF
kubectl get nodes -w                   # cp1 becomes Ready
```

The CIDR must match `--pod-network-cidr`. We use VXLAN because Google's VPC drops IP-in-IP packets, so Calico's default would silently break cross-node traffic. That's a good real-world troubleshooting story.

## 4. Join the workers

```bash
kubeadm token create --print-join-command        # on cp1; copy the output
ssh node01
sudo kubeadm join cp1:6443 --token … --discovery-token-ca-cert-hash sha256:…
exit
ssh node02   # same
kubectl get nodes -o wide                         # 3 nodes Ready
```

## 5. Add-ons, then the app

```bash
PLATFORM_ENV=kubeadm cka-platform.sh              # metrics-server, local-path storage, Gateway API, Traefik (skips Calico steps already done)
```

Back in Cloud Shell, run `./envs/kubeadm-gce/install-app.sh`.

## Do it again (the reset drill)

```bash
# on every node:
sudo kubeadm reset -f
sudo rm -rf /etc/cni/net.d $HOME/.kube
sudo iptables -F && sudo iptables -t nat -F
```

Then repeat steps 2–5 without looking. Target: under 30 minutes.

## HA control planes (theory)

The CKA asks you to *understand* HA, not build it:

- **Stacked etcd:** each control-plane node runs etcd. You need at least **3** for quorum, tolerating 1 failure.
- **External etcd:** a separate etcd cluster, with more machines and more isolation.
- A **load balancer** in front of the API servers: `kubeadm init --control-plane-endpoint LB:6443 --upload-certs`, then `kubeadm join … --control-plane --certificate-key …` for the extra control planes.
- Workers talk to the LB endpoint. If one API server dies, nothing notices.
