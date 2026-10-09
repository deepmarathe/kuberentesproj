#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
cd "$HERE" && terraform destroy -auto-approve
