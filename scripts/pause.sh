#!/usr/bin/env bash
# Save money between study sessions: scale node pools to zero (cluster + disks + data stay).
# Remaining cost while paused: load balancer (~$0.60/day), disks, static IP. Run resume.sh to continue.
source "$(dirname "$0")/common.sh"
for pool in general spot; do
  say "Pausing node pool ${pool}"
  gcloud container node-pools update "${pool}" --cluster "${CLUSTER}" --zone "${ZONE}" --no-enable-autoscaling --quiet
  gcloud container clusters resize "${CLUSTER}" --node-pool "${pool}" --num-nodes 0 --zone "${ZONE}" --quiet
done
say "Paused. Pods will show Pending -- that's expected."
