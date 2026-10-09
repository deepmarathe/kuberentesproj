#!/usr/bin/env bash
# Copy this repo to cp1:/home/student/shopfleet (the trainer runs there, like the exam terminal).
source "$(dirname "$0")/common.sh"
tar -C "$ROOT" --exclude=.git --exclude='*/node_modules' --exclude='*/.terraform' --exclude='*.tfstate*' -czf - . | \
  gcloud compute ssh cp1 --zone "$ZONE" --tunnel-through-iap --quiet --command \
  "sudo -u student mkdir -p /home/student/shopfleet && sudo -u student tar -xzf - -C /home/student/shopfleet"
echo "repo copied to cp1:/home/student/shopfleet"
