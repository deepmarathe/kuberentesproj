# Shared settings for scripts. Override with env vars, e.g. REGION=europe-west1 ./scripts/...
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
CLUSTER="${CLUSTER:-shopfleet}"
NS=shopfleet
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/shopfleet"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "No project set. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
