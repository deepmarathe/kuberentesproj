# Lab 14 – HTTPS, Gateway API, and Cloud CDN

## Part A – HTTPS with a Google-managed certificate

Google-managed certificates need a domain whose DNS points at your load balancer IP. Options:
- **Your own domain** (most reliable): add an `A` record for `shop.yourdomain.com` → `make ip`.
- **nip.io** (free, no setup): `34.120.1.2.nip.io` resolves to `34.120.1.2`. It's widely used, so certificate issuance can occasionally hit CA rate limits. If it stays in `FailedNotVisible` or similar for over an hour, use a real domain.

```bash
IP=$(gcloud compute addresses describe shopfleet-ip --global --format='value(address)')
DOMAIN="$IP.nip.io"                  # or shop.yourdomain.com
sed "s|__DOMAIN__|$DOMAIN|" k8s/extras/https.yaml | kubectl apply -f -
kubectl -n shopfleet annotate ingress shopfleet --overwrite \
  networking.gke.io/managed-certificates=shopfleet-cert \
  networking.gke.io/v1beta1.FrontendConfig=shopfleet
kubectl -n shopfleet describe managedcertificate shopfleet-cert     # Provisioning → Active (15-60 min)
```

When it's `Active`, `https://$DOMAIN` works, and `http://` redirects to HTTPS (the FrontendConfig).

Things to notice:
- The LB now has *two* forwarding rules: `gcloud compute forwarding-rules list`.
- Certificates renew automatically.
- Update the uptime check in `terraform/monitoring.tf` to use HTTPS with your domain.

## Part B – Gateway API (the successor to Ingress)

The Gateway API splits responsibilities:
- **GatewayClass** = the platform team's choice of load balancer
- **Gateway** = the entry point
- **HTTPRoute** = app teams' routing rules

It supports what Ingress can't express portably: weighted traffic splits, header matching, and cross-namespace routing.

```bash
kubectl get gatewayclass                         # gke-l7-global-external-managed, gke-l7-rilb, ...
kubectl apply -f k8s/extras/gateway.yaml
kubectl -n shopfleet get gateway shopfleet -w    # wait for an ADDRESS (a new LB, ~5 min)
GW=$(kubectl -n shopfleet get gateway shopfleet -o jsonpath='{.status.addresses[0].value}')
curl -s http://$GW/api/catalog/categories
```

**Precise canary with weights:**
1. Deploy the canary (lab 04, step 4).
2. Edit `k8s/extras/gateway.yaml`: uncomment the `catalog-canary` backendRef and set weights to 90/10.
3. `kubectl apply -f k8s/extras/gateway.yaml`, then:

```bash
for i in $(seq 1 50); do curl -s -o /dev/null -D - http://$GW/api/catalog/categories | grep -i x-service; done | sort | uniq -c
```

About 10% show `catalog@canary`. Shift to 50/50, then 0/100, and you've done a progressive delivery by hand. That's what Cloud Deploy / Argo Rollouts automate.

Clean up (it's a second load balancer, about $0.60/day): `kubectl delete -f k8s/extras/gateway.yaml`

## Part C – Cloud CDN

Cloud CDN caches responses at Google's edge. It's enabled per backend through BackendConfig.

1. In `k8s/overlays/gke/backendconfigs.yaml` set `cdn.enabled: true` for `frontend`, then run `./scripts/deploy.sh`.
2. `curl -sI http://$(make -s ip | sed 's|http://||')/app.js`: no caching yet. Our nginx sends `Cache-Control: no-cache`, and **the origin controls the CDN**.
3. Make static assets cacheable. In `services/frontend/nginx.conf` add:
   ```nginx
   location ~* \.(js|css)$ { add_header Cache-Control "public, max-age=300"; }
   ```
   Then rebuild and deploy. Repeat the curl a few times and look for an `Age:` header, which means the response was served from the edge.
4. Invalidate after a release: `gcloud compute url-maps invalidate-cdn-cache URL_MAP --path "/app.js"`. Find the map with `gcloud compute url-maps list`.

## Exam takeaways
- GKE Ingress → *global external Application LB*. Internal LB: annotation `kubernetes.io/ingress.class: gce-internal`, or Gateway class `gke-l7-rilb`.
- `Service type: LoadBalancer` → passthrough *Network LB* (L4). Ingress/Gateway → L7 (HTTP routing, TLS, CDN, Cloud Armor).
- BackendConfig: health checks, timeouts, CDN, IAP, Cloud Armor (`securityPolicy`), logging. FrontendConfig: HTTPS redirect, SSL policies.
- Container-native load balancing (NEGs) sends LB traffic **straight to pod IPs**, skipping the node hop. It requires a VPC-native cluster.
