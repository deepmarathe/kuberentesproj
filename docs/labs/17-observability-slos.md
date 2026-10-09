# Lab 17 – Observability: dashboards, log-based metrics, SLOs

**Scenario:** Leadership asks, "How reliable is checkout, and how do we know before customers tell us?"

## 1. Build an operations dashboard

Monitoring → Dashboards → Create dashboard → add widgets. For each, choose **PromQL**:

| Widget | Query |
|---|---|
| Requests/s by service | `sum by (service) (rate(http_requests_total[5m]))` |
| 5xx ratio by service | `sum by (service)(rate(http_requests_total{status=~"5.."}[5m])) / sum by (service)(rate(http_requests_total[5m]))` |
| p95 latency by route | `histogram_quantile(0.95, sum by (le, route)(rate(http_request_duration_seconds_bucket[5m])))` |
| Checkout outcomes | `sum by (result)(rate(orders_checkout_total[5m]))` |
| Revenue $/min | `sum(rate(orders_revenue_cents_total[5m])) * 60 / 100` |
| Payment breaker | `max(orders_payment_circuit_open)` |
| Fulfillment lag | `max(worker_stream_lag)` |
| Pod restarts (1h) | `sum by (container_name)(increase(kubernetes_io:container_restart_count{namespace_name="shopfleet"}[1h]))` |

Then run a chaos scenario (lab 03 or 05) and watch the dashboard tell the story.

## 2. Log-based metric

Count failed checkouts from logs alone. This is useful for apps without Prometheus metrics:

```bash
gcloud logging metrics create shopfleet_checkout_failures \
  --description="Checkouts that did not end in paid" \
  --log-filter='resource.type="k8s_container" resource.labels.namespace_name="shopfleet" jsonPayload.message="checkout" jsonPayload.result!="paid"'
```

After a few minutes it appears in Metrics Explorer as `logging.googleapis.com/user/shopfleet_checkout_failures`. Alert on it like any other metric.

## 3. Define an SLO

**SLI** (what we measure): the proportion of checkout attempts that didn't fail for *technical* reasons. **SLO** (target): 99% over 7 days. **Error budget**: 1%, so about 1.7 hours of full outage per week.

Monitoring → **SLOs** (or Services) → Define service → *Custom service* named "checkout" → Create SLO:
- Metric: *Request-based*, *Good/total ratio*
- Good: `prometheus.googleapis.com/orders_checkout_total/counter` with `result` = one of `paid`, `payment_declined`, `out_of_stock`, `empty_cart`
- Total: the same metric with no filter
- Compliance period: rolling 7 days, goal 99%

Then add an **SLO alert on burn rate**, e.g. "budget burning 10× faster than sustainable, over 1 hour." It catches serious incidents fast without paging on every blip.

Test it: `./scripts/chaos.sh payment-errors 1.0` for 10–15 minutes, watch the budget drop, then `payment-reset`.

## 4. Uptime check

Terraform created **"ShopFleet storefront"**: it probes `/api/catalog/categories` on your IP from several regions every minute. Monitoring → Uptime checks. Scale catalog to 0 for 3 minutes and watch it fail and alert:

```bash
kubectl -n shopfleet scale deploy/catalog --replicas=0 ; sleep 240 ; ./scripts/deploy.sh
```

(The HPA's `minReplicas: 2` will try to bring it back. Delete the HPA first if you want a sustained outage.)

## 5. Stretch: distributed tracing

ShopFleet doesn't emit traces yet. Instrument `orders` with OpenTelemetry (`opentelemetry-instrumentation-fastapi`, `-httpx`) and export to Cloud Trace. You'll see one checkout span fan out into cart, catalog, and payment calls, with the retry delays clearly visible.

## Exam takeaways
- Cloud Logging: logs from GKE containers arrive automatically. JSON stdout becomes `jsonPayload`. Use sinks to export to BigQuery, GCS, or Pub/Sub for retention or analysis. Admin Activity audit logs are always on and free.
- Cloud Monitoring: system metrics (`kubernetes.io/...`) come for free. Managed Prometheus stores app metrics as `prometheus.googleapis.com/...` and you query them with PromQL.
- Alert on symptoms and SLO burn rate, not on every cause.
