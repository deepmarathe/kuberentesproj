# 03 – Exam technique

## Shell setup (identical to what you'll type in the first minute of the exam)

The kubeadm environment's `cp1` already has this. Elsewhere, run `source exam/setup-shell.sh`.

```bash
alias k=kubectl
source <(kubectl completion bash)
complete -o default -F __start_kubectl k
export do="--dry-run=client -o yaml"        # k create deploy web --image=nginx $do > web.yaml
export now="--force --grace-period 0"       # k delete pod x $now
```

`~/.vimrc`, so pasted YAML doesn't turn into a staircase:

```
set expandtab tabstop=2 shiftwidth=2 number
```

In vim, `:set paste` before pasting from the docs and `:set nopaste` after. `>` and `<` indent a visual selection.

## The golden rules

1. **Read the context line first.** Every task starts with something like `kubectl config use-context k8s-c2`. Running a correct answer in the wrong cluster earns 0 points. The same goes for **namespaces**: check `-n` on every command.
2. **Generate YAML, don't write it.** `k create … $do > file.yaml`, then edit only what's missing.
3. **Know `kubectl explain` cold:** `k explain pod.spec.containers.securityContext --recursive | less`
4. **Do the easy points first.** Tasks are weighted (2–13%). Flag anything taking more than 1.5× its weight in minutes, skip it, and come back.
5. **Verify every task** (`get`, `describe`, `logs`) before moving on. The trainer's `check` builds that habit.
6. **On nodes:** `ssh node01`, then `sudo -i`, and **exit back** to the main terminal before the next task. Edits on the wrong host are a classic way to lose points.

## Imperative commands worth memorising

```bash
k run tmp --image=busybox:1.36 --rm -it --restart=Never -- sh          # throwaway debug pod
k create deploy web --image=nginx:1.27 --replicas=3 $do
k expose deploy web --port=80 --target-port=8080 --type=NodePort $do
k create cm cfg --from-literal=A=1 --from-file=app.conf $do
k create secret generic s --from-literal=pw=x $do
k create job j --image=busybox -- echo hi
k create cronjob c --image=busybox --schedule="*/5 * * * *" -- date
k create job j2 --from=cronjob/c
k create sa app; k create role r --verb=get,list --resource=pods; k create rolebinding rb --role=r --serviceaccount=ns:app
k create clusterrole cr --verb=get,list --resource=nodes; k create clusterrolebinding crb --clusterrole=cr --user=jane
k create quota q --hard=pods=10,requests.cpu=2 ; k create priorityclass high --value=1000
k create ingress web --class=nginx --rule="host/path*=svc:80"
k autoscale deploy web --min=2 --max=5 --cpu-percent=60
k set image deploy/web nginx=nginx:1.27 ; k set resources deploy/web --limits=memory=256Mi ; k set env deploy/web A=1
k rollout status|history|undo|restart deploy/web
k label / annotate / taint node node01 key=val:NoSchedule ; k taint node node01 key-
k auth can-i list pods --as=system:serviceaccount:ns:app -n ns
k get pods -o wide --show-labels -l app=web --sort-by=.metadata.creationTimestamp
k get pod x -o jsonpath='{.status.podIP}' ; k get nodes -o custom-columns=N:.metadata.name,V:.status.nodeInfo.kubeletVersion
k top pods --sort-by=memory -A
k drain node01 --ignore-daemonsets --delete-emptydir-data ; k uncordon node01
```

## Node-level checklist (CKA)

```bash
ssh node01 ; sudo -i
systemctl status kubelet ; journalctl -u kubelet -n 50 --no-pager
cat /var/lib/kubelet/config.yaml            # staticPodPath, cgroupDriver, clusterDNS
ls /etc/kubernetes/manifests/               # static pods (control plane lives here on cp1)
crictl ps -a ; crictl logs <id> ; crictl pods
cat /etc/kubernetes/kubelet.conf            # kubelet's kubeconfig
kubeadm certs check-expiration
```

## Docs pages to know where to find (practise navigating to them)

- Concepts → Workloads → Pods → *Init Containers*, *Sidecar Containers*
- Tasks → Configure Pods and Containers → *Configure Liveness, Readiness and Startup Probes*, *Security Context*, *Assign Pods to Nodes using Node Affinity*
- Tasks → Administer a Cluster → *Operating etcd clusters for Kubernetes* (backup and restore commands), *kubeadm → Upgrading kubeadm clusters*
- Concepts → Services, Load Balancing → *Network Policies*, *Gateway API*, *Ingress*
- Reference → *kubectl Quick Reference* (the cheat sheet: bookmark it)
