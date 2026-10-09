# =============================================================================
# ShopFleet infrastructure
#   VPC (+ secondary ranges for pods/services) -> Cloud NAT (private nodes need egress)
#   GKE Standard cluster: private nodes, Workload Identity, Dataplane V2, Managed Prometheus
#   Node pools: "general" (on-demand) + "spot" (cheap, preemptible, tainted)
#   Artifact Registry, GCS bucket for reports, IAM for nodes / workloads / CI
# =============================================================================

locals {
  services = [
    "container.googleapis.com",
    "compute.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# ----------------------------------------------------------------------------- network
resource "google_compute_network" "vpc" {
  name                    = "shopfleet-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "gke" {
  name                     = "shopfleet-gke"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.10.0.0/20" # nodes
  private_ip_google_access = true           # reach Google APIs without public IPs

  # VPC-native cluster: pods and services get real VPC IPs from these ranges.
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

# Private nodes have no public IP; Cloud NAT lets them pull images from Docker Hub etc.
resource "google_compute_router" "router" {
  name    = "shopfleet-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "shopfleet-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Static IP for the Ingress, so the address survives Ingress re-creation.
resource "google_compute_global_address" "ingress" {
  name       = "shopfleet-ip"
  depends_on = [google_project_service.apis]
}

# ----------------------------------------------------------------------------- IAM: node SA
# Nodes get a minimal custom SA instead of the over-privileged Compute default SA.
resource "google_service_account" "nodes" {
  account_id   = "shopfleet-nodes"
  display_name = "ShopFleet GKE nodes"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/autoscaling.metricsWriter",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

# ----------------------------------------------------------------------------- GKE cluster
resource "google_container_cluster" "this" {
  name     = var.cluster_name
  location = var.zone

  # We manage node pools separately (best practice: lets you replace pools without recreating the cluster).
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false # so `terraform destroy` works in a lab. Set true in production!

  network         = google_compute_network.vpc.id
  subnetwork      = google_compute_subnetwork.gke.id
  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # control plane keeps a public endpoint (restricted below)
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = var.master_authorized_cidr
      display_name = "allowed"
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  datapath_provider = "ADVANCED_DATAPATH" # Dataplane V2 (eBPF/Cilium): enforces NetworkPolicy, adds policy logging

  addons_config {
    http_load_balancing { disabled = false } # GKE Ingress controller
    horizontal_pod_autoscaling { disabled = false }
    gce_persistent_disk_csi_driver_config { enabled = true }
  }

  vertical_pod_autoscaling { enabled = true } # VPA recommendations (lab 08)

  gateway_api_config {
    channel = "CHANNEL_STANDARD" # Gateway API, the successor to Ingress (lab 14)
  }

  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
    managed_prometheus { enabled = true }
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  # Auto-upgrades happen only in this window: weekends, 02:00-10:00 UTC.
  # (GKE requires >= 48h of maintenance availability per 32 days.)
  maintenance_policy {
    recurring_window {
      start_time = "2025-01-04T02:00:00Z"
      end_time   = "2025-01-04T10:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SA,SU"
    }
  }

  depends_on = [google_project_service.apis, google_compute_router_nat.nat]
}

resource "google_container_node_pool" "general" {
  name     = "general"
  cluster  = google_container_cluster.this.id
  location = var.zone

  initial_node_count = 2
  autoscaling {
    min_node_count = var.general_min_nodes
    max_node_count = var.general_max_nodes
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1 # add one new node before draining an old one
    max_unavailable = 0
  }

  node_config {
    machine_type    = var.general_machine_type
    disk_size_gb    = 50
    disk_type       = "pd-balanced"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = { pool = "general" }

    workload_metadata_config {
      mode = "GKE_METADATA" # required for Workload Identity
    }
    shielded_instance_config {
      enable_secure_boot = true
    }
  }

  lifecycle {
    ignore_changes = [initial_node_count]
  }
  depends_on = [google_project_iam_member.nodes]
}

resource "google_container_node_pool" "spot" {
  name     = "spot"
  cluster  = google_container_cluster.this.id
  location = var.zone

  initial_node_count = 1
  autoscaling {
    min_node_count = 0
    max_node_count = var.spot_max_nodes
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.spot_machine_type
    spot            = true # ~60-90% cheaper, can be reclaimed by Google at any time
    disk_size_gb    = 50
    disk_type       = "pd-balanced"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = { pool = "spot" }

    # Only pods that explicitly tolerate this land here (worker, loadgen).
    taint {
      key    = "cloud.google.com/gke-spot"
      value  = "true"
      effect = "NO_SCHEDULE"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    shielded_instance_config {
      enable_secure_boot = true
    }
  }

  lifecycle {
    ignore_changes = [initial_node_count]
  }
  depends_on = [google_project_iam_member.nodes]
}

# ----------------------------------------------------------------------------- Artifact Registry
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "shopfleet"
  format        = "DOCKER"
  description   = "ShopFleet container images"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 15
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }
  depends_on = [google_project_service.apis]
}

# ----------------------------------------------------------------------------- Workload Identity for reports
resource "google_storage_bucket" "reports" {
  name                        = "${var.project_id}-shopfleet-reports"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  lifecycle_rule {
    condition { age = 30 }
    action { type = "Delete" }
  }
}

resource "google_service_account" "reports" {
  account_id   = "shopfleet-reports"
  display_name = "ShopFleet daily-report CronJob"
}

resource "google_storage_bucket_iam_member" "reports_writer" {
  bucket = google_storage_bucket.reports.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.reports.email}"
}

# The magic link: KSA shopfleet/reports may impersonate this GSA.
resource "google_service_account_iam_member" "reports_wi" {
  service_account_id = google_service_account.reports.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[shopfleet/reports]"
  depends_on         = [google_container_cluster.this]
}

# ----------------------------------------------------------------------------- CI/CD identity
resource "google_service_account" "cloudbuild" {
  account_id   = "shopfleet-cloudbuild"
  display_name = "ShopFleet Cloud Build deployer"
}

resource "google_project_iam_member" "cloudbuild" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/container.developer",
    "roles/logging.logWriter",
    "roles/storage.objectViewer", # read the uploaded source tarball
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.cloudbuild.email}"
}
