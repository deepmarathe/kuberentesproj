# Shortcuts. Run `make help`.
.PHONY: help bootstrap deploy redeploy status chaos pause resume teardown ip creds local

help:            ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

bootstrap:       ## One-time: create GCP infra with Terraform and connect kubectl
	./scripts/bootstrap.sh

deploy:          ## Build all images in Cloud Build and deploy to GKE
	./scripts/build-and-deploy.sh

redeploy:        ## Re-apply manifests with the last built tag (no rebuild)
	./scripts/deploy.sh

status:          ## On-call overview of the cluster
	./scripts/status.sh

chaos:           ## List chaos scenarios
	./scripts/chaos.sh list

ip:              ## Print the storefront URL
	@echo "http://$$(gcloud compute addresses describe shopfleet-ip --global --format='value(address)')"

creds:           ## Re-connect kubectl to the cluster (e.g. new Cloud Shell session)
	gcloud container clusters get-credentials shopfleet --zone $${ZONE:-us-central1-a}

pause:           ## Scale node pools to 0 to save money
	./scripts/pause.sh

resume:          ## Bring node pools back
	./scripts/resume.sh

teardown:        ## Delete everything
	./scripts/teardown.sh

local:           ## Run on a local kind cluster
	./scripts/local-kind.sh
