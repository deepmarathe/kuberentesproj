#!/bin/bash
# GCE startup script (runs on EVERY boot; real work only happens once).
# Rendered by Terraform: node=${node_name} role=${role} k8s=v${k8s_minor} bootstrap=${bootstrap}
# Logs:  sudo journalctl -u google-startup-scripts -f      Marker: /var/lib/cka-init.done
set -euxo pipefail
NODE=${node_name}
ROLE=${role}
K8S_MINOR=${k8s_minor}
BOOTSTRAP=${bootstrap}
TOKEN=${token}
MARK=/var/lib/cka-init.done
MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes

if [ -f "$MARK" ]; then exit 0; fi

# ---------------------------------------------------------------- names & student user
hostnamectl set-hostname "$NODE"
grep -q " cp1$" /etc/hosts || cat >> /etc/hosts <<'HOSTS'
${hosts}
HOSTS

id student >/dev/null 2>&1 || useradd -m -s /bin/bash student
echo "student ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/student
install -d -m 700 -o student -g student /home/student/.ssh
curl -sf -H "Metadata-Flavor: Google" "$MD/student-pub" >> /home/student/.ssh/authorized_keys
if [ "$ROLE" = "control-plane" ]; then
  curl -sf -H "Metadata-Flavor: Google" "$MD/student-key" | base64 -d > /home/student/.ssh/id_ed25519
  chmod 600 /home/student/.ssh/id_ed25519
  printf 'Host node01 node02 cp1\n  StrictHostKeyChecking no\n  UserKnownHostsFile /dev/null\n  LogLevel ERROR\n' > /home/student/.ssh/config
fi
chown -R student:student /home/student/.ssh && chmod 600 /home/student/.ssh/authorized_keys

# ---------------------------------------------------------------- kernel prerequisites (CKA: "prepare infrastructure")
swapoff -a
sed -i '/ swap / s/^/#/' /etc/fstab
cat > /etc/modules-load.d/k8s.conf <<'EOF'
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter
cat > /etc/sysctl.d/k8s.conf <<'EOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sysctl --system

# ---------------------------------------------------------------- container runtime: containerd (systemd cgroups)
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y containerd apt-transport-https ca-certificates curl gpg jq vim bash-completion socat conntrack
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
systemctl restart containerd
systemctl enable containerd

# ---------------------------------------------------------------- kubeadm, kubelet, kubectl (pinned minor, held)
mkdir -p /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/v$K8S_MINOR/deb/Release.key" | gpg --dearmor --yes -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v$K8S_MINOR/deb/ /" > /etc/apt/sources.list.d/kubernetes.list
apt-get update
apt-get install -y kubelet kubeadm kubectl cri-tools
apt-mark hold kubelet kubeadm kubectl
systemctl enable kubelet
cat > /etc/crictl.yaml <<'EOF'
runtime-endpoint: unix:///run/containerd/containerd.sock
image-endpoint: unix:///run/containerd/containerd.sock
timeout: 10
EOF

# etcdctl + etcdutl (for backup/restore tasks)
ETCD_VER=v3.5.17
curl -fsSL "https://github.com/etcd-io/etcd/releases/download/$ETCD_VER/etcd-$ETCD_VER-linux-amd64.tar.gz" | tar xz -C /tmp
install /tmp/etcd-$ETCD_VER-linux-amd64/etcdctl /tmp/etcd-$ETCD_VER-linux-amd64/etcdutl /usr/local/bin/

if [ "$ROLE" = "control-plane" ]; then
  # helm + podman (Helm and image-build tasks) + python3 for the trainer
  curl -fsSL https://get.helm.sh/helm-v3.16.3-linux-amd64.tar.gz | tar xz -C /tmp && install /tmp/linux-amd64/helm /usr/local/bin/helm
  apt-get install -y podman python3
  # the shared add-on installer (Calico, metrics-server, storage, Gateway API, Traefik)
  cat > /usr/local/bin/cka-platform.sh <<'PLATFORM'
${platform_script}
PLATFORM
  chmod +x /usr/local/bin/cka-platform.sh
  # exam-like shell for student
  cat >> /home/student/.bashrc <<'EOF'
source <(kubectl completion bash)
alias k=kubectl
complete -o default -F __start_kubectl k
export do="--dry-run=client -o yaml"   # k create deploy x --image=nginx $do > x.yaml
export now="--force --grace-period 0"  # k delete pod x $now
EOF
  printf 'set expandtab\nset tabstop=2\nset shiftwidth=2\nset number\n' > /home/student/.vimrc
  chown student:student /home/student/.bashrc /home/student/.vimrc
fi

touch /var/lib/cka-prereqs.done
if [ "$BOOTSTRAP" != "auto" ]; then
  touch "$MARK"
  exit 0   # manual mode: practise kubeadm init/join yourself (docs/cka-ckad/labs/01-kubeadm-install.md)
fi

# ---------------------------------------------------------------- auto bootstrap
if [ "$ROLE" = "control-plane" ]; then
  PUBLIC_IP=$(curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)
  kubeadm init \
    --pod-network-cidr=192.168.0.0/16 \
    --token "$TOKEN" --token-ttl 0 \
    --node-name cp1 \
    --apiserver-cert-extra-sans "cp1,10.240.0.10,$PUBLIC_IP"
  install -d -o student -g student /home/student/.kube
  install -o student -g student -m 600 /etc/kubernetes/admin.conf /home/student/.kube/config
  export KUBECONFIG=/etc/kubernetes/admin.conf HOME=/root
  # wait for both workers to register, then install add-ons
  for i in $(seq 1 90); do [ "$(kubectl get nodes --no-headers 2>/dev/null | wc -l)" -ge 3 ] && break; sleep 10; done
  PLATFORM_ENV=kubeadm /usr/local/bin/cka-platform.sh
else
  until kubeadm join cp1:6443 --token "$TOKEN" --discovery-token-unsafe-skip-ca-verification --node-name "$NODE"; do
    echo "control plane not ready yet, retrying in 15s"; kubeadm reset -f >/dev/null 2>&1 || true; sleep 15
  done
fi
touch "$MARK"
