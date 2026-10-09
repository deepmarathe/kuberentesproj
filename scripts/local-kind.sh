#!/usr/bin/env bash
# Run ShopFleet on a local kind cluster (needs docker, kind, kubectl). Free, no GCP needed.
# Good for practising kubectl; GKE-only features (Ingress/LB, Workload Identity, Managed
# Prometheus, Spot) are skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
kind get clusters | grep -qx shopfleet || kind create cluster --name shopfleet
for s in frontend catalog cart orders payment worker dbmigrate loadgen auth notifications ops-api operator node-agent; do
  docker build -t "shopfleet/${s}:dev" "${ROOT}/services/${s}"
  kind load docker-image "shopfleet/${s}:dev" --name shopfleet
done
# metrics-server enables `kubectl top` and the HPAs
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deployment metrics-server --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' || true
kubectl -n shopfleet delete job db-migrate --ignore-not-found
kubectl apply -k "${ROOT}/k8s/overlays/local"
kubectl -n shopfleet wait --for=condition=complete job/db-migrate --timeout=300s
echo "Open the store:  kubectl -n shopfleet port-forward svc/frontend 8080:8080  -> http://localhost:8080"
