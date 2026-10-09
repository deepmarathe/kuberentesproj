#!/usr/bin/env bash
# Stop the VMs between study sessions (disks are kept; the cluster comes back on start).
source "$(dirname "$0")/common.sh"
gcloud compute instances "${1:-stop}" cp1 node01 node02 --zone "$ZONE"
