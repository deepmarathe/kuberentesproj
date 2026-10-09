# 03 – Exam map

> **Preparing for CKA or CKAD?** Use the dedicated track instead: [cka-ckad/README.md](cka-ckad/README.md). It has an exam-shaped kubeadm cluster, 55 auto-graded tasks, and mock exams. This page focuses on Google Cloud exams (ACE / Professional DevOps).

Google Cloud has no certification called just "the GKE exam". GKE is heavily tested in:
- **Associate Cloud Engineer (ACE)**: deploying and managing GKE, `gcloud`/`kubectl`, IAM, monitoring
- **Professional Cloud DevOps Engineer** and **Professional Cloud Architect / Developer**: SRE practice, CI/CD, SLOs, design trade-offs

The CNCF **CKA / CKAD** exams are hands-on kubectl tests.

This map covers all of them. Check your exam's current official guide and weigh the sections accordingly.

## Topic → where to practise

| Topic | Labs / files |
|---|---|
| Create & configure GKE clusters (Standard vs Autopilot, zonal vs regional, private, VPC-native, release channels) | `terraform/main.tf`, 00-concepts, labs 08, 16, 18 |
| Node pools, machine types, Spot, autoscaling, taints/tolerations | `terraform/main.tf`, labs 07, 08, 16 |
| Deploy workloads: Deployments, StatefulSets, Jobs/CronJobs, Services | `k8s/base/*`, labs 01, 04, 06, 07 |
| Configure Ingress / Gateway / load balancing, NEGs, static IP, managed certs | `k8s/overlays/gke/*`, lab 14 |
| Autoscaling: HPA, VPA, Cluster Autoscaler | labs 02, 08 |
| Rolling updates, rollbacks, canary, blue/green | lab 04, lab 14 |
| ConfigMaps, Secrets, Secret Manager, KMS encryption | labs 03, 11 |
| IAM vs RBAC, service accounts, Workload Identity, least privilege | labs 09, 10 |
| Network security: NetworkPolicy, private clusters, authorized networks | labs 10, 13 |
| Pod security: securityContext, Pod Security Admission, distroless, non-root | every manifest, labs 08, 12 |
| Artifact Registry, Cloud Build, CI/CD, vulnerability scanning, Binary Authorization | lab 13 |
| Cloud Logging, Cloud Monitoring, Managed Prometheus, alerts, uptime checks, SLOs | 02-day2, lab 17 |
| Troubleshooting: CrashLoopBackOff, ImagePullBackOff, Pending, OOMKilled, no endpoints | labs 01, 04, 05, 08, 12 |
| Storage: PV/PVC, StorageClasses, snapshots, Cloud SQL | labs 06, 15, 18 |
| Upgrades, maintenance windows, PDBs, drains | lab 16 |
| Backup / DR, RPO/RTO, regional design | lab 18 |
| Cost optimisation | 02-day2 (cost hygiene), labs 07, 08 |

## Commands to know cold (practise until you can type them without looking)

```bash
# cluster lifecycle
gcloud container clusters create NAME --zone Z --num-nodes 3 --machine-type e2-standard-2 \
  --enable-autoscaling --min-nodes 1 --max-nodes 5 --workload-pool=PROJECT.svc.id.goog --release-channel regular
gcloud container clusters create-auto NAME --region R                 # Autopilot
gcloud container clusters get-credentials NAME --zone Z
gcloud container clusters resize NAME --node-pool P --num-nodes N --zone Z
gcloud container node-pools create P --cluster NAME --zone Z --spot --machine-type e2-standard-2
gcloud container clusters upgrade NAME --master --cluster-version V --zone Z
gcloud container clusters update NAME --zone Z --enable-autoscaling --node-pool P --min-nodes 1 --max-nodes 4

# imperative kubectl (fast YAML generation)
kubectl create deployment web --image=nginx --replicas=3 --dry-run=client -o yaml > web.yaml
kubectl expose deployment web --port=80 --target-port=8080 --type=ClusterIP
kubectl expose deployment web --port=80 --type=LoadBalancer
kubectl autoscale deployment web --cpu-percent=60 --min=2 --max=10
kubectl create configmap app --from-literal=LOG_LEVEL=info --from-file=config.json
kubectl create secret generic db --from-literal=password=s3cr3t
kubectl create job once --image=busybox -- echo hi
kubectl create cronjob tick --image=busybox --schedule="*/5 * * * *" -- date
kubectl create serviceaccount app
kubectl create role pod-reader --verb=get,list,watch --resource=pods
kubectl create rolebinding rb --role=pod-reader --serviceaccount=default:app
kubectl set image deploy/web nginx=nginx:1.27
kubectl rollout status|history|undo deploy/web
kubectl scale deploy/web --replicas=5
kubectl label pod POD tier=frontend
kubectl annotate sa app iam.gke.io/gcp-service-account=GSA@PROJECT.iam.gserviceaccount.com
kubectl taint nodes NODE dedicated=batch:NoSchedule
kubectl cordon|drain|uncordon NODE
kubectl top pods|nodes
kubectl auth can-i VERB RESOURCE --as=USER -n NS
kubectl explain pod.spec.containers.resources

# Workload Identity in 4 lines
gcloud iam service-accounts create GSA
gcloud projects add-iam-policy-binding PROJECT --role=ROLE --member=serviceAccount:GSA@PROJECT.iam.gserviceaccount.com
gcloud iam service-accounts add-iam-policy-binding GSA@PROJECT.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser --member="serviceAccount:PROJECT.svc.id.goog[NS/KSA]"
kubectl annotate sa KSA -n NS iam.gke.io/gcp-service-account=GSA@PROJECT.iam.gserviceaccount.com
```

## Classic exam-style questions (answer them using this repo)

1. *Pods must access Cloud Storage securely without keys.* → Workload Identity (lab 09).
2. *Minimise cost for fault-tolerant batch jobs.* → Spot node pool + taints/tolerations (lab 07).
3. *App must survive a zone failure.* → Regional cluster, ≥2 replicas spread across zones, PDB, regional storage or Cloud SQL HA (lab 18).
4. *Deploy a new version to 10% of users first.* → Canary: Gateway API weights or Cloud Deploy (labs 04, 14).
5. *Pods stuck Pending with "Insufficient cpu".* → Enable/raise the Cluster Autoscaler max, or right-size requests (lab 08).
6. *Expose an HTTP app globally with TLS.* → Ingress/Gateway + Google-managed certificate + static IP (lab 14).
7. *Restrict which pods can talk to the database.* → NetworkPolicy with Dataplane V2 (lab 10).
8. *Give a developer read-only access to one namespace.* → Minimal IAM (clusterViewer) + RBAC Role/RoleBinding (lab 10).
9. *Least operational overhead for running containers on GKE.* → Autopilot (00-concepts, lab 08).
10. *Find why a container restarts repeatedly.* → `describe` (Last State, exit code), `logs --previous` (labs 01, 05).

## Suggested 3-week plan

| Week | Do |
|---|---|
| 1 | 00-concepts, 01-setup, 02-day2. Labs 01–06. Each day: run `status.sh`, read one manifest end to end, explain every field out loud |
| 2 | Labs 07–13. Rebuild a Deployment + Service + HPA + PDB **from scratch** without looking, using `kubectl create ... --dry-run=client -o yaml` |
| 3 | Labs 14–18. Then chaos drills: have `chaos.sh` pick a random scenario, diagnose it under a 10-minute timer. Practise exams |

Random drill:

```bash
s=$(printf "payment-errors\ncatalog-leak\ncatalog-unready\nbad-image\nbad-config\nkill-redis\nnetpol-break\nworker-stop\n" | shuf -n1)
./scripts/chaos.sh "$s" >/dev/null 2>&1; echo "Something is wrong. Go."   # then: echo $s to check yourself
```
