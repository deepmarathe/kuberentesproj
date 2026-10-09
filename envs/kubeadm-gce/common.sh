set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/envs/kubeadm-gce"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-us-central1-a}"
REGION="${REGION:-us-central1}"
cp1() { gcloud compute ssh cp1 --zone "$ZONE" --tunnel-through-iap --quiet --command "sudo -iu student bash -lc $(printf %q "$*")"; }
