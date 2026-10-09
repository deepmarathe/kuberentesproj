#!/usr/bin/env bash
# Build images (Cloud Build), create an image-pull secret, and helm install ShopFleet on the kubeadm cluster.
#   TAG=<existing tag> ./install-app.sh   to skip the build
source "$(dirname "$0")/common.sh"
REGISTRY="$REGION-docker.pkg.dev/$PROJECT_ID/shopfleet"
if [[ -z "${TAG:-}" ]]; then
  TAG="cka-$(date +%Y%m%d-%H%M%S)"
  echo "==> building images $TAG in Cloud Build (~8 min)"
  (cd "$ROOT" && gcloud builds submit . --region "$REGION" --config cloudbuild.yaml \
     --substitutions "_TAG=$TAG,_REGION=$REGION,_DEPLOY=false")
fi

echo "==> image-pull credentials (JSON key of cka-image-puller, stored as a docker-registry Secret)"
KEY=$(mktemp); trap 'rm -f "$KEY"' EXIT
gcloud iam service-accounts keys create "$KEY" --iam-account "cka-image-puller@$PROJECT_ID.iam.gserviceaccount.com" --quiet
"$HERE/push-repo.sh"
gcloud compute scp "$KEY" cp1:/tmp/puller.json --zone "$ZONE" --tunnel-through-iap --quiet
cp1 "
set -e
kubectl create namespace shopfleet --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace shopfleet pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted --overwrite
kubectl -n shopfleet create secret docker-registry ar-cred --docker-server=$REGION-docker.pkg.dev \
  --docker-username=_json_key --docker-password=\"\$(sudo cat /tmp/puller.json)\" --dry-run=client -o yaml | kubectl apply -f -
sudo rm -f /tmp/puller.json
helm upgrade --install shopfleet ~/shopfleet/charts/shopfleet -n shopfleet -f ~/shopfleet/charts/shopfleet/values-kubeadm.yaml \
  --set image.registry=$REGISTRY --set image.tag=$TAG
kubectl apply -f ~/shopfleet/k8s/examples/flashsale.yaml || true
kubectl -n shopfleet get pods
"
IP=$(gcloud compute instances describe node01 --zone "$ZONE" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
echo
echo "Store:    http://$IP:30080   (pods need ~2 minutes)"
echo "Practice: ./envs/kubeadm-gce/ssh.sh   then   cd shopfleet && ./exam/trainer env"
