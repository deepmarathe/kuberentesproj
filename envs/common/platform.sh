#!/usr/bin/env bash
# =============================================================================
# Cluster add-ons every exam-style cluster needs, installed the way you would in the CKA:
#   1. CNI: Calico (operator)      -- pods get IPs + NetworkPolicy enforcement
#   2. metrics-server (Helm)       -- kubectl top, HPA
#   3. local-path-provisioner      -- dynamic PersistentVolumes on node disks (default StorageClass)
#   4. Gateway API CRDs            -- gateway.networking.k8s.io
#   5. Traefik (Helm)              -- Ingress + Gateway controller, NodePort 30080
# Idempotent: safe to re-run. Usage: PLATFORM_ENV=kubeadm|kind ./platform.sh
# =============================================================================
set -euo pipefail
PLATFORM_ENV="${PLATFORM_ENV:-kubeadm}"
CALICO_VERSION="${CALICO_VERSION:-v3.29.1}"
GATEWAY_API_VERSION="${GATEWAY_API_VERSION:-v1.2.1}"
LOCAL_PATH_VERSION="${LOCAL_PATH_VERSION:-v0.0.30}"
POD_CIDR="${POD_CIDR:-192.168.0.0/16}"
say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

say "1/5 Calico CNI ${CALICO_VERSION}"
if ! kubectl get ns tigera-operator >/dev/null 2>&1; then
  kubectl create -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/tigera-operator.yaml"
fi
kubectl wait --for=condition=Established crd/installations.operator.tigera.io --timeout=120s
# VXLAN encapsulation: GCE's VPC drops IP-in-IP (protocol 4), so the default IPIP mode would break pod traffic between nodes.
ENCAP=VXLAN; [[ "$PLATFORM_ENV" == "kind" ]] && ENCAP=VXLANCrossSubnet
kubectl apply -f - <<YAML
apiVersion: operator.tigera.io/v1
kind: Installation
metadata: { name: default }
spec:
  calicoNetwork:
    ipPools:
      - name: default-ipv4-ippool
        blockSize: 26
        cidr: ${POD_CIDR}
        encapsulation: ${ENCAP}
        natOutgoing: Enabled
        nodeSelector: all()
---
apiVersion: operator.tigera.io/v1
kind: APIServer
metadata: { name: default }
spec: {}
YAML
echo "waiting for nodes to become Ready (CNI starting)…"
kubectl wait --for=condition=Ready nodes --all --timeout=600s

say "2/5 metrics-server"
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null 2>&1 || true
helm repo update metrics-server >/dev/null
# kubeadm/kind kubelets use self-signed serving certs -> --kubelet-insecure-tls (lab only!)
helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system \
  --set 'args={--kubelet-insecure-tls}' --wait --timeout 5m

say "3/5 StorageClass"
if ! kubectl get storageclass 2>/dev/null | grep -q '(default)'; then
  kubectl apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCAL_PATH_VERSION}/deploy/local-path-storage.yaml"
  kubectl patch storageclass local-path -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
fi
kubectl get storageclass

say "4/5 Gateway API ${GATEWAY_API_VERSION}"
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"

say "5/5 Traefik (Ingress + Gateway controller) on NodePort 30080"
helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
helm repo update traefik >/dev/null
helm upgrade --install traefik traefik/traefik -n traefik --create-namespace \
  --set service.type=NodePort \
  --set ports.web.nodePort=30080 \
  --set ports.websecure.nodePort=30443 \
  --set providers.kubernetesIngress.enabled=true \
  --set providers.kubernetesGateway.enabled=true \
  --set ingressClass.isDefaultClass=true \
  --wait --timeout 5m
kubectl get ingressclass,gatewayclass

say "Platform ready"
kubectl get nodes -o wide
