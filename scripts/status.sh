#!/usr/bin/env bash
# A quick "morning check" of the whole system -- what an on-call engineer looks at first.
source "$(dirname "$0")/common.sh"
say "Nodes";            kubectl get nodes -L pool,cloud.google.com/gke-spot,topology.kubernetes.io/zone
say "Pods";             kubectl -n "${NS}" get pods -o wide
say "Not-running pods (all namespaces)"
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded 2>/dev/null || true
say "Deployments";      kubectl -n "${NS}" get deploy
say "Autoscalers";      kubectl -n "${NS}" get hpa
say "PDBs";             kubectl -n "${NS}" get pdb
say "CronJobs";         kubectl -n "${NS}" get cronjobs
say "Ingress";          kubectl -n "${NS}" get ingress
say "Resource usage (top pods)"; kubectl -n "${NS}" top pods 2>/dev/null || echo "metrics not ready yet"
say "Quota";            kubectl -n "${NS}" describe resourcequota shopfleet-quota | sed -n '/Resource/,$p'
say "Recent warning events"
kubectl -n "${NS}" get events --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -15
