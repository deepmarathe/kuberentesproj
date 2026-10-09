# 02 – Six-week study plan (≈1–1.5 h/day)

**Order: CKAD first, then CKA.** CKAD builds the YAML and kubectl speed that the CKA assumes. If you only want the CKA, still do weeks 1–2.

The rhythm for every task:
1. `start`, then solve **without** looking anything up except kubernetes.io/docs.
2. `check`.
3. If it isn't ✓, retry the next day before reading `solution`.
4. A task counts as mastered when you hit ✓ **within its exam pace** (shown after `check`).

## Week 1 – Foundations and speed (CKAD)
- Set up the environment and shell ([01](01-environments.md), [03](03-exam-technique.md)).
- Read [lab 07: architecture tour](labs/07-architecture-tour.md) with the ShopFleet manifests open.
- Tasks: ckad-04, 06, 15, 17, 18, 19, 22, 23
- Daily drill (10 min): create a Deployment, Service, ConfigMap, Secret, and Job **imperatively** with `--dry-run=client -o yaml`, without docs.

## Week 2 – Workloads and deployment (CKAD)
- Tasks: ckad-01, 02, 03, 07, 08, 09, 12, 14
- [Lab 05: Helm & Kustomize](labs/05-helm-and-kustomize.md), then tasks ckad-10, 11
- Break ShopFleet with `./scripts/chaos.sh bad-image` and `bad-config` and fix them without notes.

## Week 3 – Security, networking, CRDs (CKAD) + first mock
- Tasks: ckad-05, 13, 16, 20, 21, 24, 25, 26
- [Lab 06: CRDs & operators](labs/06-crds-and-operators.md)
- **Mock:** `./exam/trainer exam ckad`. Goal: ≥ 66%. Below that, redo the weak domains (`./exam/trainer progress`).

## Week 4 – The cluster itself (CKA)
- [Lab 01: kubeadm install by hand](labs/01-kubeadm-install.md). Use manual mode and do it at least twice.
- [Lab 02: upgrade](labs/02-cluster-upgrade.md) → task cka-02
- [Lab 03: etcd](labs/03-etcd-backup-restore.md) → tasks cka-03, cka-04
- Tasks: cka-01, 05, 06, 07, 08

## Week 5 – Scheduling, networking, storage, troubleshooting (CKA)
- Tasks: cka-09 … cka-20
- [Lab 04: troubleshooting](labs/04-control-plane-troubleshooting.md) → tasks cka-21 … cka-29
- Every day: one random chaos scenario (see [../03-exam-map.md](../03-exam-map.md#suggested-3-week-plan) for the random drill), diagnosed in under 10 minutes.

## Week 6 – Exam simulation
- 3 mock exams per track: `./exam/trainer exam cka --disruptive` and `exam ckad`, in one sitting, 120 minutes, no notes.
- **Book the exam once you consistently score ≥ 80%.** The real exam's environment is slower than your own.
- Use your 2 killer.sh sessions in the final week. Scores there are usually lower than in the real exam, so don't panic.

## Checkpoints

| End of week | You should be able to… |
|---|---|
| 1 | generate any basic object's YAML in < 1 min without docs |
| 2 | do a rolling update and rollback, write a CronJob and sidecar from memory |
| 3 | pass a CKAD mock (≥ 66%) |
| 4 | install a kubeadm cluster from nothing in < 30 min; back up and restore etcd in < 10 min |
| 5 | find the cause of NotReady / Pending / CrashLoop / no-endpoints in < 5 min each |
| 6 | pass both mocks with ≥ 80% inside 120 min |
