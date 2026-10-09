#!/usr/bin/env bash
# Build all images in Cloud Build (no local Docker needed), push to Artifact Registry, deploy to GKE.
#   ./scripts/build-and-deploy.sh            # build + deploy, tag = timestamp
#   TAG=v2 ./scripts/build-and-deploy.sh     # custom tag
#   DEPLOY=false ./scripts/build-and-deploy.sh   # build only
source "$(dirname "$0")/common.sh"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
DEPLOY="${DEPLOY:-true}"

say "Submitting build ${TAG} to Cloud Build (watch it: Console -> Cloud Build -> History)"
cd "${ROOT}"
gcloud builds submit . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --config cloudbuild.yaml \
  --substitutions "_TAG=${TAG},_REGION=${REGION},_ZONE=${ZONE},_CLUSTER=${CLUSTER},_DEPLOY=${DEPLOY}"

say "Build ${TAG} finished."
if [[ "${DEPLOY}" == "true" ]]; then
  echo "${TAG}" > "${ROOT}/.last-tag"   # what's running now; scripts/deploy.sh defaults to it
  IP=$(gcloud compute addresses describe shopfleet-ip --global --format='value(address)' --project "${PROJECT_ID}" 2>/dev/null || true)
  echo "Storefront: http://${IP}  (first deploy: the load balancer takes ~5-10 min to come up)"
fi
