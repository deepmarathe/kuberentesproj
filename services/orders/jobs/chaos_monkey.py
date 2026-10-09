"""Chaos monkey: delete one random pod matching TARGET_SELECTOR.

Talks to the Kubernetes API directly with the pod's ServiceAccount token
(the same thing kubectl does) -- standard library only, no client package.
Permissions come from the `chaos-monkey` Role: list + delete pods, nothing more.
"""
import json
import os
import random
import ssl
import urllib.parse
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
API = f"https://{os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')}:{os.getenv('KUBERNETES_SERVICE_PORT', '443')}"


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, "service": "chaos-monkey", **fields}), flush=True)


def call(method, path):
    token = open(f"{SA}/token").read()
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    req = urllib.request.Request(API + path, method=method, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return json.load(r)


def main():
    ns = open(f"{SA}/namespace").read().strip()
    selector = urllib.parse.quote(os.getenv("TARGET_SELECTOR", "shopfleet.io/chaos=allowed"))
    pods = call("GET", f"/api/v1/namespaces/{ns}/pods?labelSelector={selector}")["items"]
    running = [p for p in pods if p["status"].get("phase") == "Running" and not p["metadata"].get("deletionTimestamp")]
    if not running:
        log("INFO", "no eligible pods")
        return
    victim = random.choice(running)["metadata"]["name"]
    call("DELETE", f"/api/v1/namespaces/{ns}/pods/{victim}")
    log("WARNING", "chaos monkey deleted a pod", pod=victim, candidates=len(running))


if __name__ == "__main__":
    main()
