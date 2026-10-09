# Lab 08 – Capacity: Pending pods, Cluster Autoscaler, quotas, priorities

**Scenario:** A new team deploys a big workload onto the shared cluster. Pods go Pending. Who gets resources, who waits, and does the cluster grow?

## 1. Baseline capacity

```bash
kubectl get nodes -L pool
kubectl describe nodes | grep -A6 "Allocated resources"       # requested vs. allocatable per node
kubectl top nodes
```

**Allocatable** is less than the machine size, because GKE reserves CPU and memory for the kubelet and OS.

## 2. Fill the cluster → Cluster Autoscaler

```bash
./scripts/chaos.sh hog 6            # 6 pods × 700m CPU in namespace "sandbox"
kubectl -n sandbox get pods -w
kubectl get nodes -w                # second terminal
```

**Observe:**
1. Some hog pods are **Pending**. `kubectl -n sandbox describe pod <pending>` shows:
   - `FailedScheduling ... Insufficient cpu`
   - then `TriggeredScaleUp ... pool general`
2. A new node joins within ~1–2 minutes and the pods schedule.
3. Hog has no toleration for the spot taint, so it never lands on spot nodes.
4. If it would exceed `max_node_count` (4), pods stay Pending with `NotTriggerScaleUp ... max node group size reached`.

Logs Explorer → `logName="projects/PROJECT/logs/container.googleapis.com%2Fcluster-autoscaler-visibility"` shows the autoscaler's decisions and reasons.

## 3. Priority and preemption

Fill it up completely:

```bash
kubectl -n sandbox scale deployment hog --replicas=20
kubectl -n shopfleet scale deployment catalog --replicas=6
kubectl -n sandbox get events -w | grep -i preempt
```

- The new catalog pods have `priorityClassName: shopfleet-standard` (10000) and hog has the default (0). When catalog pods can't fit, the scheduler **evicts hog pods to make room**.
- `loadgen` uses `shopfleet-low` with `preemptionPolicy: Never`. It waits rather than kicking anyone out, and it's first to be preempted.
- Check the priority classes with `kubectl get priorityclass`.

## 4. ResourceQuota stops a runaway

```bash
kubectl -n shopfleet scale deployment cart --replicas=60
kubectl -n shopfleet get deploy cart                    # READY stays well below 60
kubectl -n shopfleet describe rs -l app=cart | grep -i quota
kubectl -n shopfleet describe resourcequota shopfleet-quota
```

The ReplicaSet can't create pods: `exceeded quota: shopfleet-quota, requested: pods=1, used: pods=60`, or the CPU equivalent. The quota protected the cluster from a typo.

## 5. Pod Security Admission says no

```bash
kubectl -n shopfleet run test --image=nginx
```

It's rejected: `violates PodSecurity "restricted:latest": allowPrivilegeEscalation != false, runAsNonRoot != true, ...`. The namespace label enforces this for every pod, so it's guardrails by default. (`kubectl run` in the `sandbox` namespace works, because it has no label.)

## 6. VPA recommendations (right-sizing)

```bash
kubectl apply -f k8s/extras/vpa.yaml
# wait 15-30 minutes with traffic, then:
kubectl -n shopfleet describe vpa catalog | sed -n '/Recommendation/,$p'
```

Compare `Target` with the current requests (100m / 192Mi). Over-requesting wastes money, and under-requesting risks evictions and throttling. GKE's Cost Optimization tab shows the same picture.

## Clean up

```bash
./scripts/chaos.sh hog-off
./scripts/deploy.sh                        # resets catalog/cart replicas
```

Unused nodes are removed after ~10 minutes of low utilisation.

## Exam takeaways
- Pending + `Insufficient cpu/memory` → Cluster Autoscaler (if enabled and below max). Pending + `taint` / `affinity` / `PVC` messages → a configuration problem, not capacity.
- The CA scales on **requests**, not actual usage.
- The CA won't remove a node if a pod can't move: a PDB violation, local storage, or a pod without a controller. `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` blocks it on purpose.
- **Autopilot** removes node management entirely: you pay for pod requests, and it enforces the security baseline. Know when you'd pick it over Standard: less ops, less control, no SSH, no privileged pods.

<details><summary>Check yourself</summary>

1. *Nodes are 20% CPU utilised but the CA added a node. Why?* Requests, not usage. Pods requested more than was free.
2. *Quota is `requests.cpu: 6`, and a pod has no CPU request. What happens?* It's rejected, unless a LimitRange supplies a default (ours does: 50m).
3. *What's preempted first?* Lower priority first. Within the same priority, the scheduler minimises disruption (PDB-respecting where possible).
</details>
