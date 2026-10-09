#!/usr/bin/env bash
# Create the 3-VM kubeadm cluster.   BOOTSTRAP=manual ./up.sh  -> prerequisites only (install it yourself)
source "$(dirname "$0")/common.sh"
cd "$HERE"
[[ -f terraform.tfvars ]] || printf 'project_id = "%s"\nregion = "%s"\nzone = "%s"\nbootstrap = "%s"\n' "$PROJECT_ID" "$REGION" "$ZONE" "${BOOTSTRAP:-auto}" > terraform.tfvars
terraform init -upgrade >/dev/null
terraform apply -auto-approve
echo "==> VMs created. Bootstrapping takes ~8-10 minutes. Waiting for cp1…"
for i in $(seq 1 80); do
  if gcloud compute ssh cp1 --zone "$ZONE" --tunnel-through-iap --quiet --command "test -f /var/lib/cka-init.done" 2>/dev/null; then break; fi
  sleep 15; printf '.'
done
echo
cp1 "kubectl get nodes -o wide 2>/dev/null || echo 'manual mode: no cluster yet -- see docs/cka-ckad/labs/01-kubeadm-install.md'"
echo "Next: ./envs/kubeadm-gce/install-app.sh   then   ./envs/kubeadm-gce/ssh.sh"
