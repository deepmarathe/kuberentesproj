#!/usr/bin/env bash
# =============================================================================
# Break things on purpose. Each scenario maps to a lab in docs/labs/.
#   ./scripts/chaos.sh list
#   ./scripts/chaos.sh payment-errors 0.5
# =============================================================================
set -euo pipefail
NS=shopfleet
cmd="${1:-list}"; arg="${2:-}"

say() { printf '\033[1;31m[chaos]\033[0m %s\n' "$*"; }

# POST to a chaos endpoint on localhost inside a pod (catalog has python, so we use it as curl).
pod_post() {
  local pod="$1" path="$2"
  kubectl -n "$NS" exec "$pod" -c catalog -- python -c \
    "import urllib.request as u; print(u.urlopen(u.Request('http://localhost:8080$path', method='POST'), timeout=10).read().decode())"
}
catalog_pods() { kubectl -n "$NS" get pods -l app=catalog -o jsonpath='{.items[*].metadata.name}'; }
one_catalog_pod() { kubectl -n "$NS" get pods -l app=catalog -o jsonpath='{.items[0].metadata.name}'; }

payment_chaos() {
  kubectl -n "$NS" create configmap payment-chaos --from-literal=chaos.json="$1" \
    --dry-run=client -o yaml | kubectl apply -f -
  say "payment-chaos ConfigMap updated. Pods pick it up WITHOUT restarting in ~30-90s."
  say "Watch:  kubectl -n $NS logs -l app=payment -f | grep reloaded"
}

case "$cmd" in
  list)
    cat <<'LIST'
Payment (hot-reloaded ConfigMap)             lab
  payment-errors [rate=0.5]   5xx errors      03
  payment-slow   [ms=2500]    high latency    03
  payment-hang   [rate=0.3]   30s hangs       03
  payment-reset                               03
Catalog (per-pod chaos endpoints)
  catalog-cpu    [seconds=300] burn CPU, HPA  02
  catalog-leak   [mb/min=100]  memory leak    05
  catalog-unready              1 pod unready  01
  catalog-crash                kill process   01
  catalog-reset                               --
Deployments / config
  bad-image                    ImagePullBackOff, rollback      04
  bad-config                   broken env var, stuck rollout   04
Infrastructure
  kill-redis                   delete redis-0                  06
  kill-postgres                delete postgres-0               06
  worker-stop / worker-start   backlog builds up               07
  netpol-break / netpol-fix    block catalog->postgres         10
  monkey-on / monkey-off       random pod kills every 10 min   01
  flash-sale / traffic-normal  spike load generator            02
  hog / hog-off                fill nodes -> cluster autoscaler 08
LIST
    ;;

  payment-errors) payment_chaos "{\"failure_rate\": ${arg:-0.5}}" ;;
  payment-slow)   payment_chaos "{\"latency_ms\": ${arg:-2500}, \"jitter_ms\": 500}" ;;
  payment-hang)   payment_chaos "{\"hang_rate\": ${arg:-0.3}}" ;;
  payment-reset)  payment_chaos '{"failure_rate": 0.0, "decline_rate": 0.03, "latency_ms": 80, "jitter_ms": 120, "hang_rate": 0.0}' ;;

  catalog-cpu)
    for p in $(catalog_pods); do pod_post "$p" "/admin/chaos/burn?seconds=${arg:-300}&threads=1"; done
    say "Watch:  kubectl -n $NS get hpa catalog -w" ;;
  catalog-leak)
    p=$(one_catalog_pod); pod_post "$p" "/admin/chaos/leak?rate_mb_per_min=${arg:-100}"
    say "Leaking in $p. Watch:  kubectl -n $NS get pod $p -w   (look for OOMKilled / RESTARTS)" ;;
  catalog-unready)
    p=$(one_catalog_pod); pod_post "$p" "/admin/chaos/ready?state=false"
    say "$p now fails readiness. Watch:  kubectl -n $NS get endpointslices -l kubernetes.io/service-name=catalog -w" ;;
  catalog-crash)
    p=$(one_catalog_pod); pod_post "$p" "/admin/chaos/crash" || true
    say "$p crashed. Watch:  kubectl -n $NS get pods -l app=catalog -w" ;;
  catalog-reset)
    for p in $(catalog_pods); do pod_post "$p" "/admin/chaos/reset"; done ;;

  bad-image)
    kubectl -n "$NS" set image deployment/catalog catalog=us-docker.pkg.dev/does-not-exist/nope/catalog:v999
    say "Rollout started with a non-existent image. Fix:  kubectl -n $NS rollout undo deployment/catalog" ;;
  bad-config)
    kubectl -n "$NS" set env deployment/orders DB_HOST=postgres-typo
    say "orders rolled out with a wrong DB_HOST. Fix:  kubectl -n $NS rollout undo deployment/orders" ;;

  kill-redis)     kubectl -n "$NS" delete pod redis-0 ;;
  kill-postgres)  kubectl -n "$NS" delete pod postgres-0 ;;
  worker-stop)
    kubectl -n "$NS" patch hpa worker -p '{"spec":{"minReplicas":1}}' >/dev/null
    kubectl -n "$NS" scale deployment/worker --replicas=0
    say "Note: the HPA will fight you (it scales back to minReplicas=1) -- that's lesson #1 of lab 07."
    say "To really stop it:  kubectl -n $NS delete hpa worker && kubectl -n $NS scale deploy/worker --replicas=0" ;;
  worker-start)   kubectl -n "$NS" scale deployment/worker --replicas=1 ;;

  netpol-break)
    kubectl -n "$NS" patch networkpolicy postgres-from-db-clients --type=json \
      -p '[{"op":"replace","path":"/spec/ingress/0/from/0/podSelector/matchLabels","value":{"shopfleet.io/db-client":"nobody"}}]'
    say "Postgres now rejects everyone. Symptoms: readiness failures, 503s. Fix: ./scripts/chaos.sh netpol-fix" ;;
  netpol-fix)
    kubectl -n "$NS" patch networkpolicy postgres-from-db-clients --type=json \
      -p '[{"op":"replace","path":"/spec/ingress/0/from/0/podSelector/matchLabels","value":{"shopfleet.io/db-client":"true"}}]' ;;

  monkey-on)  kubectl -n "$NS" patch cronjob chaos-monkey -p '{"spec":{"suspend":false}}' ;;
  monkey-off) kubectl -n "$NS" patch cronjob chaos-monkey -p '{"spec":{"suspend":true}}' ;;

  flash-sale)     kubectl -n loadgen set env deployment/loadgen MAX_USERS=120 MIN_USERS=60 ;;
  traffic-normal) kubectl -n loadgen set env deployment/loadgen MAX_USERS=25 MIN_USERS=3 ;;

  hog)
    kubectl create namespace sandbox --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n sandbox create deployment hog --image=registry.k8s.io/pause:3.10 --replicas="${arg:-6}" \
      --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n sandbox set resources deployment/hog --requests=cpu=700m,memory=256Mi
    say "Watch pods go Pending, then new nodes appear:  kubectl get nodes -w" ;;
  hog-off) kubectl delete namespace sandbox --ignore-not-found ;;

  *) echo "unknown scenario: $cmd (try: $0 list)"; exit 1 ;;
esac
