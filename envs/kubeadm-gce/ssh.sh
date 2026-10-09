#!/usr/bin/env bash
# SSH into a node as "student" (default cp1).   ./ssh.sh node01
source "$(dirname "$0")/common.sh"
exec gcloud compute ssh "${1:-cp1}" --zone "$ZONE" --tunnel-through-iap -- -t "sudo -iu student"
