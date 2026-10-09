# CKA & CKAD with ShopFleet

This track turns ShopFleet into a CKA/CKAD training ground: an exam-shaped cluster, **55 auto-graded exam-style tasks**, timed mock exams, and hands-on labs for the node-level skills the CKA tests.

## What the exams actually are

| | CKAD | CKA |
|---|---|---|
| Format | ~15–20 hands-on tasks in a terminal, 2 hours | ~15–20 hands-on tasks in a terminal, 2 hours |
| Pass mark | 66% | 66% |
| Focus | Building and running *applications*: pods, deployments, config, probes, Helm, Kustomize, services, policies | Running the *cluster*: kubeadm install and upgrade, etcd backup and restore, nodes, networking, storage, **troubleshooting (30%)** |
| Allowed docs | kubernetes.io/docs, kubernetes.io/blog, helm.sh/docs (one extra browser tab) | same |
| Environment | Remote desktop, Linux terminal, several clusters (each task says which `kubectl config use-context` to run first) | same, plus `ssh` into nodes and `sudo -i` |
| Included | 2 sessions of the killer.sh simulator (harder than the real exam: use them in the last 2 weeks) | same |

Always check the **current curriculum** at training.linuxfoundation.org. It changes every year or two; the 2025 updates added Helm, Kustomize, the Gateway API, and CRDs/operators.

## Why this repo gets you there faster

Passing comes down to **speed and muscle memory under time pressure**, not reading. Everything here is built for repetition:

1. **`./exam/trainer`** sets up a realistic, often broken, scenario on your cluster. You solve it with `kubectl` the way you would in the exam, and it **grades the result** in seconds. Repeat until every task shows ✓, then run timed mock exams.
2. **An exam-shaped cluster**: `cp1` + `node01` + `node02`, built with kubeadm, containerd, and Calico, where you `ssh node01` and `sudo -i`. That's the only way to practise etcd restores, kubeadm upgrades, broken kubelets, and static pods. GKE hides all of that.
3. **A real app on top.** Breaking ShopFleet's Postgres Service or scheduler has visible consequences in the store and in the in-app **Cluster** page. You learn to *diagnose*, not just to type YAML.

## Start here

1. **Pick an environment:** [01-environments.md](01-environments.md). The recommended one is kubeadm on 3 GCE VMs, at ~$3/day while running and cents while stopped.
2. **Set up an exam-like shell** (aliases, vim, completion): [03-exam-technique.md](03-exam-technique.md)
3. **Follow the plan:** [02-study-plan.md](02-study-plan.md), 6 weeks at ~1–1.5 h/day.
4. **Coverage check:** [04-curriculum-map.md](04-curriculum-map.md) maps every official curriculum bullet to tasks and labs.

## The trainer in 60 seconds

```bash
./exam/trainer env                 # what environment is this, which tasks can run here
./exam/trainer list --track ckad   # tasks by official domain, ✓ when you've mastered one
./exam/trainer start ckad-08       # sets up the scenario, prints the question
#   ... solve it with kubectl ...
./exam/trainer check ckad-08       # ✓/✗ per requirement + points + your time vs. exam pace
./exam/trainer solution ckad-08    # only after a real attempt!
./exam/trainer reset ckad-08

./exam/trainer exam ckad           # 16 random tasks weighted like the real exam, 120 min
./exam/trainer exam status         # questions + time left (questions also in ~/exam-answers/EXAM.md)
./exam/trainer exam grade          # score vs. the 66% pass mark
./exam/trainer progress            # mastery per domain
```

Graders check the **result**, not the method. Any correct approach (imperative, YAML, edit, patch) passes. Every task's reference solution is tested to score full marks, and every task scores **zero** before you do any work, so the points mean something.

## Labs (node-level and deeper topics)

| Lab | Exam |
|---|---|
| [01 – Install a cluster with kubeadm (by hand)](labs/01-kubeadm-install.md) | CKA |
| [02 – Upgrade the cluster (control plane + workers)](labs/02-cluster-upgrade.md) | CKA |
| [03 – etcd backup & restore](labs/03-etcd-backup-restore.md) | CKA |
| [04 – Control-plane & node troubleshooting](labs/04-control-plane-troubleshooting.md) | CKA |
| [05 – Helm & Kustomize with the ShopFleet chart](labs/05-helm-and-kustomize.md) | CKA + CKAD |
| [06 – CRDs & operators: inside the FlashSale operator](labs/06-crds-and-operators.md) | CKA + CKAD |
| [07 – App architecture tour: every exam concept in ShopFleet](labs/07-architecture-tour.md) | CKAD |

The GKE operations labs in [../labs/](../labs/README.md) are excellent CKA troubleshooting practice too, especially 01, 04, 05, 06, 08, 10, 12, and 16.
