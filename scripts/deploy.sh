#!/usr/bin/env bash
# Render the GKE overlay with real values and apply it. Used by Cloud Build and by hand.
#   TAG=20250101-120000 ./scripts/deploy.sh      (defaults to the last built tag)
#   OVERLAY=gke-cloudsql ./scripts/deploy.sh     (lab 15)
#   DRY_RUN=true ./scripts/deploy.sh             (print rendered YAML only)
source "$(dirname "$0")/common.sh"
TAG="${TAG:-$(cat "${ROOT}/.last-tag" 2>/dev/null || true)}"
: "${TAG:?Set TAG=<image tag> (no .last-tag found)}"

OVERLAY="${OVERLAY:-gke}"
SQL_INSTANCE="${SQL_INSTANCE:-${PROJECT_ID}:${REGION}:shopfleet-sql}"

render() {
  kubectl kustomize "${ROOT}/k8s/overlays/${OVERLAY}" | sed \
    -e "s|__REGISTRY__|${REGISTRY}|g" \
    -e "s|__TAG__|${TAG}|g" \
    -e "s|__PROJECT_ID__|${PROJECT_ID}|g" \
    -e "s|__SQL_INSTANCE__|${SQL_INSTANCE}|g"
}

if [[ "${DRY_RUN:-false}" == "true" ]]; then render; exit 0; fi

say "Deploying tag ${TAG} from ${REGISTRY} (overlay: ${OVERLAY})"
# Jobs are immutable: delete the previous migration Job so the new one (maybe new image) can run.
kubectl -n "${NS}" delete job db-migrate --ignore-not-found --wait=true
render | kubectl apply -f -

say "Waiting for database + migration"
kubectl -n "${NS}" rollout status statefulset/postgres --timeout=300s
kubectl -n "${NS}" rollout status statefulset/redis --timeout=300s
kubectl -n "${NS}" wait --for=condition=complete job/db-migrate --timeout=300s

say "Waiting for app rollouts"
for d in payment catalog cart orders auth notifications ops-api flashsale-operator worker frontend; do
  kubectl -n "${NS}" rollout status "deployment/${d}" --timeout=300s
done
kubectl -n "${NS}" rollout status daemonset/node-agent --timeout=300s
kubectl -n loadgen rollout status deployment/loadgen --timeout=300s
kubectl -n "${NS}" get pods -o wide
say "Deployed ${TAG}"
