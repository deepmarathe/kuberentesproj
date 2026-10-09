"""FlashSale operator: turns `FlashSale` custom resources into live discounts.

    kubectl apply -f flashsale.yaml   ->   operator reconciles   ->   catalog shows sale prices

What a real operator does, in ~300 lines of plain Python:
  * LIST + WATCH the custom resource (resumes from resourceVersion, re-lists on 410 Gone)
  * RECONCILE each object to its desired state (level-triggered: also every RESYNC seconds,
    so time-based transitions Scheduled -> Active -> Expired happen without any event)
  * FINALIZER: on delete, remove the promotion from catalog BEFORE the object disappears
  * STATUS subresource: phase/message/observedGeneration written separately from spec
  * EVENTS: visible in `kubectl describe flashsale NAME`
  * LEADER ELECTION with a coordination.k8s.io Lease, so 2 replicas never act at once
"""
import datetime as dt
import json
import logging
import os
import socket
import threading
import time
import uuid

import httpx
from prometheus_client import Counter, Gauge, start_http_server

SERVICE = "flashsale-operator"
POD = os.getenv("POD_NAME", socket.gethostname())
NAMESPACE = os.getenv("WATCH_NAMESPACE", "shopfleet")
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBE_API = os.getenv("KUBE_API") or (
    f"https://{os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')}:{os.getenv('KUBERNETES_SERVICE_PORT', '443')}")
TOKEN_FILE = os.getenv("KUBE_TOKEN_FILE", "/var/run/secrets/tokens/kube-api-token")
CA_FILE = os.getenv("KUBE_CA_FILE", f"{SA_DIR}/ca.crt")
CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog:8080")
GROUP, VERSION, PLURAL = "shopfleet.io", "v1alpha1", "flashsales"
CR_PATH = f"/apis/{GROUP}/{VERSION}/namespaces/{NAMESPACE}/{PLURAL}"
FINALIZER = "shopfleet.io/promotion-cleanup"
RESYNC = int(os.getenv("RESYNC_SECONDS", "20"))
LEASE_NAME = os.getenv("LEASE_NAME", "flashsale-operator")
LEASE_SECONDS = int(os.getenv("LEASE_DURATION_SECONDS", "15"))

RECONCILES = Counter("flashsale_reconciles_total", "Reconcile loops", ["result"])
ACTIVE = Gauge("flashsale_active", "FlashSales currently active")
LEADER = Gauge("flashsale_operator_is_leader", "1 if this replica holds the lease")

stop = threading.Event()
healthy = {"last_loop": time.time()}


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": SERVICE, "pod": POD, **fields}), flush=True)


# ---------------------------------------------------------------- Kubernetes API
class KubeError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _headers(content_type=None):
    for path in (TOKEN_FILE, f"{SA_DIR}/token"):
        try:
            with open(path) as f:
                token = f.read().strip()
            break
        except OSError:
            continue
    else:
        raise KubeError(500, "no service account token")
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if content_type:
        h["Content-Type"] = content_type
    return h


def _verify():
    return CA_FILE if os.path.exists(CA_FILE) else False


def kube(method, path, body=None, content_type="application/json"):
    r = httpx.request(method, KUBE_API + path, headers=_headers(content_type if body is not None else None),
                      content=json.dumps(body) if body is not None else None, verify=_verify(), timeout=10)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        raise KubeError(r.status_code, msg)
    return r.json() if r.content else {}


def event(obj, reason, message, etype="Normal"):
    """Create a core/v1 Event so `kubectl describe` shows what the operator did."""
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    meta = obj["metadata"]
    try:
        kube("POST", f"/api/v1/namespaces/{NAMESPACE}/events", {
            "apiVersion": "v1", "kind": "Event",
            "metadata": {"name": f"{meta['name']}.{uuid.uuid4().hex[:10]}", "namespace": NAMESPACE},
            "involvedObject": {"apiVersion": f"{GROUP}/{VERSION}", "kind": "FlashSale", "name": meta["name"],
                               "namespace": NAMESPACE, "uid": meta["uid"], "resourceVersion": meta.get("resourceVersion")},
            "reason": reason, "message": message, "type": etype,
            "source": {"component": SERVICE}, "firstTimestamp": now, "lastTimestamp": now, "count": 1,
            "reportingComponent": SERVICE, "reportingInstance": POD,
        })
    except Exception as e:
        log("WARNING", "could not record event", error=str(e)[:200])


# ---------------------------------------------------------------- leader election
class Leader:
    """Minimal Lease-based leader election (the same idea client-go's leaderelection uses)."""

    def __init__(self):
        self.is_leader = False
        self.path = f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases/{LEASE_NAME}"

    @staticmethod
    def _now():
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def try_acquire(self):
        try:
            lease = kube("GET", self.path)
        except KubeError as e:
            if e.code != 404:
                raise
            kube("POST", f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases", {
                "apiVersion": "coordination.k8s.io/v1", "kind": "Lease", "metadata": {"name": LEASE_NAME},
                "spec": {"holderIdentity": POD, "leaseDurationSeconds": LEASE_SECONDS,
                         "acquireTime": self._now(), "renewTime": self._now(), "leaseTransitions": 0}})
            return self._set(True)
        spec = lease.get("spec", {})
        holder = spec.get("holderIdentity")
        renew = spec.get("renewTime")
        expired = True
        if renew:
            renewed = dt.datetime.fromisoformat(renew.replace("Z", "+00:00"))
            expired = (dt.datetime.now(dt.timezone.utc) - renewed).total_seconds() > spec.get("leaseDurationSeconds", LEASE_SECONDS)
        if holder == POD or expired or not holder:
            spec.update({"holderIdentity": POD, "renewTime": self._now(), "leaseDurationSeconds": LEASE_SECONDS})
            if holder != POD:
                spec["acquireTime"] = self._now()
                spec["leaseTransitions"] = spec.get("leaseTransitions", 0) + 1
            lease["spec"] = spec
            try:
                kube("PUT", self.path, lease)  # resourceVersion in body = optimistic concurrency
                return self._set(True)
            except KubeError as e:
                if e.code == 409:  # someone else won the race
                    return self._set(False)
                raise
        return self._set(False)

    def _set(self, value):
        if value != self.is_leader:
            log("WARNING" if value else "INFO", "leadership changed", leader=value)
        self.is_leader = value
        LEADER.set(1 if value else 0)
        return value


# ---------------------------------------------------------------- reconcile
def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def promo_name(obj):
    return f"{NAMESPACE}-{obj['metadata']['name']}"


def set_status(obj, phase, message):
    st = obj.get("status", {})
    gen = obj["metadata"].get("generation")
    if st.get("phase") == phase and st.get("message") == message and st.get("observedGeneration") == gen:
        return
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    kube("PATCH", f"{CR_PATH}/{obj['metadata']['name']}/status",
         {"status": {"phase": phase, "message": message, "observedGeneration": gen, "lastTransitionTime": now}},
         content_type="application/merge-patch+json")
    if st.get("phase") != phase:
        event(obj, phase, message)
        log("INFO", "flashsale phase changed", flashsale=obj["metadata"]["name"], phase=phase, message=message)


def reconcile(obj):
    meta, spec = obj["metadata"], obj.get("spec", {})
    name = meta["name"]

    # 1. Being deleted? Clean up, then release the finalizer so Kubernetes can delete it.
    if meta.get("deletionTimestamp"):
        if FINALIZER in meta.get("finalizers", []):
            httpx.delete(f"{CATALOG_URL}/internal/promotions/{promo_name(obj)}", timeout=5).raise_for_status()
            finalizers = [f for f in meta["finalizers"] if f != FINALIZER]
            kube("PATCH", f"{CR_PATH}/{name}", {"metadata": {"finalizers": finalizers,
                                                             "resourceVersion": meta["resourceVersion"]}},
                 content_type="application/merge-patch+json")
            log("INFO", "finalizer removed; promotion cleaned up", flashsale=name)
        return "deleted"

    # 2. Make sure our finalizer is present before we create anything external.
    if FINALIZER not in meta.get("finalizers", []):
        kube("PATCH", f"{CR_PATH}/{name}", {"metadata": {"finalizers": meta.get("finalizers", []) + [FINALIZER],
                                                         "resourceVersion": meta["resourceVersion"]}},
             content_type="application/merge-patch+json")
        return "finalizer-added"   # the resulting MODIFIED event triggers the next reconcile

    # 3. Desired state from time window.
    now = dt.datetime.now(dt.timezone.utc)
    starts, ends = _ts(spec.get("startsAt")) or _ts(meta["creationTimestamp"]), _ts(spec["endsAt"])
    if spec.get("paused"):
        phase, msg, want = "Paused", "spec.paused is true", False
    elif now < starts:
        phase, msg, want = "Scheduled", f"starts in {int((starts - now).total_seconds() // 60)} min", False
    elif now < ends:
        phase, msg, want = "Active", f"{spec['discountPercent']}% off {spec['category']}, ends in {int((ends - now).total_seconds() // 60)} min", True
    else:
        phase, msg, want = "Expired", "sale window is over", False

    # 4. Converge the external system (catalog) -- idempotent PUT/DELETE.
    if want:
        httpx.put(f"{CATALOG_URL}/internal/promotions/{promo_name(obj)}", timeout=5, json={
            "title": spec.get("title") or name, "category": spec["category"],
            "percent": spec["discountPercent"], "ends_at": spec["endsAt"]}).raise_for_status()
    else:
        httpx.delete(f"{CATALOG_URL}/internal/promotions/{promo_name(obj)}", timeout=5).raise_for_status()

    set_status(obj, phase, msg)
    return phase


def reconcile_safely(obj):
    try:
        result = reconcile(obj)
        RECONCILES.labels("ok").inc()
        return result
    except (KubeError, httpx.HTTPError) as e:
        RECONCILES.labels("error").inc()
        log("ERROR", "reconcile failed (will retry on next resync)", flashsale=obj["metadata"]["name"], error=str(e)[:300])
        try:
            set_status(obj, "Error", str(e)[:200])
        except Exception:
            pass
        return "error"


def resync():
    items = kube("GET", CR_PATH)
    phases = [reconcile_safely(o) for o in items.get("items", [])]
    ACTIVE.set(sum(1 for p in phases if p == "Active"))
    return items["metadata"]["resourceVersion"]


def watch(resource_version, leader):
    """Stream WATCH events until the timeout, then return so the caller can resync."""
    params = {"watch": "1", "resourceVersion": resource_version, "timeoutSeconds": str(RESYNC),
              "allowWatchBookmarks": "true"}
    with httpx.stream("GET", KUBE_API + CR_PATH, params=params, headers=_headers(), verify=_verify(),
                      timeout=httpx.Timeout(RESYNC + 10)) as r:
        if r.status_code == 410:
            return None  # resourceVersion too old -> full re-list
        r.raise_for_status()
        for line in r.iter_lines():
            if stop.is_set() or not leader.is_leader:
                return resource_version
            if not line:
                continue
            evt = json.loads(line)
            obj = evt.get("object", {})
            if evt["type"] == "ERROR":
                return None
            resource_version = obj.get("metadata", {}).get("resourceVersion", resource_version)
            if evt["type"] in ("ADDED", "MODIFIED"):
                reconcile_safely(obj)
            # DELETED: nothing to do -- the finalizer already cleaned up.
    return resource_version


def main():
    port = int(os.getenv("METRICS_PORT", "8080"))
    start_http_server(port)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    log("INFO", "operator starting", namespace=NAMESPACE, resync_seconds=RESYNC)
    leader = Leader()

    def heartbeat():   # renew the lease in the background
        while not stop.is_set():
            try:
                leader.try_acquire()
            except Exception as e:
                leader._set(False)
                log("ERROR", "lease error", error=str(e)[:200])
            healthy["last_loop"] = time.time()
            stop.wait(LEASE_SECONDS / 3)

    threading.Thread(target=heartbeat, daemon=True).start()
    rv = None
    while not stop.is_set():
        if not leader.is_leader:
            stop.wait(2)
            continue
        try:
            rv = resync() if rv is None else rv
            rv = watch(rv, leader)
            if rv is not None:
                rv = resync()   # periodic level-triggered pass (time-based transitions)
        except Exception as e:
            log("ERROR", "watch loop error; backing off", error=str(e)[:300])
            rv = None
            stop.wait(3)
    log("INFO", "operator stopped")


if __name__ == "__main__":
    main()
