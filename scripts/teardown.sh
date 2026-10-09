#!/usr/bin/env bash
# Delete EVERYTHING (stops all billing for this project's ShopFleet resources).
# Order matters: delete Kubernetes objects that own cloud resources (Ingress -> load balancer,
# NEGs, firewall rules; PVCs -> disks) BEFORE destroying the network, or terraform will get stuck.
source "$(dirname "$0")/common.sh"
read -r -p "This deletes the cluster and all data in project ${PROJECT_ID}. Type 'yes': " ok
[[ "${ok}" == "yes" ]] || exit 1

if gcloud container clusters describe "${CLUSTER}" --zone "${ZONE}" >/dev/null 2>&1; then
  gcloud container clusters get-credentials "${CLUSTER}" --zone "${ZONE}"
  say "Deleting Ingress (releases the load balancer)"
  kubectl -n "${NS}" delete ingress --all --wait=true --ignore-not-found || true
  say "Deleting namespaces (releases persistent disks)"
  kubectl delete namespace "${NS}" loadgen sandbox --wait=true --ignore-not-found || true
  sleep 60   # give the GKE controllers time to clean up NEGs/firewall rules
fi

say "terraform destroy"
cd "${ROOT}/terraform" && terraform destroy -auto-approve
say "Done. Images in Artifact Registry were deleted with the repository."
