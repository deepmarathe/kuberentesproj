# Lab 03 – Payment provider outage

**Scenario:** Your third-party payment provider is having a bad day: first errors, then slowness, then hangs. Keep checkout as healthy as possible, and know when to page someone.

ShopFleet's `payment` service plays the provider. Its behaviour comes from the **`payment-chaos` ConfigMap, mounted as a file** and re-read every 5s. Changing it does *not* restart pods.

## 1. Baseline

PromQL (Monitoring → Metrics Explorer → PromQL):

```promql
sum by (result)  (rate(orders_checkout_total[2m]))        # mostly "paid", a few "payment_declined"
sum by (outcome) (rate(orders_payment_calls_total[2m]))
max(orders_payment_circuit_open)                          # 0
```

```bash
kubectl -n shopfleet get pods -l app=payment       # note AGE and RESTARTS
kubectl -n shopfleet get configmap payment-chaos -o yaml
```

## 2. 50% errors: do retries help?

```bash
./scripts/chaos.sh payment-errors 0.5
kubectl -n shopfleet logs -l app=payment -f | grep reloaded      # appears within ~30-90s
```

**Observe:**
- `orders_payment_calls_total{outcome="http_503"}` rises, but most checkouts still succeed.
- Orders retries up to 2× with exponential backoff, so P(all 3 fail) = 0.5³ = 12.5%.
- The pods were **not restarted**: AGE is unchanged. That's the file-mounted ConfigMap at work.

## 3. 100% errors: the circuit breaker

```bash
./scripts/chaos.sh payment-errors 1.0
```

**Observe:**
- `orders_payment_circuit_open` goes to **1**.
- Checkout now fails **fast** (~20ms instead of ~1s of retries) with `payment_unavailable`. Try it in the store.
- The breaker lets one trial request through every 15s (half-open).
- After ~2 minutes, the **"checkout failure rate > 10%"** alert fires: Monitoring → Alerting. You get an email if you set `alert_email`.
- Stock is **released** for each failed order (saga compensation): check product stock in the UI.

Why fail fast? Without a breaker, every checkout would wait for timeouts and retries. That ties up threads and connections, adds 3× load on an already-failing provider, and can cascade into a full outage.

## 4. Slowness and hangs: timeouts

```bash
./scripts/chaos.sh payment-slow 2500     # 2.5s ± 0.5s per charge; orders' timeout is 3s
# then
./scripts/chaos.sh payment-hang 0.3      # 30% of requests hang for 30s
```

**Observe:**
- p95 checkout latency climbs: `histogram_quantile(0.95, sum by (le) (rate(orders_checkout_duration_seconds_bucket[2m])))`
- `orders_payment_calls_total{outcome="timeout"}` appears.
- In payment's metrics, `payment_charges_total{outcome="client_cancelled"}` counts requests where the caller gave up. Payment stops working on them too, because it watches the request context.

**Timeout budget**: each layer must time out *before* the layer above it, or the user sees a generic error:

```
browser  ←  LB (30s, BackendConfig)  ←  nginx (15s)  ←  orders → payment (3s × up to 3 tries + backoff ≈ 10s)
```

## 5. Recover

```bash
./scripts/chaos.sh payment-reset
```

The breaker closes on the next successful trial, and the alert auto-resolves a few minutes later.

## Why it works
- **Retries** handle *transient* failures. **Idempotency keys** (`Idempotency-Key: <order_id>`) make retries safe: payment returns the original result instead of charging twice.
- **Circuit breakers** handle *sustained* failures by failing fast and shedding load from the struggling dependency.
- **Timeouts** bound how long a failure can hold resources.
- **ConfigMap as a mounted volume** → kubelet refreshes the file (eventually consistent, typically < 1–2 min). **ConfigMap as env vars** → read once at container start, so a restart is needed. `subPath` mounts never update.

## Exam takeaways
- Know both ways to consume a ConfigMap and which one updates live.
- Alerting on *symptoms* (checkout failure ratio) beats alerting on *causes* (payment pod CPU).
- `kubectl create configmap X --from-literal=... --dry-run=client -o yaml | kubectl apply -f -` is the idiom to update a ConfigMap from the CLI.

<details><summary>Check yourself</summary>

1. *Payment returns 402 (declined). Should orders retry?* No. A decline is a valid business answer. Retrying would hammer the provider and confuse the user. (Look at `_charge` in `services/orders/app/main.py`.)
2. *Why does orders count a decline as a breaker "success"?* The provider responded correctly, so it's healthy.
3. *You change the ConfigMap but pods don't pick it up after 5 minutes. Causes?* The app reads env vars rather than a file, the mount uses `subPath`, or you edited a different (hash-suffixed) ConfigMap than the one mounted.
</details>
