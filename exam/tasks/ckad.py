"""CKAD tasks. Each mirrors the style of a real exam question, set in the ShopFleet world.

Conventions:
  - Every task works in its own namespace (ckad-NN) unless it says otherwise.
  - Answers that are "write X to a file" go to {answers}/<task-id>.txt
  - `solution` is a runnable reference; the checks only look at the RESULT, so any correct approach passes.
"""
from trainer import Check, Task, jget

D1, D2, D3, D4, D5 = ("Application Design and Build", "Application Deployment",
                      "Application Observability and Maintenance",
                      "Application Environment, Configuration and Security", "Services and Networking")


def ns(n):
    return f"    kubectl create namespace {n} --dry-run=client -o yaml | kubectl apply -f - >/dev/null\n"


def rmns(n):
    return f"    kubectl delete namespace {n} --ignore-not-found --wait=false >/dev/null 2>&1 || true\n"


def guard(primary, fn):
    """A 'nothing else changed' check only earns points once the main change exists."""
    return lambda c: bool(primary(c)) and fn(c)


def container(obj, name):
    spec = jget(obj, "spec.template.spec") or jget(obj, "spec") or {}
    for c in spec.get("containers", []) + spec.get("initContainers", []):
        if c["name"] == name:
            return c
    return None


TASKS = []

# ============================================================================ Application Design and Build
TASKS.append(Task(
    id="ckad-01", track="ckad", domain=D1, points=6, minutes=7,
    title="Multi-container pod: log shipper sidecar",
    text="""
    The fulfilment team wants their logs shipped. In namespace `ckad-01` create a Pod named `log-shipper`:

    - container `app`, image `busybox:1.36`, runs:
        sh -c 'while true; do echo "order shipped $(date)" >> /var/log/app/app.log; sleep 2; done'
    - a SIDECAR container named `shipper`, image `busybox:1.36`, runs:  tail -F /var/log/app/app.log
      (use a native sidecar -- an init container with restartPolicy: Always)
    - both mount an emptyDir volume named `logs` at /var/log/app

    `kubectl logs log-shipper -c shipper -n ckad-01` must show the "order shipped" lines.
    """,
    setup=ns("ckad-01"), cleanup=rmns("ckad-01"),
    checks=[
        Check("pod log-shipper exists", lambda c: c.get("pod", "log-shipper", "ckad-01") is not None),
        Check("emptyDir volume 'logs'", lambda c: any(v["name"] == "logs" and "emptyDir" in v for v in jget(c.get("pod", "log-shipper", "ckad-01"), "spec.volumes", []))),
        Check("'shipper' is a native sidecar (initContainer, restartPolicy Always)",
              lambda c: any(i["name"] == "shipper" and i.get("restartPolicy") == "Always" for i in jget(c.get("pod", "log-shipper", "ckad-01"), "spec.initContainers", []))),
        Check("both containers mount /var/log/app",
              lambda c: all(any(m["mountPath"] == "/var/log/app" for m in (container(c.get("pod", "log-shipper", "ckad-01"), n) or {}).get("volumeMounts", [])) for n in ("app", "shipper"))),
        Check("shipper logs show 'order shipped'", lambda c: "order shipped" in c.kubectl("logs", "log-shipper", "-c", "shipper", "-n", "ckad-01", "--tail=5").stdout, runtime=True),
    ],
    solution="""
    kubectl apply -n ckad-01 -f - <<'EOF'
    apiVersion: v1
    kind: Pod
    metadata: { name: log-shipper }
    spec:
      initContainers:
        - name: shipper
          image: busybox:1.36
          restartPolicy: Always
          command: ["tail", "-F", "/var/log/app/app.log"]
          volumeMounts: [{ name: logs, mountPath: /var/log/app }]
      containers:
        - name: app
          image: busybox:1.36
          command: ["sh", "-c", "while true; do echo \\"order shipped $(date)\\" >> /var/log/app/app.log; sleep 2; done"]
          volumeMounts: [{ name: logs, mountPath: /var/log/app }]
      volumes:
        - name: logs
          emptyDir: {}
    EOF
    """,
))

HAS_INIT02 = lambda c: container(c.get("deploy", "checkout-probe", "ckad-02"), "wait-for-payment") is not None
TASKS.append(Task(
    id="ckad-02", track="ckad", domain=D1, points=5, minutes=6,
    title="Init container: wait for a dependency",
    text="""
    Deployment `checkout-probe` in namespace `ckad-02` must not start its main container until the ShopFleet
    payment Service resolves in DNS.

    Add an init container named `wait-for-payment`, image `busybox:1.36`, that loops until
        nslookup payment.shopfleet.svc.cluster.local
    succeeds (sleep 2 between attempts). Do not change the existing container. The Deployment must become Available.
    """,
    setup=ns("ckad-02") + """
    kubectl -n ckad-02 create deployment checkout-probe --image=nginx:1.27-alpine --replicas=1 --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    """,
    cleanup=rmns("ckad-02"),
    checks=[
        Check("init container wait-for-payment exists", lambda c: container(c.get("deploy", "checkout-probe", "ckad-02"), "wait-for-payment") is not None),
        Check("it uses busybox:1.36", lambda c: (container(c.get("deploy", "checkout-probe", "ckad-02"), "wait-for-payment") or {}).get("image") == "busybox:1.36"),
        Check("it looks up payment.shopfleet.svc.cluster.local",
              lambda c: "payment.shopfleet" in " ".join((container(c.get("deploy", "checkout-probe", "ckad-02"), "wait-for-payment") or {}).get("command", []) + (container(c.get("deploy", "checkout-probe", "ckad-02"), "wait-for-payment") or {}).get("args", []))),
        Check("main container unchanged (nginx:1.27-alpine)", guard(HAS_INIT02, lambda c: jget(c.get("deploy", "checkout-probe", "ckad-02"), "spec.template.spec.containers.0.image") == "nginx:1.27-alpine")),
        Check("deployment Available", guard(HAS_INIT02, lambda c: c.deploy_ready("checkout-probe", "ckad-02"))),
    ],
    solution="""
    kubectl -n ckad-02 patch deployment checkout-probe --type=json -p '[{"op":"add","path":"/spec/template/spec/initContainers","value":[
      {"name":"wait-for-payment","image":"busybox:1.36",
       "command":["sh","-c","until nslookup payment.shopfleet.svc.cluster.local; do echo waiting; sleep 2; done"]}]}]'
    kubectl -n ckad-02 rollout status deploy/checkout-probe --timeout=120s || true
    # (or: kubectl -n ckad-02 edit deploy checkout-probe  and add an initContainers: section)
    """,
))

TASKS.append(Task(
    id="ckad-03", track="ckad", domain=D1, points=6, minutes=7,
    title="CronJob for a stock audit + a manual run",
    text="""
    In namespace `ckad-03` create a CronJob named `stock-audit`:

    - runs every 15 minutes
    - image `busybox:1.36`, command:  wget -qO- http://catalog.shopfleet.svc.cluster.local:8080/api/catalog/categories
    - keep 2 successful and 1 failed job in history
    - never run two at the same time
    - each Job must be killed if it runs longer than 60 seconds; pods must never restart (restartPolicy Never)

    Then trigger it once by hand by creating a Job named `stock-audit-manual` from the CronJob.
    """,
    setup=ns("ckad-03"), cleanup=rmns("ckad-03"),
    checks=[
        Check("schedule every 15 minutes", lambda c: jget(c.get("cronjob", "stock-audit", "ckad-03"), "spec.schedule") in ("*/15 * * * *", "0,15,30,45 * * * *")),
        Check("history limits 2 / 1", lambda c: (jget(c.get("cronjob", "stock-audit", "ckad-03"), "spec.successfulJobsHistoryLimit"), jget(c.get("cronjob", "stock-audit", "ckad-03"), "spec.failedJobsHistoryLimit")) == (2, 1)),
        Check("concurrencyPolicy Forbid", lambda c: jget(c.get("cronjob", "stock-audit", "ckad-03"), "spec.concurrencyPolicy") == "Forbid"),
        Check("activeDeadlineSeconds 60", lambda c: jget(c.get("cronjob", "stock-audit", "ckad-03"), "spec.jobTemplate.spec.activeDeadlineSeconds") == 60),
        Check("restartPolicy Never", lambda c: jget(c.get("cronjob", "stock-audit", "ckad-03"), "spec.jobTemplate.spec.template.spec.restartPolicy") == "Never"),
        Check("command targets the catalog API", lambda c: "catalog.shopfleet" in json_dump(jget(c.get("cronjob", "stock-audit", "ckad-03"), "spec.jobTemplate.spec.template.spec.containers"))),
        Check("job stock-audit-manual created FROM the cronjob",
              lambda c: jget(c.get("job", "stock-audit-manual", "ckad-03"), "metadata.annotations", {}).get("cronjob.kubernetes.io/instantiate") == "manual"),
    ],
    solution="""
    kubectl -n ckad-03 create cronjob stock-audit --image=busybox:1.36 --schedule="*/15 * * * *" \\
      --dry-run=client -o yaml -- wget -qO- http://catalog.shopfleet.svc.cluster.local:8080/api/catalog/categories > /tmp/cj.yaml
    # edit /tmp/cj.yaml: add successfulJobsHistoryLimit/failedJobsHistoryLimit/concurrencyPolicy under spec,
    # activeDeadlineSeconds under spec.jobTemplate.spec. Here with a patch instead:
    kubectl apply -f /tmp/cj.yaml
    kubectl -n ckad-03 patch cronjob stock-audit --type=merge -p '{"spec":{"successfulJobsHistoryLimit":2,"failedJobsHistoryLimit":1,
      "concurrencyPolicy":"Forbid","jobTemplate":{"spec":{"activeDeadlineSeconds":60,"template":{"spec":{"restartPolicy":"Never"}}}}}}'
    kubectl -n ckad-03 create job stock-audit-manual --from=cronjob/stock-audit
    """,
))

TASKS.append(Task(
    id="ckad-04", track="ckad", domain=D1, points=4, minutes=4,
    title="Parallel Job",
    text="""
    In namespace `ckad-04` create a Job named `price-check` that:
    - must complete successfully 6 times, running at most 2 pods in parallel
    - gives up after 3 failed retries
    - image `busybox:1.36`, command:  sh -c 'echo checking prices; sleep 3'
    """,
    setup=ns("ckad-04"), cleanup=rmns("ckad-04"),
    checks=[
        Check("completions 6", lambda c: jget(c.get("job", "price-check", "ckad-04"), "spec.completions") == 6),
        Check("parallelism 2", lambda c: jget(c.get("job", "price-check", "ckad-04"), "spec.parallelism") == 2),
        Check("backoffLimit 3", lambda c: jget(c.get("job", "price-check", "ckad-04"), "spec.backoffLimit") == 3),
        Check("image busybox:1.36", lambda c: jget(c.get("job", "price-check", "ckad-04"), "spec.template.spec.containers.0.image") == "busybox:1.36"),
        Check("job eventually succeeds 6 times", lambda c: (jget(c.get("job", "price-check", "ckad-04"), "status.succeeded", 0) or 0) >= 6
              or (False, "may still be running -- check again in a minute"), runtime=True),
    ],
    solution="""
    kubectl -n ckad-04 create job price-check --image=busybox:1.36 --dry-run=client -o yaml -- sh -c 'echo checking prices; sleep 3' > /tmp/job.yaml
    # add under spec:  completions: 6, parallelism: 2, backoffLimit: 3
    python3 - <<'EOF'
    import re
    s = open('/tmp/job.yaml').read().replace('spec:\\n  template:', 'spec:\\n  completions: 6\\n  parallelism: 2\\n  backoffLimit: 3\\n  template:', 1)
    open('/tmp/job.yaml', 'w').write(s)
    EOF
    kubectl apply -f /tmp/job.yaml
    """,
))

TASKS.append(Task(
    id="ckad-05", track="ckad", domain=D1, points=5, minutes=7, needs={"podman"},
    title="Build and export a container image",
    text="""
    Using podman (or docker), build the image defined in `{repo}/services/payment/Dockerfile`
    (build context: that directory) and tag it `shopfleet/payment:exam`.

    Export the image as an OCI/docker archive to `{answers}/payment-exam.tar`.
    """,
    setup="mkdir -p \"$ANSWERS\"; rm -f \"$ANSWERS/payment-exam.tar\"",
    cleanup="rm -f \"$ANSWERS/payment-exam.tar\"; (podman rmi shopfleet/payment:exam || docker rmi shopfleet/payment:exam) >/dev/null 2>&1 || true",
    checks=[
        Check("archive exists", lambda c: c.sh(f'test -s "{c.v["answers"]}/payment-exam.tar"').returncode == 0),
        Check("archive contains tag shopfleet/payment:exam",
              lambda c: "shopfleet/payment:exam" in c.sh(f'tar -xOf "{c.v["answers"]}/payment-exam.tar" manifest.json index.json 2>/dev/null').stdout),
    ],
    solution="""
    cd $REPO/services/payment
    podman build -t shopfleet/payment:exam .          # or: docker build ...
    podman save -o $ANSWERS/payment-exam.tar shopfleet/payment:exam   # or: docker save -o ...
    """,
))

TASKS.append(Task(
    id="ckad-06", track="ckad", domain=D1, points=5, minutes=6,
    title="PersistentVolumeClaim for reports",
    text="""
    In namespace `ckad-06`:
    1. Create a PersistentVolumeClaim `reports-pvc` requesting 100Mi, access mode ReadWriteOnce,
       using the cluster's default StorageClass.
    2. Create a Pod `report-writer`, image `busybox:1.36`, command `sleep 3600`, that mounts the claim at `/reports`.
    """,
    setup=ns("ckad-06"), cleanup=rmns("ckad-06"),
    checks=[
        Check("PVC requests 100Mi RWO", lambda c: jget(c.get("pvc", "reports-pvc", "ckad-06"), "spec.resources.requests.storage") == "100Mi"
              and "ReadWriteOnce" in jget(c.get("pvc", "reports-pvc", "ckad-06"), "spec.accessModes", [])),
        Check("pod mounts the claim at /reports", lambda c: any(
            v.get("persistentVolumeClaim", {}).get("claimName") == "reports-pvc" and any(m["name"] == v["name"] and m["mountPath"] == "/reports"
            for m in jget(c.get("pod", "report-writer", "ckad-06"), "spec.containers.0.volumeMounts", []))
            for v in jget(c.get("pod", "report-writer", "ckad-06"), "spec.volumes", []))),
        Check("PVC is Bound", lambda c: jget(c.get("pvc", "reports-pvc", "ckad-06"), "status.phase") == "Bound", runtime=True),
    ],
    solution="""
    kubectl apply -n ckad-06 -f - <<'EOF'
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata: { name: reports-pvc }
    spec:
      accessModes: [ReadWriteOnce]
      resources: { requests: { storage: 100Mi } }
    ---
    apiVersion: v1
    kind: Pod
    metadata: { name: report-writer }
    spec:
      containers:
        - name: writer
          image: busybox:1.36
          command: ["sleep", "3600"]
          volumeMounts: [{ name: data, mountPath: /reports }]
      volumes:
        - name: data
          persistentVolumeClaim: { claimName: reports-pvc }
    EOF
    """,
))

# ============================================================================ Application Deployment
REV3_07 = lambda c: int(jget(c.get("deploy", "catalog-mirror", "ckad-07"), "metadata.annotations", {}).get("deployment.kubernetes.io/revision", "0")) >= 3
TASKS.append(Task(
    id="ckad-07", track="ckad", domain=D2, points=6, minutes=6,
    title="Rolling update, then roll back",
    text="""
    Deployment `catalog-mirror` (namespace `ckad-07`) runs nginx:1.26-alpine with 4 replicas.

    1. Change its rolling-update strategy to maxSurge 2 and maxUnavailable 0.
    2. Update the image to nginx:1.27-alpine and wait for the rollout to finish.
    3. The new version is "bad": roll the Deployment back to the previous revision.

    Final state: image nginx:1.26-alpine, strategy maxSurge 2 / maxUnavailable 0, at least 3 revisions in history.
    """,
    setup=ns("ckad-07") + """
    kubectl -n ckad-07 create deployment catalog-mirror --image=nginx:1.26-alpine --replicas=4 --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    """,
    cleanup=rmns("ckad-07"),
    checks=[
        Check("strategy maxSurge 2 / maxUnavailable 0", lambda c: (str(jget(c.get("deploy", "catalog-mirror", "ckad-07"), "spec.strategy.rollingUpdate.maxSurge")),
                                                             str(jget(c.get("deploy", "catalog-mirror", "ckad-07"), "spec.strategy.rollingUpdate.maxUnavailable"))) == ("2", "0")),
        Check("rolled back: image is nginx:1.26-alpine", guard(REV3_07, lambda c: jget(c.get("deploy", "catalog-mirror", "ckad-07"), "spec.template.spec.containers.0.image") == "nginx:1.26-alpine")),
        Check("rollout history shows the update + rollback (revision >= 3)",
              lambda c: int(jget(c.get("deploy", "catalog-mirror", "ckad-07"), "metadata.annotations", {}).get("deployment.kubernetes.io/revision", "0")) >= 3),
        Check("4 replicas available", guard(REV3_07, lambda c: c.deploy_ready("catalog-mirror", "ckad-07"))),
    ],
    solution="""
    kubectl -n ckad-07 patch deploy catalog-mirror -p '{"spec":{"strategy":{"rollingUpdate":{"maxSurge":2,"maxUnavailable":0}}}}'
    kubectl -n ckad-07 set image deploy/catalog-mirror nginx=nginx:1.27-alpine
    kubectl -n ckad-07 rollout status deploy/catalog-mirror --timeout=120s || true
    kubectl -n ckad-07 rollout undo deploy/catalog-mirror
    kubectl -n ckad-07 rollout history deploy/catalog-mirror
    """,
))

TASKS.append(Task(
    id="ckad-08", track="ckad", domain=D2, points=6, minutes=7,
    title="Canary: send ~20% of traffic to v2",
    text="""
    In namespace `ckad-08`, Deployment `web-v1` (labels app=web, version=v1, 5 replicas) is behind Service `web`.

    Release v2 as a canary that receives about 20% of the traffic:
    - create Deployment `web-v2`, image `nginx:1.27-alpine`, pod labels app=web and version=v2
    - total pods behind the Service must stay 5: 4 from v1 and 1 from v2
    - do NOT modify the Service
    """,
    setup=ns("ckad-08") + """
    kubectl apply -n ckad-08 -f - >/dev/null <<'EOF'
    apiVersion: apps/v1
    kind: Deployment
    metadata: { name: web-v1 }
    spec:
      replicas: 5
      selector: { matchLabels: { app: web, version: v1 } }
      template:
        metadata: { labels: { app: web, version: v1 } }
        spec: { containers: [{ name: nginx, image: nginx:1.26-alpine }] }
    ---
    apiVersion: v1
    kind: Service
    metadata: { name: web }
    spec:
      selector: { app: web }
      ports: [{ port: 80 }]
    EOF
    """,
    cleanup=rmns("ckad-08"),
    checks=[
        Check("web-v1 scaled to 4", lambda c: jget(c.get("deploy", "web-v1", "ckad-08"), "spec.replicas") == 4),
        Check("web-v2 has 1 replica with nginx:1.27-alpine", lambda c: jget(c.get("deploy", "web-v2", "ckad-08"), "spec.replicas") == 1
              and jget(c.get("deploy", "web-v2", "ckad-08"), "spec.template.spec.containers.0.image") == "nginx:1.27-alpine"),
        Check("web-v2 pods labelled app=web, version=v2", lambda c: jget(c.get("deploy", "web-v2", "ckad-08"), "spec.template.metadata.labels", {}) .get("app") == "web"
              and jget(c.get("deploy", "web-v2", "ckad-08"), "spec.template.metadata.labels", {}).get("version") == "v2"),
        Check("Service unchanged (selector app=web only)", lambda c: c.get("deploy", "web-v2", "ckad-08") is not None and jget(c.get("svc", "web", "ckad-08"), "spec.selector") == {"app": "web"}),
        Check("Service has 5 ready endpoints incl. 1 v2", lambda c: len(endpoints(c, "web", "ckad-08")) == 5 and len(c.pods_ready("ckad-08", "app=web,version=v2")) == 1),
    ],
    solution="""
    kubectl -n ckad-08 scale deploy web-v1 --replicas=4
    kubectl -n ckad-08 get deploy web-v1 -o yaml > /tmp/v2.yaml   # copy & edit: name web-v2, version v2, replicas 1, new image
    kubectl apply -n ckad-08 -f - <<'EOF'
    apiVersion: apps/v1
    kind: Deployment
    metadata: { name: web-v2 }
    spec:
      replicas: 1
      selector: { matchLabels: { app: web, version: v2 } }
      template:
        metadata: { labels: { app: web, version: v2 } }
        spec: { containers: [{ name: nginx, image: nginx:1.27-alpine }] }
    EOF
    """,
))

GREEN09 = lambda c: jget(c.get("svc", "shop-live", "ckad-09"), "spec.selector", {}).get("color") == "green"
TASKS.append(Task(
    id="ckad-09", track="ckad", domain=D2, points=4, minutes=4,
    title="Blue/green switch",
    text="""
    Namespace `ckad-09` runs two complete versions of the storefront: Deployments `shop-blue` (color=blue)
    and `shop-green` (color=green). Service `shop-live` currently sends traffic to blue.

    Green has passed testing. Switch ALL live traffic to green by changing only the Service.
    Do not delete blue (it's the rollback path).
    """,
    setup=ns("ckad-09") + """
    for c in blue green; do
    kubectl apply -n ckad-09 -f - >/dev/null <<EOF
    apiVersion: apps/v1
    kind: Deployment
    metadata: { name: shop-$c }
    spec:
      replicas: 2
      selector: { matchLabels: { app: shop, color: $c } }
      template:
        metadata: { labels: { app: shop, color: $c } }
        spec: { containers: [{ name: web, image: nginx:1.27-alpine }] }
    EOF
    done
    kubectl -n ckad-09 create service clusterip shop-live --tcp=80:80 --dry-run=client -o yaml | \\
      sed 's/app: shop-live/app: shop\\n    color: blue/' | kubectl apply -f - >/dev/null
    """,
    cleanup=rmns("ckad-09"),
    checks=[
        Check("shop-live selects color=green", lambda c: jget(c.get("svc", "shop-live", "ckad-09"), "spec.selector", {}).get("color") == "green"),
        Check("selector still requires app=shop", guard(GREEN09, lambda c: jget(c.get("svc", "shop-live", "ckad-09"), "spec.selector", {}).get("app") == "shop")),
        Check("blue deployment still exists (rollback path)", guard(GREEN09, lambda c: c.get("deploy", "shop-blue", "ckad-09") is not None)),
        Check("endpoints are exactly the green pods", lambda c: sorted(a for e in endpoints(c, "shop-live", "ckad-09") for a in e["addresses"])
              == sorted(p["status"].get("podIP") for p in c.pods_ready("ckad-09", "app=shop,color=green")) != []),
    ],
    solution="""
    kubectl -n ckad-09 patch service shop-live -p '{"spec":{"selector":{"app":"shop","color":"green"}}}'
    kubectl -n ckad-09 get endpointslices -l kubernetes.io/service-name=shop-live
    """,
))

TASKS.append(Task(
    id="ckad-10", track="ckad", domain=D2, points=7, minutes=8, needs={"helm"},
    title="Helm: install, upgrade, roll back",
    text="""
    A small chart lives at `{repo}/exam/charts/mini-web`.

    1. Install it as release `mini` in namespace `ckad-10` (create the namespace with Helm) with replicaCount=2.
    2. Upgrade the release: set image.tag=1.27-alpine and service.type=NodePort.
    3. The upgrade broke something: roll the release back to revision 1.
    4. Write the output of `helm list -n ckad-10` to `{answers}/ckad-10.txt`.
    """,
    setup="helm uninstall mini -n ckad-10 >/dev/null 2>&1 || true\n" + rmns("ckad-10") + "sleep 2",
    cleanup="helm uninstall mini -n ckad-10 >/dev/null 2>&1 || true\n" + rmns("ckad-10"),
    checks=[
        Check("release mini exists in ckad-10", lambda c: "mini" in c.sh("helm list -n ckad-10 -q").stdout.split()),
        Check("at least 3 revisions (install, upgrade, rollback)", lambda c: len(json_load(c.sh("helm history mini -n ckad-10 -o json").stdout)) >= 3),
        Check("current revision is a rollback to 1", lambda c: "Rollback to 1" in json_load(c.sh("helm history mini -n ckad-10 -o json").stdout)[-1].get("description", "")),
        Check("deployment back to 2 replicas, service ClusterIP", lambda c: jget(c.get("deploy", "mini-mini-web", "ckad-10"), "spec.replicas") == 2
              and jget(c.get("svc", "mini-mini-web", "ckad-10"), "spec.type") == "ClusterIP"),
        Check("helm list saved to file", lambda c: "mini" in (c.answer("ckad-10.txt") or "")),
    ],
    solution="""
    helm install mini $REPO/exam/charts/mini-web -n ckad-10 --create-namespace --set replicaCount=2
    helm upgrade mini $REPO/exam/charts/mini-web -n ckad-10 --reuse-values --set image.tag=1.27-alpine --set service.type=NodePort
    helm history mini -n ckad-10
    helm rollback mini 1 -n ckad-10
    helm list -n ckad-10 > $ANSWERS/ckad-10.txt
    """,
))

TASKS.append(Task(
    id="ckad-11", track="ckad", domain=D2, points=6, minutes=7,
    title="Kustomize: a production overlay",
    text="""
    `{repo}/exam/kustomize/base` contains a Deployment `api` and a Service `api`.

    Create an overlay in `{answers}/kustomize/prod` (copy the base there first, as `{answers}/kustomize/base`) that:
    - deploys into namespace `ckad-11`
    - prefixes every name with `prod-`
    - adds the label `env: prod` to all resources
    - sets the Deployment's replicas to 3
    - generates a ConfigMap `api-config` with literal FEATURE_FLAGS=checkout-v2

    Apply it with `kubectl apply -k`.
    """,
    setup=ns("ckad-11") + 'rm -rf "$ANSWERS/kustomize"',
    cleanup=rmns("ckad-11") + 'rm -rf "$ANSWERS/kustomize"',
    checks=[
        Check("deployment prod-api exists in ckad-11", lambda c: c.get("deploy", "prod-api", "ckad-11") is not None),
        Check("replicas 3", lambda c: jget(c.get("deploy", "prod-api", "ckad-11"), "spec.replicas") == 3),
        Check("label env=prod on deployment and service", lambda c: jget(c.get("deploy", "prod-api", "ckad-11"), "metadata.labels", {}).get("env") == "prod"
              and jget(c.get("svc", "prod-api", "ckad-11"), "metadata.labels", {}).get("env") == "prod"),
        Check("generated configmap prod-api-config-<hash> with FEATURE_FLAGS",
              lambda c: any(cm["metadata"]["name"].startswith("prod-api-config-") and cm.get("data", {}).get("FEATURE_FLAGS") == "checkout-v2" for cm in c.items("configmap", "ckad-11"))),
        Check("overlay kustomization.yaml exists", lambda c: c.sh(f'test -f "{c.v["answers"]}/kustomize/prod/kustomization.yaml"').returncode == 0),
    ],
    solution="""
    mkdir -p $ANSWERS/kustomize && cp -r $REPO/exam/kustomize/base $ANSWERS/kustomize/base
    mkdir -p $ANSWERS/kustomize/prod
    cat > $ANSWERS/kustomize/prod/kustomization.yaml <<'EOF'
    apiVersion: kustomize.config.k8s.io/v1beta1
    kind: Kustomization
    namespace: ckad-11
    namePrefix: prod-
    labels:
      - pairs: { env: prod }
        includeSelectors: false
    resources: [../base]
    replicas:
      - name: api
        count: 3
    configMapGenerator:
      - name: api-config
        literals: [FEATURE_FLAGS=checkout-v2]
    EOF
    kubectl apply -k $ANSWERS/kustomize/prod
    """,
))

# ============================================================================ Observability and Maintenance
STARTUP12 = lambda c: jget(c.get("deploy", "slowstart", "ckad-12"), "spec.template.spec.containers.0.startupProbe") is not None
TASKS.append(Task(
    id="ckad-12", track="ckad", domain=D3, points=6, minutes=7,
    title="Fix a probe that kills a slow-starting app",
    text="""
    Deployment `slowstart` in namespace `ckad-12` never becomes ready: its container needs ~25 seconds to start,
    but the liveness probe kills it first (check RESTARTS and `kubectl describe`).

    Fix it WITHOUT changing the existing liveness probe: add a startupProbe (httpGet on path / port 80) that allows
    up to 60 seconds for startup (e.g. periodSeconds 2, failureThreshold 30). The Deployment must become Available.
    """,
    setup=ns("ckad-12") + """
    kubectl apply -n ckad-12 -f - >/dev/null <<'EOF'
    apiVersion: apps/v1
    kind: Deployment
    metadata: { name: slowstart }
    spec:
      replicas: 1
      selector: { matchLabels: { app: slowstart } }
      template:
        metadata: { labels: { app: slowstart } }
        spec:
          containers:
            - name: web
              image: nginx:1.27-alpine
              command: ["sh", "-c", "sleep 25; exec nginx -g 'daemon off;'"]
              livenessProbe:
                httpGet: { path: /, port: 80 }
                periodSeconds: 5
                failureThreshold: 2
              readinessProbe:
                httpGet: { path: /, port: 80 }
    EOF
    """,
    cleanup=rmns("ckad-12"),
    checks=[
        Check("startupProbe httpGet / :80", lambda c: jget(c.get("deploy", "slowstart", "ckad-12"), "spec.template.spec.containers.0.startupProbe.httpGet.path") == "/"
              and str(jget(c.get("deploy", "slowstart", "ckad-12"), "spec.template.spec.containers.0.startupProbe.httpGet.port")) == "80"),
        Check("startup budget >= 60s", lambda c: (jget(c.get("deploy", "slowstart", "ckad-12"), "spec.template.spec.containers.0.startupProbe.periodSeconds", 10)
                                                  * jget(c.get("deploy", "slowstart", "ckad-12"), "spec.template.spec.containers.0.startupProbe.failureThreshold", 3)) >= 60),
        Check("liveness probe unchanged", guard(STARTUP12, lambda c: jget(c.get("deploy", "slowstart", "ckad-12"), "spec.template.spec.containers.0.livenessProbe.failureThreshold") == 2)),
        Check("deployment Available", guard(STARTUP12, lambda c: c.deploy_ready("slowstart", "ckad-12"))),
    ],
    solution="""
    kubectl -n ckad-12 patch deploy slowstart --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/startupProbe",
      "value":{"httpGet":{"path":"/","port":80},"periodSeconds":2,"failureThreshold":30}}]'
    kubectl -n ckad-12 rollout status deploy/slowstart --timeout=120s || true
    """,
))

TASKS.append(Task(
    id="ckad-13", track="ckad", domain=D3, points=5, minutes=6,
    title="Migrate manifests off removed API versions",
    text="""
    `{repo}/exam/files/legacy.yaml` was written for an old cluster and fails to apply.

    Copy it to `{answers}/ckad-13.yaml`, update every object to an API version served by this cluster
    (adjust fields where the schema changed), and apply it to namespace `ckad-13`.
    Hint: `kubectl api-resources`, `kubectl explain <kind> --api-version=...`.
    """,
    setup=ns("ckad-13"), cleanup=rmns("ckad-13") + 'rm -f "$ANSWERS/ckad-13.yaml"',
    checks=[
        Check("CronJob legacy-cleanup exists (batch/v1)", lambda c: c.get("cronjob", "legacy-cleanup", "ckad-13") is not None),
        Check("PodDisruptionBudget legacy-pdb exists (policy/v1)", lambda c: c.get("pdb", "legacy-pdb", "ckad-13") is not None),
        Check("HPA legacy-hpa exists (autoscaling/v2) targeting 70% CPU", lambda c: jget(c.get("hpa.v2.autoscaling", "legacy-hpa", "ckad-13"), "spec.metrics.0.resource.target.averageUtilization") == 70),
        Check("Ingress legacy-ingress exists (networking.k8s.io/v1) with backend service legacy-web:80",
              lambda c: jget(c.get("ingress", "legacy-ingress", "ckad-13"), "spec.rules.0.http.paths.0.backend.service.name") == "legacy-web"),
    ],
    solution="""
    cat > $ANSWERS/ckad-13.yaml <<'EOF'
    apiVersion: batch/v1
    kind: CronJob
    metadata: { name: legacy-cleanup }
    spec:
      schedule: "0 * * * *"
      jobTemplate:
        spec:
          template:
            spec:
              restartPolicy: OnFailure
              containers: [{ name: c, image: busybox:1.36, command: ["echo", "cleanup"] }]
    ---
    apiVersion: policy/v1
    kind: PodDisruptionBudget
    metadata: { name: legacy-pdb }
    spec:
      minAvailable: 1
      selector: { matchLabels: { app: legacy-web } }
    ---
    apiVersion: autoscaling/v2
    kind: HorizontalPodAutoscaler
    metadata: { name: legacy-hpa }
    spec:
      scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: legacy-web }
      minReplicas: 1
      maxReplicas: 3
      metrics:
        - type: Resource
          resource: { name: cpu, target: { type: Utilization, averageUtilization: 70 } }
    ---
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata: { name: legacy-ingress }
    spec:
      rules:
        - http:
            paths:
              - path: /
                pathType: Prefix
                backend: { service: { name: legacy-web, port: { number: 80 } } }
    EOF
    kubectl apply -n ckad-13 -f $ANSWERS/ckad-13.yaml
    """,
))

FIX14 = lambda c: "SYNC_INTERVAL" in json_dump(c.get("cm", "sync-config", "ckad-14")) or "SYNC_INTERVAL" in json_dump(jget(c.get("deploy", "inventory-sync", "ckad-14"), "spec.template.spec.containers.0.env"))
TASKS.append(Task(
    id="ckad-14", track="ckad", domain=D3, points=6, minutes=6,
    title="Debug a CrashLoopBackOff",
    text="""
    Deployment `inventory-sync` in namespace `ckad-14` is in CrashLoopBackOff.
    Find out why (logs of the *previous* container, describe, the ConfigMap it uses) and fix the root cause
    so the Deployment becomes Available. Do not change the container's command.
    Write the root cause in one line to `{answers}/ckad-14.txt`.
    """,
    setup=ns("ckad-14") + """
    kubectl -n ckad-14 create configmap sync-config --from-literal=TARGET_URL=http://catalog.shopfleet:8080 --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    kubectl apply -n ckad-14 -f - >/dev/null <<'EOF'
    apiVersion: apps/v1
    kind: Deployment
    metadata: { name: inventory-sync }
    spec:
      replicas: 1
      selector: { matchLabels: { app: inventory-sync } }
      template:
        metadata: { labels: { app: inventory-sync } }
        spec:
          containers:
            - name: sync
              image: busybox:1.36
              command: ["sh", "-c", "if [ -z \\"$SYNC_INTERVAL\\" ]; then echo 'FATAL: SYNC_INTERVAL not set'; exit 1; fi; while true; do echo syncing every $SYNC_INTERVAL s; sleep $SYNC_INTERVAL; done"]
              envFrom: [{ configMapRef: { name: sync-config } }]
    EOF
    """,
    cleanup=rmns("ckad-14") + 'rm -f "$ANSWERS/ckad-14.txt"',
    checks=[
        Check("SYNC_INTERVAL provided (ConfigMap or env)", lambda c: "SYNC_INTERVAL" in json_dump(c.get("cm", "sync-config", "ckad-14")) or "SYNC_INTERVAL" in json_dump(jget(c.get("deploy", "inventory-sync", "ckad-14"), "spec.template.spec.containers.0.env"))),
        Check("command unchanged", lambda c: FIX14(c) and "FATAL: SYNC_INTERVAL" in json_dump(jget(c.get("deploy", "inventory-sync", "ckad-14"), "spec.template.spec.containers.0.command"))),
        Check("deployment Available", guard(lambda c: FIX14(c), lambda c: c.deploy_ready("inventory-sync", "ckad-14"))),
        Check("root cause written", lambda c: "SYNC_INTERVAL" in (c.answer("ckad-14.txt") or "").upper()),
    ],
    solution="""
    kubectl -n ckad-14 logs deploy/inventory-sync --previous || true   # FATAL: SYNC_INTERVAL not set
    kubectl -n ckad-14 patch configmap sync-config -p '{"data":{"SYNC_INTERVAL":"30"}}'
    kubectl -n ckad-14 rollout restart deploy/inventory-sync      # env from ConfigMaps is read only at start!
    echo "SYNC_INTERVAL missing from ConfigMap sync-config" > $ANSWERS/ckad-14.txt
    """,
))

TASKS.append(Task(
    id="ckad-15", track="ckad", domain=D3, points=5, minutes=6,
    title="Investigate with get, describe and events",
    text="""
    Answer from the live cluster (one value per line, in this order) into `{answers}/ckad-15.txt`:

    1. the name of the node running pod `postgres-0` in namespace `shopfleet`
    2. the QoS class of that pod
    3. the number of containers (init + regular) in any `frontend` pod in namespace `shopfleet`
    4. the image of the ShopFleet `payment` container (namespace `shopfleet`)
    """,
    setup='rm -f "$ANSWERS/ckad-15.txt"', cleanup='rm -f "$ANSWERS/ckad-15.txt"',
    checks=[
        Check("4 lines", lambda c: len((c.answer("ckad-15.txt") or "").splitlines()) == 4),
        Check("node of postgres-0", lambda c: c.answer("ckad-15.txt") is not None and (c.answer("ckad-15.txt") + "\n").splitlines()[0].strip() == (jget(c.get("pod", "postgres-0", "shopfleet"), "spec.nodeName") or "")),
        Check("QoS class", lambda c: line(c, "ckad-15.txt", 1) == jget(c.get("pod", "postgres-0", "shopfleet"), "status.qosClass")),
        Check("frontend container count", lambda c: line(c, "ckad-15.txt", 2) == str(len(jget(c.items("pods", "shopfleet", "app=frontend")[0], "spec.containers", [])) + len(jget(c.items("pods", "shopfleet", "app=frontend")[0], "spec.initContainers", [])))),
        Check("payment image", lambda c: line(c, "ckad-15.txt", 3) == jget(c.get("deploy", "payment", "shopfleet"), "spec.template.spec.containers.0.image")),
    ],
    solution="""
    {
      kubectl -n shopfleet get pod postgres-0 -o jsonpath='{.spec.nodeName}'; echo
      kubectl -n shopfleet get pod postgres-0 -o jsonpath='{.status.qosClass}'; echo
      P=$(kubectl -n shopfleet get pod -l app=frontend -o jsonpath='{.items[0].metadata.name}')
      echo $(( $(kubectl -n shopfleet get pod $P -o jsonpath='{.spec.containers[*].name}' | wc -w) + $(kubectl -n shopfleet get pod $P -o jsonpath='{.spec.initContainers[*].name}' | wc -w) ))
      kubectl -n shopfleet get deploy payment -o jsonpath='{.spec.template.spec.containers[0].image}'; echo
    } > $ANSWERS/ckad-15.txt
    """,
))

TASKS.append(Task(
    id="ckad-16", track="ckad", domain=D3, points=4, minutes=4, needs={"metrics"},
    title="Resource usage with kubectl top",
    text="""
    Find the pod in namespace `shopfleet` that currently uses the MOST memory, and write its name
    (just the name) to `{answers}/ckad-16.txt`.
    """,
    setup='rm -f "$ANSWERS/ckad-16.txt"', cleanup='rm -f "$ANSWERS/ckad-16.txt"',
    checks=[
        Check("answer is one of the top-3 memory users right now (usage moves!)",
              lambda c: (c.answer("ckad-16.txt") or "x") in c.sh("kubectl top pods -n shopfleet --no-headers --sort-by=memory | head -3 | awk '{print $1}'").stdout.split()),
    ],
    solution="kubectl top pods -n shopfleet --sort-by=memory --no-headers | head -1 | awk '{print $1}' > $ANSWERS/ckad-16.txt",
))

# ============================================================================ Environment, Configuration and Security
TASKS.append(Task(
    id="ckad-17", track="ckad", domain=D4, points=7, minutes=8,
    title="ConfigMaps and Secrets: env vars and files",
    text="""
    In namespace `ckad-17`:
    1. ConfigMap `storefront-config` with key THEME=dark, plus a key `banner.txt` whose content comes from the file
       `{repo}/exam/files/banner.txt`.
    2. Secret `storefront-secret` with API_KEY=sk-live-123 and API_REGION=eu.
    3. Pod `storefront`, image `busybox:1.36`, command `sleep 3600`, with:
       - env var UI_THEME taken from ConfigMap key THEME
       - ALL keys of the Secret as env vars
       - the `banner.txt` key mounted as the file /etc/banner/banner.txt
    """,
    setup=ns("ckad-17"), cleanup=rmns("ckad-17"),
    checks=[
        Check("configmap has THEME=dark and banner.txt", lambda c: jget(c.get("cm", "storefront-config", "ckad-17"), "data.THEME") == "dark"
              and "FLASH" in (jget(c.get("cm", "storefront-config", "ckad-17"), "data", {}).get("banner.txt") or "")),
        Check("secret has API_KEY and API_REGION", lambda c: set(jget(c.get("secret", "storefront-secret", "ckad-17"), "data", {})) >= {"API_KEY", "API_REGION"}),
        Check("UI_THEME from configMapKeyRef THEME", lambda c: any(e["name"] == "UI_THEME" and jget(e, "valueFrom.configMapKeyRef.key") == "THEME"
                                                             for e in jget(c.get("pod", "storefront", "ckad-17"), "spec.containers.0.env", []))),
        Check("envFrom the secret", lambda c: any(jget(e, "secretRef.name") == "storefront-secret" for e in jget(c.get("pod", "storefront", "ckad-17"), "spec.containers.0.envFrom", []))),
        Check("banner mounted at /etc/banner/banner.txt", lambda c: "FLASH" in c.kubectl("exec", "-n", "ckad-17", "storefront", "--", "cat", "/etc/banner/banner.txt").stdout, runtime=True),
        Check("volume from the configmap mounted at /etc/banner", lambda c: any(m["mountPath"] in ("/etc/banner", "/etc/banner/banner.txt") for m in jget(c.get("pod", "storefront", "ckad-17"), "spec.containers.0.volumeMounts", []))),
    ],
    solution="""
    kubectl -n ckad-17 create configmap storefront-config --from-literal=THEME=dark --from-file=banner.txt=$REPO/exam/files/banner.txt
    kubectl -n ckad-17 create secret generic storefront-secret --from-literal=API_KEY=sk-live-123 --from-literal=API_REGION=eu
    kubectl apply -n ckad-17 -f - <<'EOF'
    apiVersion: v1
    kind: Pod
    metadata: { name: storefront }
    spec:
      containers:
        - name: app
          image: busybox:1.36
          command: ["sleep", "3600"]
          env:
            - name: UI_THEME
              valueFrom: { configMapKeyRef: { name: storefront-config, key: THEME } }
          envFrom:
            - secretRef: { name: storefront-secret }
          volumeMounts: [{ name: banner, mountPath: /etc/banner }]
      volumes:
        - name: banner
          configMap:
            name: storefront-config
            items: [{ key: banner.txt, path: banner.txt }]
    EOF
    """,
))

TASKS.append(Task(
    id="ckad-18", track="ckad", domain=D4, points=6, minutes=6,
    title="SecurityContext: harden a pod",
    text="""
    In namespace `ckad-18` create Pod `hardened`, image `busybox:1.36`, command `sleep 3600`:
    - pod level: runAsUser 2000, runAsGroup 3000, fsGroup 4000, seccomp profile RuntimeDefault
    - container level: no privilege escalation, read-only root filesystem, drop ALL capabilities
    - it needs a writable /tmp: mount an emptyDir there
    """,
    setup=ns("ckad-18"), cleanup=rmns("ckad-18"),
    checks=[
        Check("runAsUser/Group/fsGroup 2000/3000/4000", lambda c: [jget(c.get("pod", "hardened", "ckad-18"), f"spec.securityContext.{k}") for k in ("runAsUser", "runAsGroup", "fsGroup")] == [2000, 3000, 4000]),
        Check("seccomp RuntimeDefault", lambda c: "RuntimeDefault" in json_dump(c.get("pod", "hardened", "ckad-18").get("spec"))),
        Check("allowPrivilegeEscalation false", lambda c: jget(c.get("pod", "hardened", "ckad-18"), "spec.containers.0.securityContext.allowPrivilegeEscalation") is False),
        Check("readOnlyRootFilesystem true", lambda c: jget(c.get("pod", "hardened", "ckad-18"), "spec.containers.0.securityContext.readOnlyRootFilesystem") is True),
        Check("drop ALL capabilities", lambda c: "ALL" in jget(c.get("pod", "hardened", "ckad-18"), "spec.containers.0.securityContext.capabilities.drop", [])),
        Check("emptyDir at /tmp", lambda c: any(m["mountPath"] == "/tmp" for m in jget(c.get("pod", "hardened", "ckad-18"), "spec.containers.0.volumeMounts", []))),
        Check("process really runs as uid 2000", lambda c: c.kubectl("exec", "-n", "ckad-18", "hardened", "--", "id", "-u").stdout.strip() == "2000", runtime=True),
    ],
    solution="""
    kubectl apply -n ckad-18 -f - <<'EOF'
    apiVersion: v1
    kind: Pod
    metadata: { name: hardened }
    spec:
      securityContext:
        runAsUser: 2000
        runAsGroup: 3000
        fsGroup: 4000
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: app
          image: busybox:1.36
          command: ["sleep", "3600"]
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts: [{ name: tmp, mountPath: /tmp }]
      volumes: [{ name: tmp, emptyDir: {} }]
    EOF
    """,
))

TASKS.append(Task(
    id="ckad-19", track="ckad", domain=D4, points=6, minutes=6,
    title="ServiceAccount + least-privilege Role",
    text="""
    In namespace `ckad-19`:
    1. Create ServiceAccount `report-reader`.
    2. Create a Role `log-reader` allowing ONLY get/list/watch on pods and get on pods/log.
    3. Bind it to the ServiceAccount (RoleBinding `report-reader-binding`).
    4. Create Pod `reporter`, image `busybox:1.36`, command `sleep 3600`, running as that ServiceAccount.
    """,
    setup=ns("ckad-19"), cleanup=rmns("ckad-19"),
    checks=[
        Check("SA can list pods", lambda c: c.can_i("list", "pods", "system:serviceaccount:ckad-19:report-reader", "ckad-19")),
        Check("SA can get pods/log", lambda c: c.can_i("get", "pods/log", "system:serviceaccount:ckad-19:report-reader", "ckad-19")),
        Check("SA can NOT delete pods", lambda c: c.can_i("list", "pods", "system:serviceaccount:ckad-19:report-reader", "ckad-19") and not c.can_i("delete", "pods", "system:serviceaccount:ckad-19:report-reader", "ckad-19")),
        Check("SA can NOT read secrets", lambda c: c.can_i("list", "pods", "system:serviceaccount:ckad-19:report-reader", "ckad-19") and not c.can_i("get", "secrets", "system:serviceaccount:ckad-19:report-reader", "ckad-19")),
        Check("binding named report-reader-binding", lambda c: c.get("rolebinding", "report-reader-binding", "ckad-19") is not None),
        Check("pod reporter runs as report-reader", lambda c: jget(c.get("pod", "reporter", "ckad-19"), "spec.serviceAccountName") == "report-reader"),
    ],
    solution="""
    kubectl -n ckad-19 create serviceaccount report-reader
    kubectl -n ckad-19 create role log-reader --verb=get,list,watch --resource=pods
    kubectl -n ckad-19 patch role log-reader --type=json -p '[{"op":"add","path":"/rules/-","value":{"apiGroups":[""],"resources":["pods/log"],"verbs":["get"]}}]'
    kubectl -n ckad-19 create rolebinding report-reader-binding --role=log-reader --serviceaccount=ckad-19:report-reader
    kubectl -n ckad-19 run reporter --image=busybox:1.36 --overrides='{"spec":{"serviceAccountName":"report-reader"}}' -- sleep 3600
    kubectl -n ckad-19 auth can-i list pods --as=system:serviceaccount:ckad-19:report-reader
    """,
))

TASKS.append(Task(
    id="ckad-20", track="ckad", domain=D4, points=6, minutes=7,
    title="ResourceQuota and LimitRange",
    text="""
    Namespace `ckad-20` is a team sandbox. Configure:
    1. ResourceQuota `team-quota`: max 5 pods, requests.cpu 1, requests.memory 1Gi, limits.memory 2Gi.
    2. LimitRange `team-defaults` for containers: default request cpu 100m / memory 128Mi, default limit memory 256Mi.
    3. Then create Deployment `sandbox-app` (image nginx:1.27-alpine, 3 replicas) WITHOUT specifying resources
       and confirm its pods got the defaults.
    """,
    setup=ns("ckad-20"), cleanup=rmns("ckad-20"),
    checks=[
        Check("quota values", lambda c: jget(c.get("quota", "team-quota", "ckad-20"), "spec.hard", {}) == {"pods": "5", "requests.cpu": "1", "requests.memory": "1Gi", "limits.memory": "2Gi"}),
        Check("limitrange defaults", lambda c: (jget(c.get("limitrange", "team-defaults", "ckad-20"), "spec.limits.0.defaultRequest", {}) == {"cpu": "100m", "memory": "128Mi"}
                                                and jget(c.get("limitrange", "team-defaults", "ckad-20"), "spec.limits.0.default.memory") == "256Mi")),
        Check("3 sandbox-app pods running", lambda c: len(c.pods_ready("ckad-20", "app=sandbox-app")) == 3),
        Check("pods received the default requests", lambda c: all(jget(p, "spec.containers.0.resources.requests.cpu") == "100m" for p in c.items("pods", "ckad-20", "app=sandbox-app")) and c.items("pods", "ckad-20", "app=sandbox-app")),
    ],
    solution="""
    kubectl -n ckad-20 create quota team-quota --hard=pods=5,requests.cpu=1,requests.memory=1Gi,limits.memory=2Gi
    kubectl apply -n ckad-20 -f - <<'EOF'
    apiVersion: v1
    kind: LimitRange
    metadata: { name: team-defaults }
    spec:
      limits:
        - type: Container
          defaultRequest: { cpu: 100m, memory: 128Mi }
          default: { memory: 256Mi }
    EOF
    kubectl -n ckad-20 create deployment sandbox-app --image=nginx:1.27-alpine --replicas=3
    kubectl -n ckad-20 describe quota team-quota
    """,
))

TASKS.append(Task(
    id="ckad-21", track="ckad", domain=D4, points=5, minutes=5,
    title="Custom resources: create a FlashSale",
    text="""
    ShopFleet extends Kubernetes with a CustomResourceDefinition (group shopfleet.io).

    1. Find the CRD's short name and write it to `{answers}/ckad-21.txt`.
    2. In namespace `shopfleet`, create a FlashSale named `winter-gear`: category Outdoors, 35% discount,
       title "Winter Gear Week", ending at 2030-01-01T00:00:00Z.
    3. Confirm the operator moved it to phase Active (`kubectl get <shortname> -n shopfleet`).
    """,
    setup='kubectl -n shopfleet delete flashsale winter-gear --ignore-not-found >/dev/null 2>&1; rm -f "$ANSWERS/ckad-21.txt"',
    cleanup='kubectl -n shopfleet delete flashsale winter-gear --ignore-not-found --wait=false >/dev/null 2>&1; rm -f "$ANSWERS/ckad-21.txt"',
    checks=[
        Check("short name written (fs)", lambda c: (c.answer("ckad-21.txt") or "").strip() == "fs"),
        Check("FlashSale winter-gear: Outdoors 35%", lambda c: jget(c.get("flashsale", "winter-gear", "shopfleet"), "spec.category") == "Outdoors"
              and jget(c.get("flashsale", "winter-gear", "shopfleet"), "spec.discountPercent") == 35),
        Check("ends 2030-01-01T00:00:00Z", lambda c: str(jget(c.get("flashsale", "winter-gear", "shopfleet"), "spec.endsAt", "")).startswith("2030-01-01T00:00:00")),
        Check("operator reports phase Active", lambda c: jget(c.get("flashsale", "winter-gear", "shopfleet"), "status.phase") == "Active", runtime=True),
    ],
    solution="""
    kubectl get crd flashsales.shopfleet.io -o jsonpath='{.spec.names.shortNames[0]}' > $ANSWERS/ckad-21.txt
    kubectl explain flashsale.spec
    kubectl apply -f - <<'EOF'
    apiVersion: shopfleet.io/v1alpha1
    kind: FlashSale
    metadata: { name: winter-gear, namespace: shopfleet }
    spec:
      title: Winter Gear Week
      category: Outdoors
      discountPercent: 35
      endsAt: "2030-01-01T00:00:00Z"
    EOF
    kubectl get fs -n shopfleet
    """,
))

TASKS.append(Task(
    id="ckad-22", track="ckad", domain=D4, points=5, minutes=5,
    title="Requests, limits and QoS classes",
    text="""
    In namespace `ckad-22` create two pods, both image `nginx:1.27-alpine`:
    - `steady`: QoS class must be Guaranteed, using cpu 200m and memory 128Mi
    - `bursty`: QoS class must be Burstable: request cpu 100m / memory 64Mi, limit memory 256Mi (no CPU limit)
    """,
    setup=ns("ckad-22"), cleanup=rmns("ckad-22"),
    checks=[
        Check("steady is Guaranteed", lambda c: jget(c.get("pod", "steady", "ckad-22"), "status.qosClass") == "Guaranteed"),
        Check("steady uses 200m / 128Mi", lambda c: jget(c.get("pod", "steady", "ckad-22"), "spec.containers.0.resources.limits", {}) == {"cpu": "200m", "memory": "128Mi"}),
        Check("bursty is Burstable", lambda c: jget(c.get("pod", "bursty", "ckad-22"), "status.qosClass") == "Burstable"),
        Check("bursty requests 100m/64Mi, limit memory 256Mi, no cpu limit",
              lambda c: jget(c.get("pod", "bursty", "ckad-22"), "spec.containers.0.resources", {}) == {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}}),
    ],
    solution="""
    kubectl -n ckad-22 run steady --image=nginx:1.27-alpine --dry-run=client -o yaml > /tmp/steady.yaml
    kubectl apply -n ckad-22 -f - <<'EOF'
    apiVersion: v1
    kind: Pod
    metadata: { name: steady }
    spec:
      containers:
        - name: steady
          image: nginx:1.27-alpine
          resources:
            requests: { cpu: 200m, memory: 128Mi }
            limits: { cpu: 200m, memory: 128Mi }
    ---
    apiVersion: v1
    kind: Pod
    metadata: { name: bursty }
    spec:
      containers:
        - name: bursty
          image: nginx:1.27-alpine
          resources:
            requests: { cpu: 100m, memory: 64Mi }
            limits: { memory: 256Mi }
    EOF
    kubectl -n ckad-22 get pods -o custom-columns=NAME:.metadata.name,QOS:.status.qosClass
    """,
))

# ============================================================================ Services and Networking
TASKS.append(Task(
    id="ckad-23", track="ckad", domain=D5, points=5, minutes=5,
    title="Expose the catalog with a NodePort",
    text="""
    Create a Service `catalog-public` in namespace `shopfleet` of type NodePort that exposes the existing
    catalog pods (label app=catalog): service port 80 -> container port 8080, nodePort 30111.
    Verify the Service has endpoints.
    """,
    setup="kubectl -n shopfleet delete svc catalog-public --ignore-not-found >/dev/null",
    cleanup="kubectl -n shopfleet delete svc catalog-public --ignore-not-found >/dev/null",
    checks=[
        Check("type NodePort, port 80 -> 8080, nodePort 30111",
              lambda c: (jget(c.get("svc", "catalog-public", "shopfleet"), "spec.type"), jget(c.get("svc", "catalog-public", "shopfleet"), "spec.ports.0.port"),
                         str(jget(c.get("svc", "catalog-public", "shopfleet"), "spec.ports.0.targetPort")), jget(c.get("svc", "catalog-public", "shopfleet"), "spec.ports.0.nodePort")) == ("NodePort", 80, "8080", 30111)),
        Check("selects app=catalog", lambda c: jget(c.get("svc", "catalog-public", "shopfleet"), "spec.selector") == {"app": "catalog"}),
        Check("has ready endpoints", lambda c: len(endpoints(c, "catalog-public", "shopfleet")) >= 1),
    ],
    solution="""
    kubectl -n shopfleet expose deployment catalog --name=catalog-public --type=NodePort --port=80 --target-port=8080 --dry-run=client -o yaml > /tmp/svc.yaml
    # set nodePort: 30111 under ports, then:
    sed -i 's/targetPort: 8080/targetPort: 8080\\n    nodePort: 30111/' /tmp/svc.yaml
    kubectl apply -f /tmp/svc.yaml
    kubectl -n shopfleet get endpointslices -l kubernetes.io/service-name=catalog-public
    """,
))

TASKS.append(Task(
    id="ckad-24", track="ckad", domain=D5, points=7, minutes=8,
    title="NetworkPolicy: lock down the API",
    text="""
    Namespace `ckad-24` runs pods `api` (label role=api, nginx on port 80), `client` (role=client) and `intruder` (role=intruder).

    Create NetworkPolicy `api-allow-client` so that the api pods accept ingress traffic ONLY from pods with
    label role=client, and only on TCP port 80. All other ingress to api must be blocked. Do not change pod labels.
    """,
    setup=ns("ckad-24") + """
    kubectl -n ckad-24 run api --image=nginx:1.27-alpine --labels=role=api --port=80 >/dev/null 2>&1 || true
    kubectl -n ckad-24 run client --image=busybox:1.36 --labels=role=client -- sleep 3600 >/dev/null 2>&1 || true
    kubectl -n ckad-24 run intruder --image=busybox:1.36 --labels=role=intruder -- sleep 3600 >/dev/null 2>&1 || true
    kubectl -n ckad-24 expose pod api --port=80 >/dev/null 2>&1 || true
    """,
    cleanup=rmns("ckad-24"),
    checks=[
        Check("policy selects role=api", lambda c: jget(c.get("netpol", "api-allow-client", "ckad-24"), "spec.podSelector.matchLabels") == {"role": "api"}),
        Check("Ingress policyType", lambda c: c.get("netpol", "api-allow-client", "ckad-24") is not None and "Ingress" in jget(c.get("netpol", "api-allow-client", "ckad-24"), "spec.policyTypes", ["Ingress"])),
        Check("only from role=client", lambda c: [jget(f, "podSelector.matchLabels") for r in jget(c.get("netpol", "api-allow-client", "ckad-24"), "spec.ingress", []) for f in r.get("from", [])] == [{"role": "client"}]),
        Check("only TCP 80", lambda c: [(p.get("protocol", "TCP"), p.get("port")) for r in jget(c.get("netpol", "api-allow-client", "ckad-24"), "spec.ingress", []) for p in r.get("ports", [])] == [("TCP", 80)]),
        Check("client can reach api", lambda c: c.kubectl("exec", "-n", "ckad-24", "client", "--", "wget", "-qO-", "-T", "3", "http://api").returncode == 0, runtime=True),
        Check("intruder is blocked", lambda c: c.kubectl("exec", "-n", "ckad-24", "intruder", "--", "wget", "-qO-", "-T", "3", "http://api").returncode != 0, runtime=True),
    ],
    solution="""
    kubectl apply -n ckad-24 -f - <<'EOF'
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata: { name: api-allow-client }
    spec:
      podSelector: { matchLabels: { role: api } }
      policyTypes: [Ingress]
      ingress:
        - from:
            - podSelector: { matchLabels: { role: client } }
          ports:
            - { protocol: TCP, port: 80 }
    EOF
    """,
))

TASKS.append(Task(
    id="ckad-25", track="ckad", domain=D5, points=6, minutes=7,
    title="Ingress with host and path routing",
    text="""
    Create an Ingress `shop-ingress` in namespace `shopfleet` using ingressClassName `{ingress_class}`:
    - host `shop.local`
    - path /api/catalog (Prefix) -> Service catalog port 8080
    - path / (Prefix) -> Service frontend port 8080
    Test it:  curl -H 'Host: shop.local' http://<node-ip>:<ingress NodePort>/api/catalog/categories
    """,
    setup="kubectl -n shopfleet delete ingress shop-ingress --ignore-not-found >/dev/null",
    cleanup="kubectl -n shopfleet delete ingress shop-ingress --ignore-not-found >/dev/null",
    checks=[
        Check("ingressClassName set", lambda c: jget(c.get("ingress", "shop-ingress", "shopfleet"), "spec.ingressClassName") == c.v["ingress_class"]),
        Check("host shop.local", lambda c: jget(c.get("ingress", "shop-ingress", "shopfleet"), "spec.rules.0.host") == "shop.local"),
        Check("/api/catalog -> catalog:8080", lambda c: ("/api/catalog", "catalog", 8080) in paths(c.get("ingress", "shop-ingress", "shopfleet"))),
        Check("/ -> frontend:8080", lambda c: ("/", "frontend", 8080) in paths(c.get("ingress", "shop-ingress", "shopfleet"))),
    ],
    solution="""
    kubectl -n shopfleet create ingress shop-ingress --class=$INGRESS_CLASS \\
      --rule="shop.local/api/catalog*=catalog:8080" --rule="shop.local/*=frontend:8080"
    kubectl -n shopfleet describe ingress shop-ingress
    """,
))

TASKS.append(Task(
    id="ckad-26", track="ckad", domain=D5, points=5, minutes=5,
    title="A Service with no endpoints",
    text="""
    In namespace `ckad-26`, Service `orders-api` is supposed to front Deployment `orders-api`, but requests time out.
    Find the problem(s) and fix the SERVICE (not the Deployment) so it routes to the pods on their container port.
    """,
    setup=ns("ckad-26") + """
    kubectl apply -n ckad-26 -f - >/dev/null <<'EOF'
    apiVersion: apps/v1
    kind: Deployment
    metadata: { name: orders-api }
    spec:
      replicas: 2
      selector: { matchLabels: { app: orders-api, tier: backend } }
      template:
        metadata: { labels: { app: orders-api, tier: backend } }
        spec:
          containers:
            - name: web
              image: nginx:1.27-alpine
              ports: [{ name: web, containerPort: 80 }]
    ---
    apiVersion: v1
    kind: Service
    metadata: { name: orders-api }
    spec:
      selector: { app: orders, tier: backend }
      ports: [{ port: 8080, targetPort: 8080 }]
    EOF
    """,
    cleanup=rmns("ckad-26"),
    checks=[
        Check("selector matches the pods", lambda c: jget(c.get("svc", "orders-api", "ckad-26"), "spec.selector", {}).get("app") == "orders-api"),
        Check("targetPort is 80 / 'web'", lambda c: str(jget(c.get("svc", "orders-api", "ckad-26"), "spec.ports.0.targetPort")) in ("80", "web")),
        Check("deployment untouched", lambda c: jget(c.get("svc", "orders-api", "ckad-26"), "spec.selector", {}).get("app") == "orders-api" and jget(c.get("deploy", "orders-api", "ckad-26"), "spec.template.metadata.labels") == {"app": "orders-api", "tier": "backend"}),
        Check("2 ready endpoints", lambda c: len(endpoints(c, "orders-api", "ckad-26")) == 2),
    ],
    solution="""
    kubectl -n ckad-26 get endpointslices -l kubernetes.io/service-name=orders-api   # no endpoints
    kubectl -n ckad-26 get pods --show-labels                                         # app=orders-api, port 80
    kubectl -n ckad-26 patch svc orders-api --type=json -p '[{"op":"replace","path":"/spec/selector/app","value":"orders-api"},
      {"op":"replace","path":"/spec/ports/0/targetPort","value":80}]'
    """,
))


# ---------------------------------------------------------------------------- helpers used by checks
def json_dump(o):
    import json
    return json.dumps(o or {})


def json_load(s):
    import json
    try:
        return json.loads(s)
    except ValueError:
        return []


def line(c, fname, n):
    lines = (c.answer(fname) or "").splitlines()
    return lines[n].strip() if len(lines) > n else None


def endpoints(c, svc, ns):
    eps = c.items("endpointslices", ns, f"kubernetes.io/service-name={svc}")
    return [a for e in eps for a in (e.get("endpoints") or []) if a.get("conditions", {}).get("ready")]


def paths(ing):
    out = []
    for r in jget(ing, "spec.rules", []):
        for p in jget(r, "http.paths", []):
            out.append((p.get("path"), jget(p, "backend.service.name"), jget(p, "backend.service.port.number")))
    return out
