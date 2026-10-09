#!/usr/bin/env python3
"""ShopFleet CKA/CKAD trainer -- exam-style tasks on your own cluster, auto-graded.

  ./exam/trainer list [--track cka|ckad]       what's available in THIS environment
  ./exam/trainer start ckad-07                 set up the scenario and print the task
  ./exam/trainer check ckad-07                 grade your work (re-run as often as you like)
  ./exam/trainer solution ckad-07              the reference solution (after trying!)
  ./exam/trainer reset ckad-07                 clean up
  ./exam/trainer exam ckad [--minutes 120]     timed mock exam (random tasks, weighted by domain)
  ./exam/trainer exam grade | status | abort
  ./exam/trainer progress                      your history
  ./exam/trainer env                           detected environment + capabilities

Standard library only. Talks to the cluster through `kubectl`, exactly like you do in the exam.
"""
import argparse
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.getenv("EXAM_STATE_DIR", Path.home() / ".shopfleet-exam"))
ANSWERS = Path(os.getenv("EXAM_ANSWERS", Path.home() / "exam-answers"))
PASS_MARK = 66

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "b": "\033[1m", "d": "\033[2m", "c": "\033[36m", "x": "\033[0m"}
if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
    C = {k: "" for k in C}


# --------------------------------------------------------------------------- model
@dataclass
class Check:
    desc: str
    fn: object                 # fn(ctx) -> bool | (bool, hint)
    runtime: bool = False      # needs real containers (skipped in simulated clusters)


@dataclass
class Task:
    id: str
    track: str                 # cka | ckad
    domain: str
    title: str
    text: str
    checks: list
    points: int = 4
    minutes: int = 6
    needs: set = field(default_factory=set)   # node, kubeadm, metrics, podman, ingress, gateway, runtime, helm
    setup: str = ""
    solution: str = ""
    cleanup: str = ""
    destructive: bool = False


DOMAINS = {
    "ckad": {
        "Application Design and Build": 20,
        "Application Deployment": 20,
        "Application Observability and Maintenance": 15,
        "Application Environment, Configuration and Security": 25,
        "Services and Networking": 20,
    },
    "cka": {
        "Cluster Architecture, Installation and Configuration": 25,
        "Workloads and Scheduling": 15,
        "Services and Networking": 20,
        "Storage": 10,
        "Troubleshooting": 30,
    },
}


# --------------------------------------------------------------------------- environment
class Env:
    """Detects where we are and knows how to run commands ON a node (like `ssh node01`)."""

    def __init__(self):
        self.kind = os.getenv("EXAM_ENV", "")
        nodes = self._nodes()
        self.node_objs = nodes
        names = [n["metadata"]["name"] for n in nodes]
        cps = [n["metadata"]["name"] for n in nodes if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels", {})]
        workers = sorted(n for n in names if n not in cps)
        if not self.kind:
            if any(n["metadata"].get("annotations", {}).get("kwok.x-k8s.io/node") for n in nodes):
                self.kind = "kwok"
            elif any(n.endswith("-control-plane") for n in names) and shutil.which("docker"):
                self.kind = "kind"
            elif any("cloud.google.com/gke-nodepool" in n["metadata"].get("labels", {}) for n in nodes):
                self.kind = "gke"
            elif "cp1" in names:
                self.kind = "kubeadm"
            else:
                self.kind = "generic"
        self.cp = cps[0] if cps else (names[0] if names else "")
        self.w1 = workers[0] if workers else self.cp
        self.w2 = workers[1] if len(workers) > 1 else self.w1
        self._caps = None

    @staticmethod
    def _nodes():
        r = subprocess.run(["kubectl", "get", "nodes", "-o", "json"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"{C['r']}Cannot reach the cluster with kubectl:{C['x']}\n{r.stderr.strip()}")
        return json.loads(r.stdout)["items"]

    def caps(self):
        if self._caps is None:
            res = subprocess.run(["kubectl", "api-resources", "-o", "name"], capture_output=True, text=True).stdout.split()
            ics = subprocess.run(["kubectl", "get", "ingressclass", "-o", "name"], capture_output=True, text=True).stdout.split()
            gcs = subprocess.run(["kubectl", "get", "gatewayclass", "-o", "name"], capture_output=True, text=True).stdout.split() \
                if "gatewayclasses.gateway.networking.k8s.io" in res else []
            caps = {"helm"} if shutil.which("helm") else set()
            if self.kind in ("kind", "kubeadm"):
                caps |= {"node"}
            if self.kind == "kubeadm":
                caps |= {"kubeadm"}
            if self.kind != "kwok":
                caps |= {"runtime"}
            if "pods.metrics.k8s.io" in res and self.kind != "kwok":
                caps |= {"metrics"}
            if shutil.which("podman") or shutil.which("docker"):
                caps |= {"podman"}
            if ics:
                caps |= {"ingress"}
            if gcs:
                caps |= {"gateway"}
            self.ingress_class = ics[0].split("/")[-1] if ics else "traefik"
            self.gateway_class = gcs[0].split("/")[-1] if gcs else "traefik"
            self._caps = caps
        return self._caps

    def node_argv(self, node, cmd):
        """argv that runs `cmd` as root on `node` (the exam's `ssh node01` + `sudo -i`)."""
        if self.kind == "kind":
            return ["docker", "exec", node, "sh", "-c", cmd]
        if self.kind == "kubeadm":
            if node == os.uname().nodename:
                return ["sudo", "sh", "-c", cmd]
            return ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", node, "sudo sh -c " + shlex.quote(cmd)]
        return ["sh", "-c", "echo 'no node access in this environment' >&2; exit 1"]

    def node_sh_function(self):
        if self.kind == "kind":
            return 'node_sh() { local n=$1; shift; docker exec "$n" sh -c "$*"; }'
        if self.kind == "kubeadm":
            return ('node_sh() { local n=$1; shift; if [ "$n" = "$(hostname)" ]; then sudo sh -c "$*"; '
                    'else ssh -o StrictHostKeyChecking=no -o BatchMode=yes "$n" "sudo sh -c $(printf %q "$*")"; fi; }')
        return 'node_sh() { echo "no node access in this environment" >&2; return 1; }'

    def vars(self):
        self.caps()
        cp_version = next((jget(n, "status.nodeInfo.kubeletVersion") for n in self.node_objs if n["metadata"]["name"] == self.cp), "?")
        return {"cp": self.cp, "w1": self.w1, "w2": self.w2, "answers": str(ANSWERS), "repo": str(ROOT),
                "ingress_class": self.ingress_class, "gateway_class": self.gateway_class,
                "cp_version": cp_version, "gateway_port": os.getenv("EXAM_GATEWAY_PORT", "8000")}


class Ctx:
    """Handed to every check: small, readable helpers around kubectl."""

    def __init__(self, env: Env):
        self.env = env
        self.v = env.vars()

    def kubectl(self, *args, input=None):
        return subprocess.run(["kubectl", *args], capture_output=True, text=True, input=input)

    def get(self, kind, name=None, ns=None):
        args = ["get", kind] + ([name] if name else []) + (["-n", ns] if ns else []) + ["-o", "json"]
        r = self.kubectl(*args)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)

    def items(self, kind, ns=None, selector=None):
        args = ["get", kind, "-o", "json"] + (["-n", ns] if ns else ["-A"] if ns is None and kind not in NON_NS else [])
        if selector:
            args += ["-l", selector]
        r = self.kubectl(*args)
        return json.loads(r.stdout)["items"] if r.returncode == 0 else []

    def can_i(self, verb, resource, as_, ns=None):
        args = ["auth", "can-i", verb, resource, f"--as={as_}"] + (["-n", ns] if ns else [])
        return self.kubectl(*args).stdout.strip() == "yes"

    def sh(self, cmd, timeout=60):
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)

    def node(self, node, cmd, timeout=60):
        """Run as root on a node; returns (rc, stdout)."""
        r = subprocess.run(self.env.node_argv(node, cmd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()

    def answer(self, name):
        p = ANSWERS / name
        # keep blank lines (they matter for "one value per line" answers); trim only the trailing newline(s)
        return p.read_text().rstrip("\n").rstrip() if p.exists() else None

    def pods_ready(self, ns, selector):
        pods = self.items("pods", ns, selector)
        return [p for p in pods if any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", []))]

    def deploy_ready(self, name, ns):
        d = self.get("deployment", name, ns)
        if not d:
            return False
        st = d.get("status", {})
        return st.get("readyReplicas", 0) >= d["spec"].get("replicas", 1) and st.get("updatedReplicas", 0) >= d["spec"].get("replicas", 1)


NON_NS = {"nodes", "pv", "persistentvolumes", "clusterroles", "clusterrolebindings", "storageclass", "storageclasses",
          "priorityclass", "priorityclasses", "crd", "customresourcedefinitions", "ingressclass", "gatewayclass", "namespaces"}


def jget(obj, path, default=None):
    """jget(d, 'spec.template.spec.containers.0.image')"""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            cur = cur.get(part)
    return default if cur is None else cur


# --------------------------------------------------------------------------- task registry
def load_tasks():
    sys.path.insert(0, str(Path(__file__).parent))
    from tasks import cka, ckad  # noqa: E402
    tasks = {t.id: t for t in ckad.TASKS + cka.TASKS}
    return tasks


def shell_prelude(env: Env):
    v = env.vars()
    lines = [f"export {k.upper()}={shlex.quote(str(val))}" for k, val in v.items()]
    lines.append(env.node_sh_function())
    lines.append(f'mkdir -p "{ANSWERS}"')
    return "\n".join(lines) + "\n"


def clear_answers(task_id):
    """Old answer files from a previous attempt must not earn points."""
    if ANSWERS.exists():
        for p in ANSWERS.glob(f"{task_id}*"):
            if p.is_file():
                p.unlink()


def run_script(env, script, label, quiet=False):
    if not script.strip():
        return True
    r = subprocess.run(["bash", "-c", shell_prelude(env) + "set -e\n" + textwrap.dedent(script)], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        print(f"{C['r']}{label} failed:{C['x']}\n{(r.stdout + r.stderr)[-1500:]}")
        return False
    if not quiet and r.stdout.strip():
        print(f"{C['d']}{r.stdout.strip()[-800:]}{C['x']}")
    return True


def render(task, env):
    v = env.vars()
    try:
        return textwrap.dedent(task.text).strip().format(**v)
    except (KeyError, IndexError):
        return textwrap.dedent(task.text).strip()


def grade(task, env, verbose=True, skip_runtime=None):
    ctx = Ctx(env)
    if skip_runtime is None:
        skip_runtime = "runtime" not in env.caps()
    passed = total = 0
    lines = []
    for chk in task.checks:
        if chk.runtime and skip_runtime:
            lines.append(f"  {C['d']}- {chk.desc} (skipped: needs real containers){C['x']}")
            continue
        total += 1
        try:
            res = chk.fn(ctx)
        except Exception as e:  # a crashing check = not passed
            res = (False, f"check error: {e}")
        ok, hint = (res if isinstance(res, tuple) else (bool(res), None))
        passed += 1 if ok else 0
        mark = f"{C['g']}✓{C['x']}" if ok else f"{C['r']}✗{C['x']}"
        lines.append(f"  {mark} {chk.desc}" + (f"  {C['d']}({hint}){C['x']}" if hint and not ok else ""))
    score = round(task.points * passed / total) if total else 0
    if verbose:
        print("\n".join(lines))
        color = C["g"] if passed == total else C["y"] if passed else C["r"]
        print(f"  {color}{passed}/{total} checks · {score}/{task.points} points{C['x']}")
    return passed, total, score


# --------------------------------------------------------------------------- state
def load_state():
    p = STATE_DIR / "state.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"started": {}, "history": [], "exam": None}


def save_state(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(json.dumps(st, indent=2))


def available(task, env):
    missing = task.needs - env.caps()
    return not missing, missing


# --------------------------------------------------------------------------- commands
def cmd_env(args, env, tasks):
    caps = env.caps()
    print(f"{C['b']}Environment:{C['x']} {env.kind}   control plane: {env.cp}   workers: {env.w1}, {env.w2}")
    print(f"{C['b']}Capabilities:{C['x']} {', '.join(sorted(caps)) or 'none'}   ingress class: {env.ingress_class}   gateway class: {env.gateway_class}")
    avail = [t for t in tasks.values() if available(t, env)[0]]
    print(f"{C['b']}Tasks available here:{C['x']} {len(avail)}/{len(tasks)}")
    if env.kind not in ("kubeadm", "kind"):
        print(f"{C['y']}Node-level CKA tasks (etcd, kubelet, static pods, upgrades) need the kubeadm or kind environment: see docs/cka-ckad/01-environments.md{C['x']}")


def cmd_list(args, env, tasks):
    st = load_state()
    best = {}
    for h in st["history"]:
        best[h["task"]] = max(best.get(h["task"], 0), h["score"] / max(h["points"], 1))
    for track in ("ckad", "cka"):
        if args.track and args.track != track:
            continue
        print(f"\n{C['b']}{track.upper()}{C['x']}")
        for dom in DOMAINS[track]:
            ts = [t for t in tasks.values() if t.track == track and t.domain == dom]
            if not ts:
                continue
            print(f"  {C['c']}{dom} ({DOMAINS[track][dom]}%){C['x']}")
            for t in sorted(ts, key=lambda t: t.id):
                ok, missing = available(t, env)
                done = best.get(t.id)
                status = f"{C['g']}✓{C['x']}" if done == 1 else f"{C['y']}~{C['x']}" if done else " "
                need = f"  {C['d']}[needs {', '.join(sorted(missing))}]{C['x']}" if not ok else ""
                warn = f" {C['r']}(disruptive){C['x']}" if t.destructive else ""
                print(f"   {status} {t.id:9} {t.title:58} {t.points}pt ~{t.minutes}m{warn}{need}")
    print(f"\n{C['d']}✓ = full marks before, ~ = partial. Start one:  ./exam/trainer start <id>{C['x']}")


def cmd_start(args, env, tasks):
    t = tasks.get(args.id) or sys.exit(f"unknown task {args.id}")
    ok, missing = available(t, env)
    if not ok and not args.force:
        sys.exit(f"{t.id} needs: {', '.join(sorted(missing))} (this environment: {env.kind}). Use --force to try anyway.")
    if t.destructive and not args.yes:
        if input(f"{C['r']}{t.id} deliberately breaks part of the cluster. Continue? [y/N] {C['x']}").lower() != "y":
            return
    print(f"{C['d']}setting up {t.id}…{C['x']}")
    clear_answers(t.id)
    if not run_script(env, t.setup, "setup"):
        return
    st = load_state()
    st["started"][t.id] = time.time()
    save_state(st)
    print_task(t, env)


def print_task(t, env, number=None):
    head = f"Question {number}" if number else t.id
    print(f"\n{C['b']}{head}: {t.title}{C['x']}   {C['d']}[{t.track.upper()} · {t.domain} · {t.points} points · ~{t.minutes} min]{C['x']}")
    print("─" * 78)
    print(render(t, env))
    print("─" * 78)
    if not number:
        print(f"{C['d']}When done:  ./exam/trainer check {t.id}{C['x']}")


def cmd_check(args, env, tasks):
    t = tasks.get(args.id) or sys.exit(f"unknown task {args.id}")
    print(f"{C['b']}{t.id}: {t.title}{C['x']}")
    passed, total, score = grade(t, env)
    st = load_state()
    started = st["started"].get(t.id)
    st["history"].append({"task": t.id, "score": score, "points": t.points, "passed": passed, "total": total,
                          "at": time.time(), "seconds": round(time.time() - started) if started else None})
    save_state(st)
    if started:
        mins = (time.time() - started) / 60
        pace = C["g"] if mins <= t.minutes else C["y"]
        print(f"  {pace}time since start: {mins:.1f} min (exam pace ~{t.minutes} min){C['x']}")


def cmd_solution(args, env, tasks):
    t = tasks.get(args.id) or sys.exit(f"unknown task {args.id}")
    print(f"{C['b']}Reference solution for {t.id}{C['x']} {C['d']}(many correct answers exist; the checks only test the result){C['x']}\n")
    print(textwrap.dedent(t.solution).strip().replace("$CP", env.cp).replace("$W1", env.w1).replace("$W2", env.w2)
          .replace("$INGRESS_CLASS", env.vars()["ingress_class"]).replace("$GATEWAY_CLASS", env.vars()["gateway_class"])
          .replace("$ANSWERS", str(ANSWERS)).replace("$REPO", str(ROOT)))


def cmd_reset(args, env, tasks):
    ids = list(tasks) if args.id == "all" else [args.id]
    for i in ids:
        t = tasks[i]
        if t.cleanup.strip():
            run_script(env, t.cleanup, f"cleanup {i}", quiet=True)
    print(f"cleaned up {', '.join(ids) if len(ids) < 5 else f'{len(ids)} tasks'}")


def cmd_exam(args, env, tasks):
    st = load_state()
    if args.action in ("grade", "status", "abort"):
        ex = st.get("exam") or sys.exit("no exam running. Start one: ./exam/trainer exam ckad")
        if args.action == "status":
            left = ex["ends"] - time.time()
            print(f"{ex['track'].upper()} mock exam · {len(ex['tasks'])} questions · {max(0, left) / 60:.0f} min left")
            for n, tid in enumerate(ex["tasks"], 1):
                print(f"  Q{n:<3} {tid:9} {tasks[tid].title}")
            return
        if args.action == "abort":
            for tid in ex["tasks"]:
                run_script(env, tasks[tid].cleanup, f"cleanup {tid}", quiet=True)
            st["exam"] = None
            save_state(st)
            print("exam aborted and cleaned up")
            return
        total_pts = got = 0
        print(f"{C['b']}Grading {ex['track'].upper()} mock exam{C['x']}")
        for n, tid in enumerate(ex["tasks"], 1):
            t = tasks[tid]
            print(f"\n{C['b']}Q{n} {tid}{C['x']} {t.title}")
            _, _, score = grade(t, env)
            total_pts += t.points
            got += score
        pct = round(100 * got / total_pts) if total_pts else 0
        over = time.time() - ex["ends"]
        verdict = f"{C['g']}PASS{C['x']}" if pct >= PASS_MARK else f"{C['r']}FAIL{C['x']}"
        print(f"\n{C['b']}Score: {got}/{total_pts} = {pct}%  (pass mark {PASS_MARK}%) → {verdict}")
        if over > 0:
            print(f"{C['y']}You went {over / 60:.0f} min over time -- in the real exam, the terminal closes.{C['x']}")
        st["history"].append({"exam": ex["track"], "pct": pct, "at": time.time()})
        st["exam"] = None
        save_state(st)
        print(f"{C['d']}Clean up with: ./exam/trainer reset all{C['x']}")
        return

    track = args.action
    if track not in DOMAINS:
        sys.exit("usage: trainer exam ckad|cka|grade|status|abort")
    pool = [t for t in tasks.values() if t.track == track and available(t, env)[0] and (args.disruptive or not t.destructive)]
    picked = []
    for dom, weight in DOMAINS[track].items():
        dom_tasks = [t for t in pool if t.domain == dom]
        random.shuffle(dom_tasks)
        n = max(1, round(args.count * weight / 100))
        picked += dom_tasks[:n]
    random.shuffle(picked)
    print(f"Setting up {len(picked)} questions… (this takes a minute)")
    for t in picked:
        clear_answers(t.id)
        run_script(env, t.setup, f"setup {t.id}", quiet=True)
    ends = time.time() + args.minutes * 60
    st["exam"] = {"track": track, "tasks": [t.id for t in picked], "ends": ends, "started": time.time()}
    save_state(st)
    ANSWERS.mkdir(parents=True, exist_ok=True)
    sheet = [f"# {track.upper()} mock exam -- {len(picked)} questions, {args.minutes} minutes, pass mark {PASS_MARK}%\n",
             f"Ends at {time.strftime('%H:%M', time.localtime(ends))}. Grade with `./exam/trainer exam grade`.\n",
             "Like the real exam: skip hard questions, come back later, and always check which namespace you're in.\n"]
    for n, t in enumerate(picked, 1):
        sheet.append(f"\n## Question {n} ({t.points} points) -- {t.domain}\n\n{render(t, env)}\n")
    (ANSWERS / "EXAM.md").write_text("\n".join(sheet))
    for n, t in enumerate(picked, 1):
        print_task(t, env, number=n)
    print(f"\n{C['b']}Questions saved to {ANSWERS / 'EXAM.md'}. Timer: {args.minutes} min. Good luck!{C['x']}")


def cmd_progress(args, env, tasks):
    st = load_state()
    best = {}
    for h in st["history"]:
        if "task" in h:
            best[h["task"]] = max(best.get(h["task"], 0), h["score"] / max(h["points"], 1))
    for track in ("ckad", "cka"):
        ts = [t for t in tasks.values() if t.track == track]
        mastered = sum(1 for t in ts if best.get(t.id) == 1)
        print(f"{C['b']}{track.upper()}{C['x']}: {mastered}/{len(ts)} tasks mastered")
        for dom in DOMAINS[track]:
            d = [t for t in ts if t.domain == dom]
            m = sum(1 for t in d if best.get(t.id) == 1)
            bar = "█" * m + "░" * (len(d) - m)
            print(f"  {dom:55} {bar} {m}/{len(d)}")
    exams = [h for h in st["history"] if "exam" in h]
    if exams:
        print(f"\n{C['b']}Mock exams:{C['x']} " + ", ".join(f"{h['exam'].upper()} {h['pct']}%" for h in exams[-8:]))


def cmd_selftest(args, env, tasks):
    """For maintainers: setup -> reference solution -> check must pass, for every task runnable here."""
    ids = args.ids or sorted(tasks)
    failures = []
    for tid in ids:
        t = tasks[tid]
        ok, missing = available(t, env)
        if not ok:
            print(f"{C['d']}skip {tid} (needs {', '.join(sorted(missing))}){C['x']}")
            continue
        run_script(env, t.cleanup, "pre-cleanup", quiet=True)
        clear_answers(tid)
        if not run_script(env, t.setup, f"{tid} setup", quiet=True):
            failures.append(tid)
            continue
        time.sleep(1)
        _, _, baseline = grade(t, env, verbose=False)
        if baseline:
            print(f"{C['r']}{tid}: LEAK -- scores {baseline} points before any work was done{C['x']}")
            grade(t, env, verbose=True)
            failures.append(tid)
        if not run_script(env, t.solution, f"{tid} solution", quiet=True):
            failures.append(tid)
            continue
        time.sleep(args.settle)
        passed, total, _ = grade(t, env, verbose=False)
        print(f"{C['g'] if passed == total else C['r']}{tid}: {passed}/{total}{C['x']}")
        if passed != total:
            grade(t, env, verbose=True)
            failures.append(tid)
        if not args.keep:
            run_script(env, t.cleanup, f"{tid} cleanup", quiet=True)
    print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)


def main():
    p = argparse.ArgumentParser(prog="trainer", description="ShopFleet CKA/CKAD trainer")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("env")
    s = sub.add_parser("list"); s.add_argument("--track", choices=["cka", "ckad"])
    s = sub.add_parser("start"); s.add_argument("id"); s.add_argument("--force", action="store_true"); s.add_argument("-y", "--yes", action="store_true")
    s = sub.add_parser("check"); s.add_argument("id")
    s = sub.add_parser("solution"); s.add_argument("id")
    s = sub.add_parser("reset"); s.add_argument("id")
    s = sub.add_parser("exam"); s.add_argument("action"); s.add_argument("--minutes", type=int, default=120)
    s.add_argument("--count", type=int, default=16); s.add_argument("--disruptive", action="store_true")
    sub.add_parser("progress")
    s = sub.add_parser("selftest"); s.add_argument("ids", nargs="*"); s.add_argument("--keep", action="store_true")
    s.add_argument("--settle", type=float, default=3)
    args = p.parse_args()
    if not shutil.which("kubectl"):
        sys.exit("kubectl not found on PATH")
    env = Env()
    tasks = load_tasks()
    {"env": cmd_env, "list": cmd_list, "start": cmd_start, "check": cmd_check, "solution": cmd_solution,
     "reset": cmd_reset, "exam": cmd_exam, "progress": cmd_progress, "selftest": cmd_selftest}[args.cmd](args, env, tasks)


if __name__ == "__main__":
    main()
