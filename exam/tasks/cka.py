"""CKA tasks. Node-level ones (etcd, kubelet, static pods, upgrades) run on the kubeadm or kind environment.

{cp} = control-plane node, {w1}/{w2} = workers. Reach a node like in the exam:  ssh {w1}   (kind: docker exec -it {w1} bash)
"""
from trainer import Check, Task, jget
from tasks.ckad import endpoints, json_dump, line, ns, rmns

D1, D2, D3, D4, D5 = ("Cluster Architecture, Installation and Configuration", "Workloads and Scheduling",
                      "Services and Networking", "Storage", "Troubleshooting")


def node_obj(c, name):
    return c.get("node", name)


def node_ready(c, name):
    return any(x["type"] == "Ready" and x["status"] == "True" for x in jget(node_obj(c, name), "status.conditions", []))


TASKS = []

# ============================================================================ Architecture, Installation, Configuration
TASKS.append(Task(
    id="cka-01", track="cka", domain=D1, points=6, minutes=6,
    title="RBAC: a cluster-wide node auditor",
    text="""
    The SRE team needs a robot account that can audit nodes cluster-wide but change nothing.

    1. Create ServiceAccount `auditor` in namespace `cka-01`.
    2. Create ClusterRole `node-auditor` allowing get, list, watch on nodes and on persistentvolumes.
    3. Bind it cluster-wide to the ServiceAccount with ClusterRoleBinding `node-auditor-binding`.
    """,
    setup=ns("cka-01"),
    cleanup=rmns("cka-01") + "kubectl delete clusterrole node-auditor --ignore-not-found >/dev/null; kubectl delete clusterrolebinding node-auditor-binding --ignore-not-found >/dev/null",
    checks=[
        Check("can list nodes", lambda c: c.can_i("list", "nodes", "system:serviceaccount:cka-01:auditor")),
        Check("can watch persistentvolumes", lambda c: c.can_i("watch", "persistentvolumes", "system:serviceaccount:cka-01:auditor")),
        Check("can NOT delete nodes", lambda c: c.can_i("list", "nodes", "system:serviceaccount:cka-01:auditor") and not c.can_i("delete", "nodes", "system:serviceaccount:cka-01:auditor")),
        Check("can NOT list pods", lambda c: c.can_i("list", "nodes", "system:serviceaccount:cka-01:auditor") and not c.can_i("list", "pods", "system:serviceaccount:cka-01:auditor", "default")),
        Check("binding is a ClusterRoleBinding named node-auditor-binding", lambda c: jget(c.get("clusterrolebinding", "node-auditor-binding"), "roleRef.name") == "node-auditor"),
    ],
    solution="""
    kubectl -n cka-01 create serviceaccount auditor
    kubectl create clusterrole node-auditor --verb=get,list,watch --resource=nodes,persistentvolumes
    kubectl create clusterrolebinding node-auditor-binding --clusterrole=node-auditor --serviceaccount=cka-01:auditor
    kubectl auth can-i list nodes --as=system:serviceaccount:cka-01:auditor
    """,
))

TASKS.append(Task(
    id="cka-02", track="cka", domain=D1, points=13, minutes=20, needs={"kubeadm"}, destructive=True,
    title="Upgrade the control plane with kubeadm",
    text="""
    The cluster runs Kubernetes {cp_version}. Upgrade the CONTROL PLANE node `{cp}` to the next minor version
    (latest patch). Workers stay as they are.

    - drain {cp} before upgrading kubelet/kubectl, uncordon it afterwards
    - upgrade kubeadm, run `kubeadm upgrade plan` / `kubeadm upgrade apply`, then upgrade kubelet + kubectl and restart kubelet

    Full walkthrough: docs/cka-ckad/labs/02-cluster-upgrade.md
    """,
    checks=[
        Check("control-plane kubelet is on a newer minor than workers",
              lambda c: minor(jget(node_obj(c, c.v["cp"]), "status.nodeInfo.kubeletVersion")) > minor(jget(node_obj(c, c.v["w1"]), "status.nodeInfo.kubeletVersion"))),
        Check("API server reports the new version", lambda c: minor(c.kubectl("version", "-o", "json").stdout and __import__("json").loads(c.kubectl("version", "-o", "json").stdout)["serverVersion"]["gitVersion"])
              > minor(jget(node_obj(c, c.v["w1"]), "status.nodeInfo.kubeletVersion"))),
        Check("control plane is Ready and schedulable (uncordoned)", lambda c: node_ready(c, c.v["cp"]) and not jget(node_obj(c, c.v["cp"]), "spec.unschedulable", False)),
    ],
    solution="""
    # on the control plane (ssh $CP), as root.  Example: 1.34 -> 1.35  (adjust versions!)
    NEXT=1.35
    sed -i "s#/v1\\.[0-9]*/#/v$NEXT/#" /etc/apt/sources.list.d/kubernetes.list && apt-get update
    apt-cache madison kubeadm | head -3            # pick the latest patch, e.g. 1.35.1-1.1
    apt-mark unhold kubeadm && apt-get install -y kubeadm='1.35.*' && apt-mark hold kubeadm
    kubeadm upgrade plan
    kubeadm upgrade apply v1.35.1 -y               # the version kubeadm plan suggested
    exit   # back to student
    kubectl drain $CP --ignore-daemonsets --delete-emptydir-data
    ssh $CP 'sudo apt-mark unhold kubelet kubectl && sudo apt-get install -y kubelet="1.35.*" kubectl="1.35.*" && sudo apt-mark hold kubelet kubectl && sudo systemctl daemon-reload && sudo systemctl restart kubelet'
    kubectl uncordon $CP
    kubectl get nodes
    """,
))

TASKS.append(Task(
    id="cka-03", track="cka", domain=D1, points=7, minutes=7, needs={"node"},
    title="Back up etcd",
    text="""
    Take a snapshot of the cluster's etcd and save it ON THE CONTROL PLANE NODE `{cp}` at
    `/opt/backup/etcd-snapshot.db`.

    etcd listens on https://127.0.0.1:2379. Find the CA, server cert and key in the etcd static pod
    manifest (/etc/kubernetes/manifests/etcd.yaml). Then verify the snapshot with `etcdutl snapshot status`
    (or `etcdctl snapshot status`).
    """,
    setup="node_sh $CP 'rm -f /opt/backup/etcd-snapshot.db'",
    checks=[
        Check("snapshot file exists on the control plane", lambda c: c.node(c.v["cp"], "test -s /opt/backup/etcd-snapshot.db")[0] == 0),
        Check("snapshot is valid and recent (< 1 day)", lambda c: c.node(c.v["cp"], "find /opt/backup/etcd-snapshot.db -mtime -1 | grep -q . && "
              "(command -v etcdutl >/dev/null && etcdutl snapshot status /opt/backup/etcd-snapshot.db || ETCDCTL_API=3 etcdctl snapshot status /opt/backup/etcd-snapshot.db) "
              "|| (crictl ps -q --name etcd | head -1 | xargs -I{} sh -c 'cp /opt/backup/etcd-snapshot.db /var/lib/etcd/.chk.db && crictl exec {} etcdutl snapshot status /var/lib/etcd/.chk.db; rc=$?; rm -f /var/lib/etcd/.chk.db; exit $rc')")[0] == 0),
    ],
    solution="""
    ssh $CP            # (kind: docker exec -it $CP bash)
    sudo -i
    grep -E 'cert-file|key-file|trusted-ca-file|listen-client' /etc/kubernetes/manifests/etcd.yaml
    mkdir -p /opt/backup
    ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 \\
      --cacert=/etc/kubernetes/pki/etcd/ca.crt \\
      --cert=/etc/kubernetes/pki/etcd/server.crt \\
      --key=/etc/kubernetes/pki/etcd/server.key \\
      snapshot save /opt/backup/etcd-snapshot.db
    etcdutl snapshot status /opt/backup/etcd-snapshot.db -w table
    # no etcdctl on the node (kind)? run it inside the etcd pod, which mounts /var/lib/etcd:
    #   kubectl -n kube-system exec etcd-$CP -- etcdctl ... snapshot save /var/lib/etcd/snap.db  && copy it out of /var/lib/etcd
    """,
))

TASKS.append(Task(
    id="cka-04", track="cka", domain=D1, points=13, minutes=15, needs={"kubeadm"}, destructive=True,
    title="Restore etcd after an incident",
    text="""
    INCIDENT: someone deleted namespace `cka-precious` (and the ConfigMap `ledger` in it).
    A snapshot taken before the incident exists on {cp} at /opt/backup/etcd-before-incident.db.

    Restore the cluster state from that snapshot:
    - restore into a NEW data directory /var/lib/etcd-restored
    - point the etcd static pod at it (edit /etc/kubernetes/manifests/etcd.yaml: the etcd-data hostPath)
    - wait for the API server to come back
    Afterwards `kubectl get configmap ledger -n cka-precious` must work again.
    (Objects created after the snapshot will be gone -- that's what a restore means.)
    """,
    setup="""
    kubectl create namespace cka-precious --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    kubectl -n cka-precious create configmap ledger --from-literal=balance=1000000 --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    sleep 2
    node_sh $CP 'mkdir -p /opt/backup && ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key snapshot save /opt/backup/etcd-before-incident.db' >/dev/null
    kubectl delete namespace cka-precious --wait=true >/dev/null
    """,
    cleanup=rmns("cka-precious"),
    checks=[
        Check("configmap ledger is back", lambda c: jget(c.get("cm", "ledger", "cka-precious"), "data.balance") == "1000000"),
        Check("etcd uses /var/lib/etcd-restored", lambda c: "/var/lib/etcd-restored" in c.node(c.v["cp"], "cat /etc/kubernetes/manifests/etcd.yaml")[1]),
        Check("API server healthy", lambda c: c.kubectl("get", "--raw", "/readyz").stdout.strip() == "ok"),
    ],
    solution="""
    ssh $CP; sudo -i
    etcdutl snapshot restore /opt/backup/etcd-before-incident.db --data-dir /var/lib/etcd-restored
    #   (older: ETCDCTL_API=3 etcdctl snapshot restore ... --data-dir /var/lib/etcd-restored)
    vi /etc/kubernetes/manifests/etcd.yaml     # volumes: name etcd-data -> hostPath.path: /var/lib/etcd-restored
    # kubelet notices the manifest change and recreates etcd; the API server reconnects (1-2 minutes)
    watch crictl ps                            # etcd + kube-apiserver Running again
    kubectl get cm ledger -n cka-precious
    """,
))

TASKS.append(Task(
    id="cka-05", track="cka", domain=D1, points=6, minutes=6, needs={"runtime"},
    title="Operators and CRDs: inspect the FlashSale operator",
    text="""
    ShopFleet runs a custom operator (Deployment `flashsale-operator`, namespace `shopfleet`) with leader election.

    Write to `{answers}/cka-05.txt`, one per line:
    1. the API group of the FlashSale CRD
    2. the name of the operator pod that currently holds the leader Lease (Lease `flashsale-operator`)
    3. the number of FlashSale objects in namespace shopfleet whose status.phase is Active
    """,
    setup='rm -f "$ANSWERS/cka-05.txt"', cleanup='rm -f "$ANSWERS/cka-05.txt"',
    checks=[
        Check("API group", lambda c: line(c, "cka-05.txt", 0) == "shopfleet.io"),
        Check("leader pod", lambda c: line(c, "cka-05.txt", 1) == jget(c.get("lease", "flashsale-operator", "shopfleet"), "spec.holderIdentity")),
        Check("active FlashSales", lambda c: line(c, "cka-05.txt", 2) == str(sum(1 for f in c.items("flashsales", "shopfleet") if jget(f, "status.phase") == "Active"))),
    ],
    solution="""
    {
      kubectl get crd flashsales.shopfleet.io -o jsonpath='{.spec.group}'; echo
      kubectl -n shopfleet get lease flashsale-operator -o jsonpath='{.spec.holderIdentity}'; echo
      kubectl -n shopfleet get flashsales -o json | jq '[.items[] | select(.status.phase=="Active")] | length'
    } > $ANSWERS/cka-05.txt
    # no jq?  kubectl -n shopfleet get fs --no-headers | awk '$4=="Active"' | wc -l
    """,
))

TASKS.append(Task(
    id="cka-06", track="cka", domain=D1, points=5, minutes=5,
    title="Cluster extension interfaces: CNI, CRI, CSI",
    text="""
    Write to `{answers}/cka-06.txt`, one per line:
    1. the container runtime (name + version) of node `{w1}` exactly as `kubectl get nodes -o wide` shows it
    2. the pod CIDR assigned to node `{w1}` (spec.podCIDR, or "none" if empty)
    3. the name of the cluster's DEFAULT StorageClass provisioner
    """,
    setup='rm -f "$ANSWERS/cka-06.txt"', cleanup='rm -f "$ANSWERS/cka-06.txt"',
    checks=[
        Check("container runtime", lambda c: line(c, "cka-06.txt", 0) == jget(node_obj(c, c.v["w1"]), "status.nodeInfo.containerRuntimeVersion")),
        Check("pod CIDR", lambda c: line(c, "cka-06.txt", 1) == (jget(node_obj(c, c.v["w1"]), "spec.podCIDR") or "none")),
        Check("default provisioner", lambda c: line(c, "cka-06.txt", 2) == next((s["provisioner"] for s in c.items("storageclass") if jget(s, "metadata.annotations", {}).get("storageclass.kubernetes.io/is-default-class") == "true"), "none")),
    ],
    solution="""
    {
      kubectl get node $W1 -o jsonpath='{.status.nodeInfo.containerRuntimeVersion}'; echo
      CIDR=$(kubectl get node $W1 -o jsonpath='{.spec.podCIDR}'); echo "${CIDR:-none}"
      PROV=$(kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class=="true")].provisioner}'); echo "${PROV:-none}"
    } > $ANSWERS/cka-06.txt
    """,
))

TASKS.append(Task(
    id="cka-07", track="cka", domain=D1, points=7, minutes=7, needs={"helm"},
    title="Install a cluster component with Helm",
    text="""
    Install the chart `{repo}/exam/charts/mini-web` as release `edge-proxy` in namespace `cka-07`
    (create it) with 3 replicas and service type NodePort, using a values FILE you write at
    `{answers}/edge-values.yaml` (not --set).
    Then write the list of ALL Helm releases in ALL namespaces to `{answers}/cka-07.txt`.
    """,
    setup="helm uninstall edge-proxy -n cka-07 >/dev/null 2>&1 || true\n" + rmns("cka-07") + 'rm -f "$ANSWERS/edge-values.yaml" "$ANSWERS/cka-07.txt"',
    cleanup="helm uninstall edge-proxy -n cka-07 >/dev/null 2>&1 || true\n" + rmns("cka-07"),
    checks=[
        Check("values file sets replicaCount 3 and NodePort", lambda c: "replicaCount: 3" in (c.answer("edge-values.yaml") or "") and "NodePort" in (c.answer("edge-values.yaml") or "")),
        Check("release deployed", lambda c: "edge-proxy" in c.sh("helm list -n cka-07 -q").stdout.split()),
        Check("3 replicas, NodePort service", lambda c: jget(c.get("deploy", "edge-proxy-mini-web", "cka-07"), "spec.replicas") == 3 and jget(c.get("svc", "edge-proxy-mini-web", "cka-07"), "spec.type") == "NodePort"),
        Check("all-namespaces release list written", lambda c: "edge-proxy" in (c.answer("cka-07.txt") or "") and "NAMESPACE" in (c.answer("cka-07.txt") or "")),
    ],
    solution="""
    cat > $ANSWERS/edge-values.yaml <<'EOF'
    replicaCount: 3
    service:
      type: NodePort
    EOF
    helm install edge-proxy $REPO/exam/charts/mini-web -n cka-07 --create-namespace -f $ANSWERS/edge-values.yaml
    helm list -A > $ANSWERS/cka-07.txt
    """,
))

TASKS.append(Task(
    id="cka-08", track="cka", domain=D1, points=6, minutes=7, needs={"node"},
    title="Static pod on a worker",
    text="""
    Create a static pod named `node-probe` on node `{w1}`: image `busybox:1.36`, command `sleep 1d`,
    in the default namespace. It must be managed by {w1}'s kubelet (not the API server).
    Find the static pod path in the kubelet config (/var/lib/kubelet/config.yaml).
    """,
    setup="node_sh $W1 'rm -f /etc/kubernetes/manifests/node-probe.yaml'",
    cleanup="node_sh $W1 'rm -f /etc/kubernetes/manifests/node-probe.yaml' || true",
    checks=[
        Check("manifest present on the node", lambda c: c.node(c.v["w1"], "grep -l node-probe $(awk '/staticPodPath/ {print $2}' /var/lib/kubelet/config.yaml)/*")[0] == 0),
        Check(f"mirror pod node-probe-<node> exists", lambda c: c.get("pod", f"node-probe-{c.v['w1']}", "default") is not None),
        Check("mirror pod Running", lambda c: jget(c.get("pod", f"node-probe-{c.v['w1']}", "default"), "status.phase") == "Running", runtime=True),
    ],
    solution="""
    kubectl run node-probe --image=busybox:1.36 --dry-run=client -o yaml --command -- sleep 1d > /tmp/node-probe.yaml
    ssh $W1 'sudo grep staticPodPath /var/lib/kubelet/config.yaml'     # /etc/kubernetes/manifests
    scp /tmp/node-probe.yaml $W1:/tmp/ && ssh $W1 'sudo cp /tmp/node-probe.yaml /etc/kubernetes/manifests/'
    # kind:  docker cp /tmp/node-probe.yaml $W1:/etc/kubernetes/manifests/
    kubectl get pod node-probe-$W1
    """,
))

# ============================================================================ Workloads and Scheduling
TASKS.append(Task(
    id="cka-09", track="cka", domain=D2, points=7, minutes=7,
    title="Taints, tolerations and node selection",
    text="""
    Node `{w2}` is reserved for analytics.

    1. Taint it with key `dedicated`, value `analytics`, effect NoSchedule.
    2. In namespace `cka-09` create Deployment `analytics-runner` (image busybox:1.36, command `sleep 1d`, 2 replicas)
       whose pods tolerate that taint AND are forced to run on {w2} (use the node's hostname label).
    """,
    setup=ns("cka-09") + "kubectl taint node $W2 dedicated- >/dev/null 2>&1 || true",
    cleanup=rmns("cka-09") + "kubectl taint node $W2 dedicated- >/dev/null 2>&1 || true",
    checks=[
        Check("taint dedicated=analytics:NoSchedule on w2", lambda c: {"key": "dedicated", "value": "analytics", "effect": "NoSchedule"} in jget(node_obj(c, c.v["w2"]), "spec.taints", [])),
        Check("toleration present", lambda c: any(t.get("key") == "dedicated" and t.get("effect") in (None, "NoSchedule") for t in jget(c.get("deploy", "analytics-runner", "cka-09"), "spec.template.spec.tolerations", []))),
        Check("2 pods, all on w2", lambda c: (lambda ps: len(ps) == 2 and all(p["spec"].get("nodeName") == c.v["w2"] for p in ps))(c.items("pods", "cka-09", "app=analytics-runner"))),
    ],
    solution="""
    kubectl taint node $W2 dedicated=analytics:NoSchedule
    kubectl -n cka-09 create deployment analytics-runner --image=busybox:1.36 --replicas=2 --dry-run=client -o yaml -- sleep 1d > /tmp/ar.yaml
    # add under spec.template.spec:
    #   tolerations: [{key: dedicated, operator: Equal, value: analytics, effect: NoSchedule}]
    #   nodeSelector: {kubernetes.io/hostname: <w2>}
    kubectl apply -f /tmp/ar.yaml
    kubectl -n cka-09 patch deploy analytics-runner -p '{"spec":{"template":{"spec":{"tolerations":[{"key":"dedicated","operator":"Equal","value":"analytics","effect":"NoSchedule"}],"nodeSelector":{"kubernetes.io/hostname":"'$W2'"}}}}}'
    kubectl -n cka-09 get pods -o wide
    """,
))

TASKS.append(Task(
    id="cka-10", track="cka", domain=D2, points=6, minutes=6,
    title="Spread replicas across nodes",
    text="""
    In namespace `cka-10` create Deployment `spread-web` (image nginx:1.27-alpine, 4 replicas) such that
    no two pods may run on the same node unless unavoidable -- use a topologySpreadConstraint with
    topologyKey kubernetes.io/hostname, maxSkew 1, whenUnsatisfiable DoNotSchedule.
    How many pods end up Pending, and why? Write the number to `{answers}/cka-10.txt`.
    """,
    setup=ns("cka-10") + 'rm -f "$ANSWERS/cka-10.txt"', cleanup=rmns("cka-10"),
    checks=[
        Check("constraint configured", lambda c: (lambda t: t and t.get("topologyKey") == "kubernetes.io/hostname" and t.get("maxSkew") == 1 and t.get("whenUnsatisfiable") == "DoNotSchedule")
              (jget(c.get("deploy", "spread-web", "cka-10"), "spec.template.spec.topologySpreadConstraints.0"))),
        Check("constraint selects the pods", lambda c: jget(c.get("deploy", "spread-web", "cka-10"), "spec.template.spec.topologySpreadConstraints.0.labelSelector.matchLabels.app") == "spread-web"),
        Check("Pending count written correctly", lambda c: (c.answer("cka-10.txt") or "x") == str(sum(1 for p in c.items("pods", "cka-10", "app=spread-web") if jget(p, "status.phase") == "Pending"))),
    ],
    solution="""
    kubectl -n cka-10 create deployment spread-web --image=nginx:1.27-alpine --replicas=4 --dry-run=client -o yaml > /tmp/sw.yaml
    kubectl apply -f /tmp/sw.yaml
    kubectl -n cka-10 patch deploy spread-web -p '{"spec":{"template":{"spec":{"topologySpreadConstraints":[{"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"DoNotSchedule","labelSelector":{"matchLabels":{"app":"spread-web"}}}]}}}}'
    sleep 8
    kubectl -n cka-10 get pods -o wide
    # with 2 schedulable workers, 4 pods spread 2/2 -> 0 Pending (the control plane is tainted, so it is not counted)
    kubectl -n cka-10 get pods --field-selector=status.phase=Pending --no-headers 2>/dev/null | wc -l > $ANSWERS/cka-10.txt
    """,
))

TASKS.append(Task(
    id="cka-11", track="cka", domain=D2, points=5, minutes=5,
    title="Priority and preemption",
    text="""
    1. Create PriorityClass `exam-critical` with value 500000, preemptionPolicy PreemptLowerPriority, not global default.
    2. In namespace `cka-11` create Pod `payments-hotfix` (image nginx:1.27-alpine) using that priority class.
    3. Write the priority VALUE the pod ended up with to `{answers}/cka-11.txt`.
    """,
    setup=ns("cka-11"), cleanup=rmns("cka-11") + "kubectl delete priorityclass exam-critical --ignore-not-found >/dev/null",
    checks=[
        Check("priorityclass value 500000", lambda c: jget(c.get("priorityclass", "exam-critical"), "value") == 500000),
        Check("not global default", lambda c: c.get("priorityclass", "exam-critical") is not None and not jget(c.get("priorityclass", "exam-critical"), "globalDefault", False)),
        Check("pod uses it", lambda c: jget(c.get("pod", "payments-hotfix", "cka-11"), "spec.priorityClassName") == "exam-critical"),
        Check("answer 500000", lambda c: (c.answer("cka-11.txt") or "") == "500000"),
    ],
    solution="""
    kubectl create priorityclass exam-critical --value=500000 --preemption-policy=PreemptLowerPriority --description="hotfixes"
    kubectl -n cka-11 run payments-hotfix --image=nginx:1.27-alpine --overrides='{"spec":{"priorityClassName":"exam-critical"}}'
    kubectl -n cka-11 get pod payments-hotfix -o jsonpath='{.spec.priority}' > $ANSWERS/cka-11.txt
    """,
))

TASKS.append(Task(
    id="cka-12", track="cka", domain=D2, points=6, minutes=6,
    title="HorizontalPodAutoscaler with scaling behaviour",
    text="""
    In namespace `cka-12`, Deployment `api` exists. Create an HPA named `api` (autoscaling/v2):
    - target average CPU utilization 50%
    - min 2, max 6 replicas
    - scale-down stabilization window of 30 seconds
    """,
    setup=ns("cka-12") + """
    kubectl -n cka-12 create deployment api --image=nginx:1.27-alpine --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    kubectl -n cka-12 set resources deployment api --requests=cpu=50m >/dev/null
    """,
    cleanup=rmns("cka-12"),
    checks=[
        Check("targets deployment api", lambda c: jget(c.get("hpa", "api", "cka-12"), "spec.scaleTargetRef.name") == "api"),
        Check("min 2 / max 6", lambda c: (jget(c.get("hpa", "api", "cka-12"), "spec.minReplicas"), jget(c.get("hpa", "api", "cka-12"), "spec.maxReplicas")) == (2, 6)),
        Check("cpu 50%", lambda c: jget(c.get("hpa", "api", "cka-12"), "spec.metrics.0.resource.target.averageUtilization") == 50),
        Check("scaleDown stabilization 30s", lambda c: jget(c.get("hpa", "api", "cka-12"), "spec.behavior.scaleDown.stabilizationWindowSeconds") == 30),
    ],
    solution="""
    kubectl -n cka-12 autoscale deployment api --cpu-percent=50 --min=2 --max=6
    kubectl -n cka-12 patch hpa api -p '{"spec":{"behavior":{"scaleDown":{"stabilizationWindowSeconds":30}}}}'
    """,
))

TASKS.append(Task(
    id="cka-13", track="cka", domain=D2, points=6, minutes=6,
    title="Rotate a Secret consumed as a file",
    text="""
    ShopFleet signs login tokens with the key in Secret `jwt-signing-key` (namespace `shopfleet`, key `jwt-secret`),
    mounted as a FILE into auth, catalog, orders, notifications and ops-api.

    Rotate the key to the value `rotated-key-0001-abcdef0123456789` WITHOUT restarting any pods.
    Then confirm (from inside an auth pod) that the mounted file shows the new value -- it can take ~1 minute.
    """,
    setup="""
    kubectl -n shopfleet create secret generic jwt-signing-key --from-literal=jwt-secret=pre-exam-signing-key-$(date +%s) \\
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1
    """,
    cleanup="",
    checks=[
        Check("secret holds the new key", lambda c: __import__("base64").b64decode(jget(c.get("secret", "jwt-signing-key", "shopfleet"), "data.jwt-secret", "")).decode() == "rotated-key-0001-abcdef0123456789"),
        Check("auth still Ready after rotation", lambda c: b"rotated-key-0001" in __import__("base64").b64decode(jget(c.get("secret", "jwt-signing-key", "shopfleet"), "data.jwt-secret", "")) and c.deploy_ready("auth", "shopfleet")),
        Check("mounted file updated inside the pod", lambda c: "rotated-key-0001" in c.kubectl("exec", "-n", "shopfleet", "deploy/auth", "--", "cat", "/etc/shopfleet/jwt/jwt-secret").stdout, runtime=True),
    ],
    solution="""
    kubectl -n shopfleet create secret generic jwt-signing-key --from-literal=jwt-secret=rotated-key-0001-abcdef0123456789 \\
      --dry-run=client -o yaml | kubectl apply -f -
    # secret VOLUMES are refreshed by the kubelet (eventually); env vars would need a restart.
    sleep 60; kubectl -n shopfleet exec deploy/auth -- cat /etc/shopfleet/jwt/jwt-secret || true
    # Note: existing logins become invalid -- users must log in again. That's what rotation means.
    """,
))

# ============================================================================ Services and Networking
TASKS.append(Task(
    id="cka-14", track="cka", domain=D3, points=7, minutes=8,
    title="Gateway API: route by path and header",
    text="""
    In namespace `shopfleet`:
    1. Create a Gateway `exam-gw` with gatewayClassName `{gateway_class}` and one HTTP listener named `web` on port {gateway_port}.
    2. Create an HTTPRoute `exam-route` attached to it that sends requests whose path starts with /api/catalog
       AND that carry header `X-Canary: yes` to Service `catalog` port 8080, and every other /api/catalog
       request to Service `catalog` too -- but `/` must go to Service `frontend` port 8080.
       (Two rules are enough: one matching path+header, one matching just the path prefix "/".)
    """,
    setup="kubectl -n shopfleet delete httproute exam-route --ignore-not-found >/dev/null 2>&1; kubectl -n shopfleet delete gateway exam-gw --ignore-not-found >/dev/null 2>&1; true",
    cleanup="kubectl -n shopfleet delete httproute exam-route --ignore-not-found >/dev/null 2>&1; kubectl -n shopfleet delete gateway exam-gw --ignore-not-found >/dev/null 2>&1; true",
    needs={"gateway"},
    checks=[
        Check("gateway class + HTTP listener", lambda c: jget(c.get("gateway", "exam-gw", "shopfleet"), "spec.gatewayClassName") == c.v["gateway_class"]
              and jget(c.get("gateway", "exam-gw", "shopfleet"), "spec.listeners.0.protocol") == "HTTP"),
        Check("route attached to exam-gw", lambda c: jget(c.get("httproute", "exam-route", "shopfleet"), "spec.parentRefs.0.name") == "exam-gw"),
        Check("rule with path /api/catalog + header X-Canary: yes -> catalog:8080", lambda c: any(
            any(jget(m, "path.value") == "/api/catalog" and any(h.get("name", "").lower() == "x-canary" and h.get("value") == "yes" for h in m.get("headers", [])) for m in r.get("matches", []))
            and any(b.get("name") == "catalog" and b.get("port") == 8080 for b in r.get("backendRefs", []))
            for r in jget(c.get("httproute", "exam-route", "shopfleet"), "spec.rules", []))),
        Check("/ -> frontend:8080", lambda c: any(any(jget(m, "path.value") == "/" for m in r.get("matches", [{"path": {"value": "/"}}])) and any(b.get("name") == "frontend" for b in r.get("backendRefs", []))
                                                 for r in jget(c.get("httproute", "exam-route", "shopfleet"), "spec.rules", []))),
        Check("route Accepted by the controller", lambda c: "Accepted" in json_dump(jget(c.get("httproute", "exam-route", "shopfleet"), "status")), runtime=True),
    ],
    solution="""
    kubectl apply -n shopfleet -f - <<EOF
    apiVersion: gateway.networking.k8s.io/v1
    kind: Gateway
    metadata: { name: exam-gw }
    spec:
      gatewayClassName: $GATEWAY_CLASS
      listeners:
        - { name: web, protocol: HTTP, port: 8000 }
    ---
    apiVersion: gateway.networking.k8s.io/v1
    kind: HTTPRoute
    metadata: { name: exam-route }
    spec:
      parentRefs: [{ name: exam-gw }]
      rules:
        - matches:
            - path: { type: PathPrefix, value: /api/catalog }
              headers: [{ name: X-Canary, value: "yes" }]
          backendRefs: [{ name: catalog, port: 8080 }]
        - matches:
            - path: { type: PathPrefix, value: / }
          backendRefs: [{ name: frontend, port: 8080 }]
    EOF
    kubectl -n shopfleet get gateway,httproute
    """,
))

TASKS.append(Task(
    id="cka-15", track="cka", domain=D3, points=7, minutes=8, needs={"runtime"},
    title="CoreDNS: add a custom DNS name",
    text="""
    The finance team calls the payment service as `payments.internal`. Configure the cluster DNS (CoreDNS)
    so that `payments.internal` resolves to the same address as `payment.shopfleet.svc.cluster.local`,
    using the CoreDNS `rewrite` plugin in the coredns ConfigMap (namespace kube-system).
    Make sure CoreDNS picks up the change.
    """,
    setup="kubectl -n kube-system get cm coredns -o yaml > /tmp/coredns-backup.yaml",
    cleanup="test -f /tmp/coredns-backup.yaml && kubectl replace -f /tmp/coredns-backup.yaml >/dev/null 2>&1; kubectl -n kube-system rollout restart deploy coredns >/dev/null 2>&1; true",
    checks=[
        Check("Corefile has the rewrite", lambda c: "payments.internal" in jget(c.get("cm", "coredns", "kube-system"), "data.Corefile", "")
              and "payment.shopfleet.svc.cluster.local" in jget(c.get("cm", "coredns", "kube-system"), "data.Corefile", "")),
        Check("payments.internal resolves from a pod", lambda c: c.kubectl("-n", "shopfleet", "exec", "deploy/catalog", "--", "python", "-c",
                                                                           "import socket;print(socket.gethostbyname('payments.internal'))").returncode == 0, runtime=True),
    ],
    solution="""
    kubectl -n kube-system edit configmap coredns
    # inside the ".:53 {" block, above "kubernetes cluster.local ...", add:
    #     rewrite name payments.internal payment.shopfleet.svc.cluster.local
    kubectl -n kube-system get cm coredns -o jsonpath='{.data.Corefile}' | \\
      sed 's#^\\( *\\)kubernetes cluster.local#\\1rewrite name payments.internal payment.shopfleet.svc.cluster.local\\n\\1kubernetes cluster.local#' > /tmp/Corefile
    kubectl -n kube-system create configmap coredns --from-file=Corefile=/tmp/Corefile --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n kube-system rollout restart deployment coredns     # (the reload plugin would also catch it within ~30s)
    kubectl -n shopfleet exec deploy/catalog -- python -c "import socket;print(socket.gethostbyname('payments.internal'))"
    """,
))

TASKS.append(Task(
    id="cka-16", track="cka", domain=D3, points=7, minutes=8,
    title="Default-deny with DNS egress allowed",
    text="""
    Namespace `cka-16` has pods `web` (app=web), `api` (app=api) and `db` (app=db).

    1. NetworkPolicy `default-deny`: deny ALL ingress and egress for every pod in the namespace.
    2. NetworkPolicy `allow-dns`: allow egress from all pods to any destination on UDP and TCP port 53.
    3. NetworkPolicy `api-from-web`: allow ingress to app=api on TCP 8080 only from app=web.
    """,
    setup=ns("cka-16") + """
    for a in web api db; do kubectl -n cka-16 run $a --image=nginx:1.27-alpine --labels=app=$a >/dev/null 2>&1 || true; done
    """,
    cleanup=rmns("cka-16"),
    checks=[
        Check("default-deny: all pods, Ingress+Egress, no rules", lambda c: (lambda p: p and jget(p, "spec.podSelector") in ({}, None) and set(jget(p, "spec.policyTypes", [])) == {"Ingress", "Egress"}
                                                                             and not jget(p, "spec.ingress") and not jget(p, "spec.egress"))(c.get("netpol", "default-deny", "cka-16"))),
        Check("allow-dns: egress UDP+TCP 53", lambda c: {(p.get("protocol", "TCP"), p.get("port")) for r in jget(c.get("netpol", "allow-dns", "cka-16"), "spec.egress", []) for p in r.get("ports", [])} >= {("UDP", 53), ("TCP", 53)}),
        Check("api-from-web: app=api <- app=web on 8080", lambda c: jget(c.get("netpol", "api-from-web", "cka-16"), "spec.podSelector.matchLabels") == {"app": "api"}
              and json_dump(jget(c.get("netpol", "api-from-web", "cka-16"), "spec.ingress")).count('"app": "web"') == 1
              and '"port": 8080' in json_dump(jget(c.get("netpol", "api-from-web", "cka-16"), "spec.ingress"))),
    ],
    solution="""
    kubectl apply -n cka-16 -f - <<'EOF'
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata: { name: default-deny }
    spec:
      podSelector: {}
      policyTypes: [Ingress, Egress]
    ---
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata: { name: allow-dns }
    spec:
      podSelector: {}
      policyTypes: [Egress]
      egress:
        - ports: [{ protocol: UDP, port: 53 }, { protocol: TCP, port: 53 }]
    ---
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata: { name: api-from-web }
    spec:
      podSelector: { matchLabels: { app: api } }
      policyTypes: [Ingress]
      ingress:
        - from: [{ podSelector: { matchLabels: { app: web } } }]
          ports: [{ protocol: TCP, port: 8080 }]
    EOF
    """,
))

TASKS.append(Task(
    id="cka-17", track="cka", domain=D3, points=5, minutes=6,
    title="A Service for something outside the cluster",
    text="""
    The legacy ERP runs on a VM at 10.240.0.200 port 9000 (outside Kubernetes). Pods in namespace `cka-17`
    should reach it as `http://legacy-erp:9000`.

    Create Service `legacy-erp` (port 9000, NO selector) and an EndpointSlice `legacy-erp-1` that points it
    to 10.240.0.200:9000.
    """,
    setup=ns("cka-17"), cleanup=rmns("cka-17"),
    checks=[
        Check("service without selector, port 9000", lambda c: not jget(c.get("svc", "legacy-erp", "cka-17"), "spec.selector") and jget(c.get("svc", "legacy-erp", "cka-17"), "spec.ports.0.port") == 9000),
        Check("endpointslice linked to the service", lambda c: jget(c.get("endpointslice", "legacy-erp-1", "cka-17"), "metadata.labels", {}).get("kubernetes.io/service-name") == "legacy-erp"),
        Check("endpoint 10.240.0.200:9000", lambda c: "10.240.0.200" in json_dump(jget(c.get("endpointslice", "legacy-erp-1", "cka-17"), "endpoints"))
              and jget(c.get("endpointslice", "legacy-erp-1", "cka-17"), "ports.0.port") == 9000),
    ],
    solution="""
    kubectl apply -n cka-17 -f - <<'EOF'
    apiVersion: v1
    kind: Service
    metadata: { name: legacy-erp }
    spec:
      ports: [{ name: http, port: 9000, targetPort: 9000 }]
    ---
    apiVersion: discovery.k8s.io/v1
    kind: EndpointSlice
    metadata:
      name: legacy-erp-1
      labels: { kubernetes.io/service-name: legacy-erp }
    addressType: IPv4
    ports: [{ name: http, port: 9000, protocol: TCP }]
    endpoints:
      - addresses: ["10.240.0.200"]
    EOF
    """,
))

# ============================================================================ Storage
TASKS.append(Task(
    id="cka-18", track="cka", domain=D4, points=7, minutes=7,
    title="Static PersistentVolume and claim",
    text="""
    1. Create PersistentVolume `archive-pv`: 1Gi, ReadWriteOnce, reclaim policy Retain, storageClassName `manual`,
       hostPath /mnt/archive.
    2. In namespace `cka-18` create PersistentVolumeClaim `archive-pvc` (500Mi, RWO, storageClassName manual)
       that binds to it.
    3. Create Pod `archiver` (busybox:1.36, `sleep 1d`) mounting the claim at /archive.
    """,
    setup=ns("cka-18") + "kubectl delete pv archive-pv --ignore-not-found >/dev/null",
    cleanup=rmns("cka-18") + "sleep 2; kubectl delete pv archive-pv --ignore-not-found --wait=false >/dev/null",
    checks=[
        Check("PV spec", lambda c: (lambda pv: pv and jget(pv, "spec.capacity.storage") == "1Gi" and jget(pv, "spec.persistentVolumeReclaimPolicy") == "Retain"
                                    and jget(pv, "spec.storageClassName") == "manual" and jget(pv, "spec.hostPath.path") == "/mnt/archive")(c.get("pv", "archive-pv"))),
        Check("PVC bound to archive-pv", lambda c: jget(c.get("pvc", "archive-pvc", "cka-18"), "spec.volumeName") == "archive-pv"),
        Check("pod mounts it at /archive", lambda c: any(m["mountPath"] == "/archive" for m in jget(c.get("pod", "archiver", "cka-18"), "spec.containers.0.volumeMounts", []))),
    ],
    solution="""
    kubectl apply -f - <<'EOF'
    apiVersion: v1
    kind: PersistentVolume
    metadata: { name: archive-pv }
    spec:
      capacity: { storage: 1Gi }
      accessModes: [ReadWriteOnce]
      persistentVolumeReclaimPolicy: Retain
      storageClassName: manual
      hostPath: { path: /mnt/archive }
    ---
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata: { name: archive-pvc, namespace: cka-18 }
    spec:
      accessModes: [ReadWriteOnce]
      storageClassName: manual
      resources: { requests: { storage: 500Mi } }
    ---
    apiVersion: v1
    kind: Pod
    metadata: { name: archiver, namespace: cka-18 }
    spec:
      containers:
        - name: c
          image: busybox:1.36
          command: ["sleep", "1d"]
          volumeMounts: [{ name: a, mountPath: /archive }]
      volumes: [{ name: a, persistentVolumeClaim: { claimName: archive-pvc } }]
    EOF
    sleep 3; kubectl get pv archive-pv; kubectl -n cka-18 get pvc
    """,
))

TASKS.append(Task(
    id="cka-19", track="cka", domain=D4, points=6, minutes=6,
    title="StorageClass with WaitForFirstConsumer",
    text="""
    Create StorageClass `fast-local` that uses the SAME provisioner as the cluster's current default StorageClass, with:
    - volumeBindingMode WaitForFirstConsumer
    - reclaimPolicy Delete
    - allowVolumeExpansion true
    Do NOT make it the default. Then in namespace `cka-19` create PVC `scratch` (1Gi, RWO) using it, and explain in
    `{answers}/cka-19.txt` (one word) the PVC's phase right after creation.
    """,
    setup=ns("cka-19") + "kubectl delete storageclass fast-local --ignore-not-found >/dev/null",
    cleanup=rmns("cka-19") + "kubectl delete storageclass fast-local --ignore-not-found >/dev/null",
    checks=[
        Check("same provisioner as default", lambda c: jget(c.get("storageclass", "fast-local"), "provisioner") == next((s["provisioner"] for s in c.items("storageclass") if jget(s, "metadata.annotations", {}).get("storageclass.kubernetes.io/is-default-class") == "true"), "kubernetes.io/no-provisioner")),
        Check("WaitForFirstConsumer, Delete, expansion", lambda c: (jget(c.get("storageclass", "fast-local"), "volumeBindingMode"), jget(c.get("storageclass", "fast-local"), "reclaimPolicy"), jget(c.get("storageclass", "fast-local"), "allowVolumeExpansion")) == ("WaitForFirstConsumer", "Delete", True)),
        Check("not default", lambda c: c.get("storageclass", "fast-local") is not None and jget(c.get("storageclass", "fast-local"), "metadata.annotations", {}).get("storageclass.kubernetes.io/is-default-class") != "true"),
        Check("PVC scratch uses fast-local", lambda c: jget(c.get("pvc", "scratch", "cka-19"), "spec.storageClassName") == "fast-local"),
        Check("answer: Pending", lambda c: (c.answer("cka-19.txt") or "").lower() == "pending"),
    ],
    solution="""
    PROV=$(kubectl get sc -o jsonpath='{.items[?(@.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class=="true")].provisioner}')
    kubectl apply -f - <<EOF
    apiVersion: storage.k8s.io/v1
    kind: StorageClass
    metadata: { name: fast-local }
    provisioner: ${PROV:-kubernetes.io/no-provisioner}
    volumeBindingMode: WaitForFirstConsumer
    reclaimPolicy: Delete
    allowVolumeExpansion: true
    EOF
    kubectl apply -n cka-19 -f - <<'EOF'
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata: { name: scratch }
    spec:
      accessModes: [ReadWriteOnce]
      storageClassName: fast-local
      resources: { requests: { storage: 1Gi } }
    EOF
    echo Pending > $ANSWERS/cka-19.txt     # no pod uses it yet -> binding waits for the first consumer
    """,
))

TASKS.append(Task(
    id="cka-20", track="cka", domain=D4, points=4, minutes=4, needs={"runtime"},
    title="Protect the database volume",
    text="""
    ShopFleet's Postgres data lives on the PersistentVolume bound to PVC `data-postgres-0` (namespace `shopfleet`).
    Change that PV's reclaim policy to Retain so the data survives even if the PVC is deleted.
    """,
    setup="",
    cleanup="",
    checks=[
        Check("PV of data-postgres-0 has reclaim policy Retain", lambda c: (lambda pvn: bool(pvn) and jget(c.get("pv", pvn), "spec.persistentVolumeReclaimPolicy") == "Retain"
                                                                            or (False, "PVC not bound yet?"))(jget(c.get("pvc", "data-postgres-0", "shopfleet"), "spec.volumeName")), runtime=True),
    ],
    solution="""
    PV=$(kubectl -n shopfleet get pvc data-postgres-0 -o jsonpath='{.spec.volumeName}')
    kubectl patch pv $PV -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
    kubectl get pv $PV
    """,
))

# ============================================================================ Troubleshooting (30%)
TASKS.append(Task(
    id="cka-21", track="cka", domain=D5, points=9, minutes=10, needs={"node"}, destructive=True,
    title="A worker node is NotReady",
    text="""
    Node `{w1}` has gone NotReady. Find out why and fix it so it becomes Ready again, and make sure the fix
    survives a reboot. (Start with `kubectl describe node`, then ssh in and look at the kubelet:
    `systemctl status kubelet`, `journalctl -u kubelet -n 50`.)
    """,
    setup="""
    node_sh $W1 'systemctl stop kubelet; systemctl disable kubelet >/dev/null 2>&1; cp /var/lib/kubelet/config.yaml /var/lib/kubelet/config.yaml.exam-bak'
    """,
    cleanup="node_sh $W1 'test -f /var/lib/kubelet/config.yaml.exam-bak && cp /var/lib/kubelet/config.yaml.exam-bak /var/lib/kubelet/config.yaml; systemctl enable --now kubelet' >/dev/null 2>&1 || true",
    checks=[
        Check("node Ready", lambda c: node_ready(c, c.v["w1"])),
        Check("kubelet enabled at boot", lambda c: c.node(c.v["w1"], "systemctl is-enabled kubelet")[1] == "enabled"),
        Check("kubelet active", lambda c: c.node(c.v["w1"], "systemctl is-active kubelet")[1] == "active"),
    ],
    solution="""
    kubectl describe node $W1 | grep -A5 Conditions       # "Kubelet stopped posting node status"
    ssh $W1
    sudo systemctl status kubelet                          # inactive (dead), disabled
    sudo systemctl enable --now kubelet
    sudo journalctl -u kubelet -n 20 --no-pager
    exit
    kubectl get nodes -w
    """,
))

TASKS.append(Task(
    id="cka-22", track="cka", domain=D5, points=9, minutes=10, needs={"node"}, destructive=True,
    title="New pods stay Pending forever",
    text="""
    Since a "small config change" on the control plane, every NEW pod in the cluster stays Pending with no events
    from the scheduler. Existing pods keep running. Find and fix the problem.
    (Hint: which component assigns pods to nodes, and how is it run on a kubeadm control plane?)
    """,
    setup="""
    node_sh $CP 'cp /etc/kubernetes/manifests/kube-scheduler.yaml /root/kube-scheduler.yaml.exam-bak && sed -i "s#--kubeconfig=/etc/kubernetes/scheduler.conf#--kubeconfig=/etc/kubernetes/scheduler.config#" /etc/kubernetes/manifests/kube-scheduler.yaml'
    sleep 20
    """,
    cleanup="node_sh $CP 'test -f /root/kube-scheduler.yaml.exam-bak && cp /root/kube-scheduler.yaml.exam-bak /etc/kubernetes/manifests/kube-scheduler.yaml' >/dev/null 2>&1 || true",
    checks=[
        Check("scheduler manifest uses scheduler.conf", lambda c: "--kubeconfig=/etc/kubernetes/scheduler.conf" in c.node(c.v["cp"], "cat /etc/kubernetes/manifests/kube-scheduler.yaml")[1]),
        Check("kube-scheduler pod Running", lambda c: any(jget(p, "status.phase") == "Running" and all(s.get("ready") for s in jget(p, "status.containerStatuses", []))
                                                          for p in c.items("pods", "kube-system", "component=kube-scheduler"))),
        Check("a new pod gets scheduled", lambda c: c.sh("kubectl -n default delete pod sched-probe --ignore-not-found >/dev/null; kubectl -n default run sched-probe --image=busybox:1.36 --restart=Never -- true >/dev/null; sleep 6; "
                                                        "kubectl -n default get pod sched-probe -o jsonpath='{.spec.nodeName}'; kubectl -n default delete pod sched-probe --wait=false >/dev/null").stdout.strip() != ""),
    ],
    solution="""
    kubectl -n default run test --image=busybox:1.36 -- sleep 1; kubectl get pod test    # Pending, no events
    kubectl -n kube-system get pods -l component=kube-scheduler                          # CrashLoopBackOff / missing
    kubectl -n kube-system logs kube-scheduler-$CP                                       # stat /etc/kubernetes/scheduler.config: no such file
    ssh $CP 'sudo vi /etc/kubernetes/manifests/kube-scheduler.yaml'                       # fix --kubeconfig=/etc/kubernetes/scheduler.conf
    # the kubelet restarts the static pod automatically; give it ~30s
    kubectl get pod test
    """,
))

TASKS.append(Task(
    id="cka-23", track="cka", domain=D5, points=8, minutes=8,
    title="The storefront can't reach its database",
    text="""
    Since a change this morning, ShopFleet's catalog, orders and auth pods in namespace `shopfleet` are not Ready,
    and the store shows errors. Postgres itself is running. Find the cause and fix it (don't restart Postgres).
    Write the cause in one line to `{answers}/cka-23.txt`.
    """,
    setup="""
    kubectl -n shopfleet get svc postgres -o yaml > /tmp/pg-svc-backup.yaml
    kubectl -n shopfleet patch svc postgres --type=json -p '[{"op":"replace","path":"/spec/selector","value":{"app":"postgresql"}}]' >/dev/null
    """,
    cleanup="kubectl -n shopfleet patch svc postgres --type=json -p '[{\"op\":\"replace\",\"path\":\"/spec/selector\",\"value\":{\"app\":\"postgres\"}}]' >/dev/null 2>&1; rm -f \"$ANSWERS/cka-23.txt\"",
    checks=[
        Check("postgres Service selects the postgres pod", lambda c: jget(c.get("svc", "postgres", "shopfleet"), "spec.selector") == {"app": "postgres"}),
        Check("postgres has an endpoint", lambda c: len(endpoints(c, "postgres", "shopfleet")) >= 1, runtime=True),
        Check("catalog Ready again", lambda c: c.deploy_ready("catalog", "shopfleet"), runtime=True),
        Check("cause written (selector)", lambda c: "selector" in (c.answer("cka-23.txt") or "").lower() or "endpoint" in (c.answer("cka-23.txt") or "").lower()),
    ],
    solution="""
    kubectl -n shopfleet get pods                                  # catalog/orders/auth 0/1
    kubectl -n shopfleet logs deploy/catalog | tail -3             # readiness: db unavailable (timeout / name resolution)
    kubectl -n shopfleet get endpointslices -l kubernetes.io/service-name=postgres   # no endpoints!
    kubectl -n shopfleet get svc postgres -o yaml | grep -A2 selector               # app: postgresql (typo)
    kubectl -n shopfleet get pod postgres-0 --show-labels                           # app=postgres
    kubectl -n shopfleet patch svc postgres -p '{"spec":{"selector":{"app":"postgres"}}}'
    echo "postgres Service selector app=postgresql matched no pods -> no endpoints" > $ANSWERS/cka-23.txt
    """,
))

TASKS.append(Task(
    id="cka-24", track="cka", domain=D5, points=7, minutes=7,
    title="OOMKilled worker",
    text="""
    The fulfilment `worker` Deployment (namespace `shopfleet`) keeps restarting. Find out why (check Last State
    and its reason), fix it by setting the container's memory limit to 256Mi (request 128Mi), and write the
    termination reason you found to `{answers}/cka-24.txt`.
    """,
    setup="""
    kubectl -n shopfleet set resources deployment worker -c worker --limits=memory=24Mi --requests=memory=16Mi >/dev/null
    """,
    cleanup="kubectl -n shopfleet set resources deployment worker -c worker --limits=memory=256Mi --requests=memory=128Mi >/dev/null 2>&1; rm -f \"$ANSWERS/cka-24.txt\"",
    checks=[
        Check("limit 256Mi / request 128Mi", lambda c: jget(c.get("deploy", "worker", "shopfleet"), "spec.template.spec.containers.0.resources.limits.memory") == "256Mi"
              and jget(c.get("deploy", "worker", "shopfleet"), "spec.template.spec.containers.0.resources.requests.memory") == "128Mi"),
        Check("reason written: OOMKilled", lambda c: (c.answer("cka-24.txt") or "").strip() == "OOMKilled"),
        Check("worker running without restarts", lambda c: any(jget(p, "status.containerStatuses.0.restartCount", 0) == 0 and jget(p, "status.phase") == "Running" for p in c.items("pods", "shopfleet", "app=worker")), runtime=True),
    ],
    solution="""
    kubectl -n shopfleet get pods -l app=worker                                         # RESTARTS climbing
    kubectl -n shopfleet describe pod -l app=worker | grep -A4 "Last State" || true     # Reason: OOMKilled, Exit Code 137
    echo OOMKilled > $ANSWERS/cka-24.txt
    kubectl -n shopfleet set resources deployment worker -c worker --limits=memory=256Mi --requests=memory=128Mi
    """,
))

TASKS.append(Task(
    id="cka-25", track="cka", domain=D5, points=7, minutes=7,
    title="Scale-up stuck below the desired count",
    text="""
    Marketing scaled `cart` (namespace `shopfleet`) to 6 replicas for a sale, but only some pods exist.
    Find what blocks the remaining pods and fix the cause (not by reducing replicas).
    Write the name of the blocking object (kind/name) to `{answers}/cka-25.txt`.
    """,
    setup="""
    kubectl -n shopfleet get resourcequota shopfleet-quota -o yaml > /tmp/quota-backup.yaml
    kubectl -n shopfleet scale deploy cart --replicas=2 >/dev/null
    sleep 3
    USED=$(kubectl -n shopfleet get pods --no-headers --field-selector=status.phase!=Succeeded,status.phase!=Failed | wc -l)
    kubectl -n shopfleet patch resourcequota shopfleet-quota --type=merge -p "{\\"spec\\":{\\"hard\\":{\\"pods\\":\\"$((USED + 1))\\"}}}" >/dev/null
    kubectl -n shopfleet annotate resourcequota shopfleet-quota exam/setup-pods=$((USED + 1)) --overwrite >/dev/null
    kubectl -n shopfleet delete hpa cart --ignore-not-found >/dev/null
    kubectl -n shopfleet scale deploy cart --replicas=6 >/dev/null
    """,
    cleanup="""kubectl -n shopfleet patch resourcequota shopfleet-quota --type=merge -p '{"spec":{"hard":{"pods":"80"}}}' >/dev/null 2>&1; kubectl -n shopfleet scale deploy cart --replicas=2 >/dev/null 2>&1; rm -f "$ANSWERS/cka-25.txt"; true""",
    checks=[
        Check("cart has 6 pods", lambda c: len(c.items("pods", "shopfleet", "app=cart")) >= 6),
        Check("quota raised enough for 4 more pods", lambda c: int(jget(c.get("quota", "shopfleet-quota", "shopfleet"), "spec.hard.pods", "0"))
              >= int(jget(c.get("quota", "shopfleet-quota", "shopfleet"), "metadata.annotations", {}).get("exam/setup-pods", "999")) + 4),
        Check("blocking object named", lambda c: "shopfleet-quota" in (c.answer("cka-25.txt") or "") and "quota" in (c.answer("cka-25.txt") or "").lower()),
    ],
    solution="""
    kubectl -n shopfleet get deploy cart                          # 3/6
    kubectl -n shopfleet describe rs -l app=cart | grep -i quota || true   # exceeded quota: shopfleet-quota, requested: pods=1 ...
    kubectl -n shopfleet describe resourcequota shopfleet-quota
    kubectl -n shopfleet patch resourcequota shopfleet-quota -p '{"spec":{"hard":{"pods":"80"}}}'
    echo "resourcequota/shopfleet-quota" > $ANSWERS/cka-25.txt
    """,
))

TASKS.append(Task(
    id="cka-26", track="cka", domain=D5, points=6, minutes=7, needs={"kubeadm"},
    title="Control-plane certificates",
    text="""
    On the control plane `{cp}`:
    1. Write the expiry date of the API server's serving certificate, exactly as shown in the EXPIRES column of
       `kubeadm certs check-expiration`, to `{answers}/cka-26.txt`.
    2. Renew ONLY the apiserver certificate with kubeadm, then make the API server use it.
    """,
    setup='rm -f "$ANSWERS/cka-26.txt"; node_sh $CP "date +%s > /root/.cka26-start"',
    checks=[
        Check("expiry written", lambda c: len(c.answer("cka-26.txt") or "") > 8),
        Check("apiserver.crt renewed after the task started", lambda c: c.node(c.v["cp"], "test /etc/kubernetes/pki/apiserver.crt -nt /root/.cka26-start")[0] == 0),
        Check("API server healthy", lambda c: c.kubectl("get", "--raw", "/readyz").stdout.strip() == "ok"),
    ],
    solution="""
    ssh $CP
    sudo kubeadm certs check-expiration | grep '^apiserver '      # copy the EXPIRES value
    exit; echo "Oct 09, 2027 10:00 UTC" > $ANSWERS/cka-26.txt     # (your value)
    ssh $CP 'sudo kubeadm certs renew apiserver'
    # restart the static pod so it loads the new cert: move the manifest out and back
    ssh $CP 'sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/ && sleep 20 && sudo mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/'
    kubectl get --raw /readyz
    """,
))

TASKS.append(Task(
    id="cka-27", track="cka", domain=D5, points=6, minutes=6, needs={"node"},
    title="Container runtime debugging with crictl",
    text="""
    On node `{w2}`, use crictl (not kubectl) to find the node-agent container (DaemonSet `node-agent`,
    namespace shopfleet) running there. Write its CONTAINER ID (the short 13-char form crictl prints) to
    `{answers}/cka-27.txt`, and its image to `{answers}/cka-27-image.txt`.
    """,
    setup='rm -f "$ANSWERS/cka-27.txt" "$ANSWERS/cka-27-image.txt"',
    cleanup='rm -f "$ANSWERS/cka-27.txt" "$ANSWERS/cka-27-image.txt"',
    checks=[
        Check("container id matches", lambda c: (c.answer("cka-27.txt") or "x")[:12] in c.node(c.v["w2"], "crictl ps --name agent -q")[1]),
        Check("image matches", lambda c: (c.answer("cka-27-image.txt") or "x").split("@")[0] in c.node(c.v["w2"], "crictl ps --name agent -o json")[1]),
    ],
    solution="""
    ssh $W2
    sudo crictl ps --name agent                      # CONTAINER  IMAGE  CREATED  STATE  NAME  ... POD
    sudo crictl inspect <id> | grep -m1 '"image"'
    exit
    ssh $W2 "sudo crictl ps --name agent -q | cut -c1-13" > $ANSWERS/cka-27.txt
    kubectl -n shopfleet get ds node-agent -o jsonpath='{.spec.template.spec.containers[0].image}' > $ANSWERS/cka-27-image.txt
    """,
))

TASKS.append(Task(
    id="cka-28", track="cka", domain=D5, points=6, minutes=6,
    title="Drain a node for maintenance",
    text="""
    Node `{w2}` needs a kernel patch. Make it unschedulable and evict its workloads safely (respecting
    PodDisruptionBudgets; DaemonSet pods may stay). Leave it cordoned -- the trainer's reset will uncordon it.
    """,
    setup="kubectl uncordon $W2 >/dev/null 2>&1 || true",
    cleanup="kubectl uncordon $W2 >/dev/null 2>&1 || true",
    checks=[
        Check("node unschedulable", lambda c: jget(node_obj(c, c.v["w2"]), "spec.unschedulable") is True),
        Check("no non-DaemonSet pods left in shopfleet on it", lambda c: jget(node_obj(c, c.v["w2"]), "spec.unschedulable") is True and not [p for p in c.items("pods", "shopfleet") if jget(p, "spec.nodeName") == c.v["w2"]
                                                                              and jget(p, "metadata.ownerReferences.0.kind") not in ("DaemonSet", "Node") and jget(p, "status.phase") in ("Running", "Pending")]),
    ],
    solution="""
    kubectl drain $W2 --ignore-daemonsets --delete-emptydir-data
    kubectl get pods -A -o wide --field-selector spec.nodeName=$W2
    """,
))

TASKS.append(Task(
    id="cka-29", track="cka", domain=D5, points=5, minutes=5, needs={"metrics"},
    title="Find the busiest node and pod",
    text="""
    Using the metrics API, write to `{answers}/cka-29.txt`:
    line 1: the node with the highest CPU usage right now
    line 2: the pod (any namespace) with the highest CPU usage, as namespace/name
    """,
    setup='rm -f "$ANSWERS/cka-29.txt"', cleanup='rm -f "$ANSWERS/cka-29.txt"',
    checks=[
        Check("node among top-2 by CPU", lambda c: line(c, "cka-29.txt", 0) in c.sh("kubectl top nodes --no-headers --sort-by=cpu | head -2 | awk '{print $1}'").stdout.split()),
        Check("pod among top-3 by CPU", lambda c: line(c, "cka-29.txt", 1) in c.sh("kubectl top pods -A --no-headers --sort-by=cpu | head -3 | awk '{print $1\"/\"$2}'").stdout.split()),
    ],
    solution="""
    kubectl top nodes --sort-by=cpu --no-headers | head -1 | awk '{print $1}' > $ANSWERS/cka-29.txt
    kubectl top pods -A --sort-by=cpu --no-headers | head -1 | awk '{print $1"/"$2}' >> $ANSWERS/cka-29.txt
    """,
))


def minor(version):
    try:
        return int(str(version).lstrip("v").split(".")[1])
    except (IndexError, ValueError):
        return -1
