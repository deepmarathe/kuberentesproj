# =============================================================================
# Alerting: an uptime check on the public IP + PromQL alerts on app metrics.
# These fire during the chaos labs -- that's the point.
# =============================================================================

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "ShopFleet on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
  depends_on   = [google_project_service.apis]
}

locals {
  channels = google_monitoring_notification_channel.email[*].id
}

resource "google_monitoring_uptime_check_config" "storefront" {
  display_name = "ShopFleet storefront"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/api/catalog/categories"
    port         = 80
    use_ssl      = false
    validate_ssl = false
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = google_compute_global_address.ingress.address
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "uptime" {
  display_name          = "ShopFleet storefront is down"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.storefront.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
  documentation {
    content   = "Storefront failing uptime checks. Runbook: docs/02-day2-operations.md#storefront-down"
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "checkout_errors" {
  display_name          = "ShopFleet checkout failure rate > 10%"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "checkout failures"
    condition_prometheus_query_language {
      query               = <<-EOT
        sum(rate(orders_checkout_total{result!~"paid|payment_declined|out_of_stock|empty_cart"}[5m]))
        /
        clamp_min(sum(rate(orders_checkout_total[5m])), 0.001) > 0.10
      EOT
      duration            = "120s"
      evaluation_interval = "60s"
    }
  }
  documentation {
    content   = "Checkouts are failing for technical reasons. Check payment chaos config, the circuit breaker gauge (orders_payment_circuit_open), and catalog/cart health. Runbook: docs/labs/03-payment-outage.md"
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "crashloop" {
  display_name          = "ShopFleet container restarts"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "restarts in last 10m"
    condition_prometheus_query_language {
      query               = "sum by (container) (increase(kubernetes_io:container_restart_count{monitored_resource=\"k8s_container\",namespace_name=\"shopfleet\"}[10m])) > 2"
      duration            = "60s"
      evaluation_interval = "60s"
    }
  }
  documentation {
    content   = "Containers are restarting (OOMKilled? failing liveness? crash?). `kubectl -n shopfleet get pods` then `kubectl describe pod` and `kubectl logs --previous`. Runbook: docs/labs/05-oomkilled.md"
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "worker_backlog" {
  display_name          = "ShopFleet fulfillment backlog growing"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "stream lag > 50"
    condition_prometheus_query_language {
      query               = "max(worker_stream_lag) > 50"
      duration            = "300s"
      evaluation_interval = "60s"
    }
  }
  documentation {
    content   = "Orders are paid but not being fulfilled. Is the worker running? Scaled to 0? Spot node reclaimed? Runbook: docs/labs/07-worker-backlog.md"
    mime_type = "text/markdown"
  }
}
