#!/usr/bin/env bash
# Free, local, exam-shaped cluster: kind (Kubernetes-in-Docker) with 3 nodes + the full ShopFleet app.
# Needs: docker, kind, kubectl, helm.   Store: http://localhost:8080   Trainer: ./exam/trainer env
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLUSTER=cka
for b in docker kind kubectl helm; do command -v $b >/dev/null || { echo "missing: $b"; exit 1; }; done

kind get clusters | grep -qx "$CLUSTER" || kind create cluster --config "$ROOT/envs/kind/kind-config.yaml"
kubectl config use-context "kind-$CLUSTER"
PLATFORM_ENV=kind "$ROOT/envs/common/platform.sh"

echo "==> building images (first run takes a few minutes)"
for s in frontend catalog cart orders payment worker dbmigrate loadgen auth notifications ops-api operator node-agent; do
  docker build -q -t "shopfleet/${s}:dev" "$ROOT/services/${s}" >/dev/null && echo "  built $s"
done
kind load docker-image --name "$CLUSTER" $(for s in frontend catalog cart orders payment worker dbmigrate loadgen auth notifications ops-api operator node-agent; do echo "shopfleet/${s}:dev"; done)

echo "==> installing ShopFleet with Helm"
kubectl create namespace shopfleet --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace shopfleet pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted --overwrite
helm upgrade --install shopfleet "$ROOT/charts/shopfleet" -n shopfleet -f "$ROOT/charts/shopfleet/values-kind.yaml"
kubectl apply -f "$ROOT/k8s/examples/flashsale.yaml" || true
echo
echo "Store:    http://localhost:8080   (give it ~2 minutes)"
echo "Admin:    kubectl -n shopfleet get secret admin-credentials -o jsonpath='{.data.password}' | base64 -d"
echo "Practice: ./exam/trainer env && ./exam/trainer list"
echo "Nodes:    docker exec -it cka-worker bash      (the kind equivalent of 'ssh node01')"
