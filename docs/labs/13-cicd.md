# Lab 13 – CI/CD: push to git, get a deployment

**Scenario:** Replace "someone runs a script from their laptop" with an auditable pipeline.

## 1. Understand the pipeline

Read [cloudbuild.yaml](../../cloudbuild.yaml):
- 8 build steps with `waitFor: ["-"]` run **in parallel**.
- `push` waits for all of them.
- `deploy` runs `scripts/deploy.sh` inside the `cloud-sdk` image.
- It runs as the dedicated **`shopfleet-cloudbuild`** service account (least privilege: AR writer, GKE developer, logs), not the broad default Cloud Build SA.

## 2. Create a trigger on git push

1. Push this repo to GitHub (private is fine).
2. Console → Cloud Build → **Repositories** → Create host connection → GitHub → link your repo.
3. Cloud Build → **Triggers** → Create:
   - Event: *Push to a branch*, branch `^main$`
   - Configuration: *Cloud Build configuration file*, `cloudbuild.yaml`
   - Substitution variables: `_TAG` = `$SHORT_SHA`, plus `_REGION`, `_ZONE`, `_CLUSTER` if you changed them
   - Service account: `shopfleet-cloudbuild@PROJECT.iam.gserviceaccount.com`
4. Change something visible, such as the brand name in `services/frontend/public/index.html`. Then commit and push.
5. Watch Cloud Build → History. The images are tagged with the **git commit SHA**, so you can trace any running pod back to its exact commit:
   ```bash
   kubectl -n shopfleet get deploy -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image
   ```

**Rollback = redeploy an old SHA:** `TAG=<old-sha> ./scripts/deploy.sh`. The images are immutable in Artifact Registry, so this is fast and exact.

## 3. Vulnerability scanning

```bash
gcloud services enable containerscanning.googleapis.com
# push a new build (trigger or ./scripts/build-and-deploy.sh), then:
gcloud artifacts docker images list us-central1-docker.pkg.dev/$(gcloud config get-value project)/shopfleet --show-occurrences --format=json | head -50
```

Or Console → Artifact Registry → shopfleet → an image → **Vulnerabilities**. Compare the distroless `payment` image (very few CVEs) with the `python:3.12-slim`-based images. That's the practical case for minimal base images.

Going further: **Binary Authorization** can *block* deploying images that weren't built by your pipeline or that have critical CVEs. Understand the concept (attestations + policy enforced at admission) for exams.

## 4. Lock down the control plane (and see what breaks)

The cluster API endpoint is public but filtered by *master authorized networks*, currently `0.0.0.0/0`. Restrict it to your Cloud Shell IP:

```bash
MYIP=$(curl -s https://ifconfig.me); echo $MYIP
cd terraform && terraform apply -var="master_authorized_cidr=$MYIP/32"
kubectl get nodes          # still works from Cloud Shell
```

Now run a build with deploy (push a commit, or run `./scripts/build-and-deploy.sh`). **The deploy step fails** with a timeout reaching the API server, because Cloud Build's default workers use random Google IPs.

Real-world fixes:
- **Cloud Build private pools** peered to your VPC, with a private control-plane endpoint.
- **Cloud Deploy** (Google's managed CD service), which deploys from Google's side.
- **Connect Gateway / Fleet** access.

Cloud Shell's IP also changes between sessions. Revert with `terraform apply` (no `-var`) when you're done.

## Exam takeaways
- Cloud Build: steps are containers; `waitFor` sets parallelism; built-in substitutions include `$PROJECT_ID`, `$SHORT_SHA`, `$BUILD_ID`; user substitutions must start with `_`.
- Artifact Registry replaces Container Registry. The image path is `REGION-docker.pkg.dev/PROJECT/REPO/IMAGE:TAG`.
- Nodes pull images using the **node service account**, which needs `roles/artifactregistry.reader`. Cross-project pulls need that role in the *registry's* project.
- Private cluster vocabulary: private nodes (no external IPs, so NAT is needed for egress), private endpoint (control plane reachable only from the VPC), authorized networks (an allow-list for the public endpoint).
