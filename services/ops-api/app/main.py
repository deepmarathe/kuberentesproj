"""ops-api: the backend of ShopFleet's in-app cluster dashboard (admin only).

It talks to the Kubernetes API exactly like kubectl does -- HTTPS + a ServiceAccount
bearer token -- using only the permissions granted by RBAC (k8s/base/ops-api.yaml):

  Role (namespace):      pods/events/services read, deployments read + patch, deployments/scale,
                         hpa/statefulsets/daemonsets/cronjobs read, flashsales CRUD
  ClusterRole (cluster): nodes read   <- nodes are cluster-scoped, so a Role can't grant them

The token comes from a *projected* volume (short-lived, auto-rotated by the kubelet), and is
re-read on every request. /api/ops/whoami shows the dashboard its own RBAC rules.
"""
import datetime as dt
import json
import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

SERVICE = "ops-api"
VERSION = os.getenv("APP_VERSION", "dev")
POD = os.getenv("POD_NAME", socket.gethostname())
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBE_API = os.getenv("KUBE_API") or (
    f"https://{os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')}:{os.getenv('KUBERNETES_SERVICE_PORT', '443')}")
TOKEN_FILE = os.getenv("KUBE_TOKEN_FILE", "/var/run/secrets/tokens/kube-api-token")
CA_FILE = os.getenv("KUBE_CA_FILE", f"{SA_DIR}/ca.crt")
NAMESPACE = os.getenv("POD_NAMESPACE", "shopfleet")
AGENT_SERVICE = os.getenv("NODE_AGENT_SERVICE", "node-agent")
AGENT_PORT = int(os.getenv("NODE_AGENT_PORT", "9100"))
CRD_PATH = "/apis/shopfleet.io/v1alpha1"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"severity": record.levelname, "message": record.getMessage(),
                   "service": SERVICE, "pod": POD, "version": VERSION}
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[_handler], force=True)
log = logging.getLogger(SERVICE)

REQS = Counter("http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
LAT = Histogram("http_request_duration_seconds", "HTTP latency", ["service", "method", "route"])
KUBE_CALLS = Counter("opsapi_kube_api_calls_total", "Calls to the Kubernetes API", ["verb", "code"])

app = FastAPI(title="ops-api")


# ---------- Kubernetes API client (what client-go / kubectl do, in ~30 lines) ----------
class KubeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _token() -> str:
    for path in (TOKEN_FILE, f"{SA_DIR}/token"):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            continue
    raise KubeError(500, "no service account token mounted")


def kube(method: str, path: str, body=None, content_type="application/json"):
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    verify = CA_FILE if os.path.exists(CA_FILE) else False
    try:
        r = httpx.request(method, KUBE_API + path, headers=headers, verify=verify, timeout=5,
                          content=json.dumps(body) if body is not None else None)
    except httpx.HTTPError as e:
        KUBE_CALLS.labels(method, "error").inc()
        raise KubeError(503, f"kubernetes API unreachable: {e}")
    KUBE_CALLS.labels(method, str(r.status_code)).inc()
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        raise KubeError(r.status_code, msg)
    return r.json() if r.content else {}


# ---------- auth (admin JWT from the auth service) ----------
def _jwt_key() -> str:
    path = os.getenv("JWT_SECRET_FILE", "")
    if path:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            pass
    return os.getenv("JWT_SECRET", "")


def require_admin(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "login required")
    try:
        claims = jwt.decode(authorization[7:], _jwt_key(), algorithms=["HS256"], issuer="shopfleet-auth")
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")
    if claims.get("role") != "admin":
        raise HTTPException(403, "admin only")
    return claims


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        log.exception("unhandled error")
        response, status = JSONResponse({"error": "internal error"}, status_code=500), 500
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if route not in ("/metrics", "/healthz", "/readyz"):
        REQS.labels(SERVICE, request.method, route, str(status)).inc()
        LAT.labels(SERVICE, request.method, route).observe(time.perf_counter() - start)
    response.headers["X-Served-By"] = POD
    response.headers["X-Service"] = f"{SERVICE}@{VERSION}"
    return response


@app.exception_handler(KubeError)
def kube_error(_request, exc: KubeError):
    # Surface RBAC denials (403) and CRD validation errors (422) to the UI verbatim -- they're the lesson.
    return JSONResponse({"error": exc.message, "kubernetes_status": exc.code},
                        status_code=exc.code if 400 <= exc.code < 600 else 502)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        kube("GET", f"/api/v1/namespaces/{NAMESPACE}/pods?limit=1")
    except KubeError as e:
        return JSONResponse({"status": "kubernetes API not usable", "error": e.message}, status_code=503)
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------- helpers that turn API objects into dashboard rows ----------
def _age(ts: str | None) -> int | None:
    if not ts:
        return None
    return int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds())


def _pod_row(p):
    st, spec, meta = p.get("status", {}), p.get("spec", {}), p["metadata"]
    cs = st.get("containerStatuses", []) + st.get("initContainerStatuses", [])
    main = st.get("containerStatuses", [])
    reason = None
    for c in cs:
        waiting = c.get("state", {}).get("waiting")
        if waiting and waiting.get("reason") not in (None, "PodInitializing"):
            reason = waiting["reason"]
        last = c.get("lastState", {}).get("terminated")
        if last and not reason and c.get("restartCount", 0):
            reason = f"last: {last.get('reason')}"
    if meta.get("deletionTimestamp"):
        reason = "Terminating"
    owner = (meta.get("ownerReferences") or [{}])[0]
    return {
        "name": meta["name"],
        "app": meta.get("labels", {}).get("app", owner.get("name", "")),
        "phase": st.get("phase"),
        "ready": sum(1 for c in main if c.get("ready")),
        "containers": len(spec.get("containers", [])),
        "restarts": sum(c.get("restartCount", 0) for c in cs),
        "node": spec.get("nodeName"),
        "ip": st.get("podIP"),
        "owner_kind": owner.get("kind"),
        "qos": st.get("qosClass"),
        "reason": reason or (st.get("reason") if st.get("phase") != "Running" else None),
        "age_seconds": _age(meta.get("creationTimestamp")),
    }


def _node_row(n):
    st, meta, spec = n.get("status", {}), n["metadata"], n.get("spec", {})
    conds = {c["type"]: c["status"] for c in st.get("conditions", [])}
    labels = meta.get("labels", {})
    return {
        "name": meta["name"],
        "ready": conds.get("Ready") == "True",
        "pressure": [t for t in ("MemoryPressure", "DiskPressure", "PIDPressure") if conds.get(t) == "True"],
        "roles": [k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/")] or ["worker"],
        "unschedulable": bool(spec.get("unschedulable")),
        "taints": [f"{t['key']}{'=' + t['value'] if t.get('value') else ''}:{t['effect']}" for t in spec.get("taints", [])],
        "cpu": st.get("allocatable", {}).get("cpu"),
        "memory": st.get("allocatable", {}).get("memory"),
        "kubelet": st.get("nodeInfo", {}).get("kubeletVersion"),
        "runtime": st.get("nodeInfo", {}).get("containerRuntimeVersion"),
        "zone": labels.get("topology.kubernetes.io/zone"),
        "pool": labels.get("pool") or labels.get("cloud.google.com/gke-nodepool"),
        "age_seconds": _age(meta.get("creationTimestamp")),
    }


def _workload_row(o):
    spec, st = o.get("spec", {}), o.get("status", {})
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    return {
        "name": o["metadata"]["name"],
        "desired": spec.get("replicas", st.get("desiredNumberScheduled")),
        "ready": st.get("readyReplicas", st.get("numberReady", 0)) or 0,
        "updated": st.get("updatedReplicas", st.get("updatedNumberScheduled", 0)) or 0,
        "available": st.get("availableReplicas", st.get("numberAvailable", 0)) or 0,
        "image": containers[0]["image"].rsplit("/", 1)[-1] if containers else None,
        "generation": o["metadata"].get("generation"),
        "observed": st.get("observedGeneration"),
        "conditions": [{"type": c["type"], "status": c["status"], "reason": c.get("reason")}
                       for c in st.get("conditions", []) if c.get("status") != "True" or c["type"] == "Progressing"],
    }


def _hpa_row(h):
    spec, st = h["spec"], h.get("status", {})
    cpu = None
    for m in st.get("currentMetrics") or []:
        if m.get("type") == "Resource" and m["resource"]["name"] == "cpu":
            cpu = m["resource"]["current"].get("averageUtilization")
    target = None
    for m in spec.get("metrics", []):
        if m.get("type") == "Resource" and m["resource"]["name"] == "cpu":
            target = m["resource"]["target"].get("averageUtilization")
    return {"name": h["metadata"]["name"], "target": spec["scaleTargetRef"]["name"],
            "min": spec.get("minReplicas", 1), "max": spec["maxReplicas"],
            "current": st.get("currentReplicas"), "desired": st.get("desiredReplicas"),
            "cpu_utilization": cpu, "cpu_target": target}


_overview_cache = {"at": 0.0, "body": None}


@app.get("/api/ops/overview")
def overview(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if time.time() - _overview_cache["at"] < 2 and _overview_cache["body"]:
        return _overview_cache["body"]
    ns = NAMESPACE
    calls = {
        "pods": f"/api/v1/namespaces/{ns}/pods",
        "deployments": f"/apis/apps/v1/namespaces/{ns}/deployments",
        "statefulsets": f"/apis/apps/v1/namespaces/{ns}/statefulsets",
        "daemonsets": f"/apis/apps/v1/namespaces/{ns}/daemonsets",
        "hpas": f"/apis/autoscaling/v2/namespaces/{ns}/horizontalpodautoscalers",
        "cronjobs": f"/apis/batch/v1/namespaces/{ns}/cronjobs",
        "events": f"/api/v1/namespaces/{ns}/events?fieldSelector=type%3DWarning",
        "nodes": "/api/v1/nodes",
    }
    results, errors = {}, {}

    def fetch(item):
        k, path = item
        try:
            results[k] = kube("GET", path).get("items", [])
        except KubeError as e:
            errors[k] = f"{e.code}: {e.message}"   # e.g. 403 if the ClusterRole for nodes is missing
            results[k] = []

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(fetch, calls.items()))

    events = sorted(results["events"], key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)[:15]
    body = {
        "namespace": ns,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "served_by": POD,
        "nodes": [_node_row(n) for n in results["nodes"]],
        "pods": sorted((_pod_row(p) for p in results["pods"]), key=lambda r: (r["app"] or "", r["name"])),
        "deployments": [_workload_row(d) for d in results["deployments"]],
        "statefulsets": [_workload_row(d) for d in results["statefulsets"]],
        "daemonsets": [_workload_row(d) for d in results["daemonsets"]],
        "hpas": [_hpa_row(h) for h in results["hpas"]],
        "cronjobs": [{"name": c["metadata"]["name"], "schedule": c["spec"]["schedule"],
                      "suspend": c["spec"].get("suspend", False),
                      "last_schedule": c.get("status", {}).get("lastScheduleTime")} for c in results["cronjobs"]],
        "events": [{"reason": e.get("reason"), "object": f"{e['involvedObject']['kind']}/{e['involvedObject']['name']}",
                    "message": (e.get("message") or "")[:300], "count": e.get("count", 1),
                    "last_seen": e.get("lastTimestamp") or e.get("eventTime")} for e in events],
        "errors": errors,
    }
    _overview_cache.update(at=time.time(), body=body)
    return body


@app.get("/api/ops/agents")
def agents(authorization: str | None = Header(default=None)):
    """Headless Service DNS returns one A record per DaemonSet pod -> ask each one directly."""
    require_admin(authorization)
    host = f"{AGENT_SERVICE}.{NAMESPACE}.svc.cluster.local"
    try:
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(host, AGENT_PORT, proto=socket.IPPROTO_TCP)})
    except socket.gaierror as e:
        return {"dns_name": host, "ips": [], "agents": [], "error": f"DNS lookup failed: {e}"}

    def ask(ip):
        try:
            return httpx.get(f"http://{ip}:{AGENT_PORT}/info", timeout=1.5).json()
        except Exception as e:
            return {"ip": ip, "error": str(e)[:100]}

    with ThreadPoolExecutor(max_workers=10) as ex:
        found = list(ex.map(ask, ips))
    return {"dns_name": host, "ips": ips, "agents": sorted(found, key=lambda a: a.get("node", ""))}


@app.get("/api/ops/whoami")
def whoami(authorization: str | None = Header(default=None)):
    """The dashboard asks the API server: who am I, and what may I do? (like `kubectl auth can-i --list`)"""
    require_admin(authorization)
    review = kube("POST", "/apis/authentication.k8s.io/v1/selfsubjectreviews",
                  {"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"})
    rules = kube("POST", "/apis/authorization.k8s.io/v1/selfsubjectrulesreviews",
                 {"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectRulesReview",
                  "spec": {"namespace": NAMESPACE}})
    can_nodes = kube("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
                     {"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
                      "spec": {"resourceAttributes": {"verb": "list", "resource": "nodes"}}})
    return {
        "user": review.get("status", {}).get("userInfo", {}),
        "namespace": NAMESPACE,
        "rules": [r for r in rules.get("status", {}).get("resourceRules", [])
                  if r.get("apiGroups") != ["authorization.k8s.io"] and r.get("apiGroups") != ["authentication.k8s.io"]],
        "can_list_nodes": can_nodes.get("status", {}).get("allowed", False),
    }


# ---------- admin actions ----------
@app.post("/api/ops/deployments/{name}/restart")
def restart(name: str, authorization: str | None = Header(default=None)):
    """Same as `kubectl rollout restart`: bump a pod-template annotation -> new ReplicaSet."""
    who = require_admin(authorization)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    kube("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{name}",
         {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}},
         content_type="application/strategic-merge-patch+json")
    log.warning("deployment restarted from dashboard", extra={"fields": {"deployment": name, "by": who.get("email")}})
    return {"restarted": name, "at": now}


class ScaleRequest(BaseModel):
    replicas: int = Field(ge=0, le=20)


@app.patch("/api/ops/deployments/{name}/scale")
def scale(name: str, body: ScaleRequest, authorization: str | None = Header(default=None)):
    """Uses the /scale SUBRESOURCE -- RBAC must grant deployments/scale, not just deployments."""
    who = require_admin(authorization)
    kube("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{name}/scale",
         {"spec": {"replicas": body.replicas}}, content_type="application/merge-patch+json")
    log.warning("deployment scaled from dashboard",
                extra={"fields": {"deployment": name, "replicas": body.replicas, "by": who.get("email")}})
    return {"deployment": name, "replicas": body.replicas}


# ---------- FlashSale custom resources ----------
def _fs_row(o):
    st, spec = o.get("status", {}), o.get("spec", {})
    return {"name": o["metadata"]["name"], "title": spec.get("title"), "category": spec.get("category"),
            "discountPercent": spec.get("discountPercent"), "startsAt": spec.get("startsAt"),
            "endsAt": spec.get("endsAt"), "phase": st.get("phase", "Pending"), "message": st.get("message"),
            "finalizers": o["metadata"].get("finalizers", []),
            "deleting": bool(o["metadata"].get("deletionTimestamp")),
            "age_seconds": _age(o["metadata"].get("creationTimestamp"))}


@app.get("/api/ops/flashsales")
def list_flashsales(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    items = kube("GET", f"{CRD_PATH}/namespaces/{NAMESPACE}/flashsales").get("items", [])
    return {"items": [_fs_row(o) for o in items]}


class FlashSaleRequest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$")
    title: str = Field(min_length=1, max_length=80)
    category: str
    discountPercent: int
    durationMinutes: int = Field(default=30, ge=1, le=10080)
    startInMinutes: int = Field(default=0, ge=0, le=10080)


@app.post("/api/ops/flashsales", status_code=201)
def create_flashsale(body: FlashSaleRequest, authorization: str | None = Header(default=None)):
    who = require_admin(authorization)
    start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(minutes=body.startInMinutes)
    end = start + dt.timedelta(minutes=body.durationMinutes)
    obj = {
        "apiVersion": "shopfleet.io/v1alpha1", "kind": "FlashSale",
        "metadata": {"name": body.name, "labels": {"app.kubernetes.io/created-by": "ops-dashboard"}},
        "spec": {"title": body.title, "category": body.category, "discountPercent": body.discountPercent,
                 "startsAt": start.isoformat().replace("+00:00", "Z"), "endsAt": end.isoformat().replace("+00:00", "Z")},
    }
    # Validation (enum, ranges, endsAt > startsAt via CEL) is done by the API server from the CRD schema.
    created = kube("POST", f"{CRD_PATH}/namespaces/{NAMESPACE}/flashsales", obj)
    log.info("flashsale created from dashboard", extra={"fields": {"flashsale": body.name, "by": who.get("email")}})
    return _fs_row(created)


@app.delete("/api/ops/flashsales/{name}")
def delete_flashsale(name: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    kube("DELETE", f"{CRD_PATH}/namespaces/{NAMESPACE}/flashsales/{name}")
    # With a finalizer, the object lingers (deletionTimestamp set) until the operator cleans up.
    return {"deleted": name}
