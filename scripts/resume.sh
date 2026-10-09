#!/usr/bin/env bash
# Bring node pools back after pause.sh.
source "$(dirname "$0")/common.sh"
gcloud container clusters resize "${CLUSTER}" --node-pool general --num-nodes 2 --zone "${ZONE}" --quiet
gcloud container node-pools update general --cluster "${CLUSTER}" --zone "${ZONE}" --enable-autoscaling --min-nodes 1 --max-nodes 4 --quiet
gcloud container clusters resize "${CLUSTER}" --node-pool spot --num-nodes 1 --zone "${ZONE}" --quiet
gcloud container node-pools update spot --cluster "${CLUSTER}" --zone "${ZONE}" --enable-autoscaling --min-nodes 0 --max-nodes 3 --quiet
say "Resumed. Watch pods come back:  kubectl -n ${NS} get pods -w"
