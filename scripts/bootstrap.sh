#!/usr/bin/env bash
# One-time setup: enable APIs, create all infrastructure with Terraform, connect kubectl.
source "$(dirname "$0")/common.sh"

say "Project: ${PROJECT_ID}  region: ${REGION}  zone: ${ZONE}"
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com --project "${PROJECT_ID}"

cd "${ROOT}/terraform"
if [[ ! -f terraform.tfvars ]]; then
  cat > terraform.tfvars <<VARS
project_id = "${PROJECT_ID}"
region     = "${REGION}"
zone       = "${ZONE}"
VARS
  say "Wrote terraform/terraform.tfvars"
fi

say "terraform init"
terraform init -upgrade
say "terraform apply (creates VPC, NAT, GKE cluster, node pools, registry, IAM, alerts: ~10-15 min)"
terraform apply -auto-approve

say "Connecting kubectl to the cluster"
gcloud container clusters get-credentials "${CLUSTER}" --zone "${ZONE}" --project "${PROJECT_ID}"
kubectl get nodes -o wide

say "Done. Next: ./scripts/build-and-deploy.sh"
