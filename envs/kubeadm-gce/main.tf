# =============================================================================
# CKA/CKAD practice cluster on plain VMs -- the same shape as the exam:
#   cp1 (control plane) + node01 + node02, Ubuntu, containerd, kubeadm, Calico.
#   SSH in:  ./envs/kubeadm-gce/ssh.sh        (lands on cp1 as user "student")
#   From cp1: ssh node01 / ssh node02         (passwordless, like the exam)
# =============================================================================
locals {
  nodes = {
    cp1    = { ip = "10.240.0.10", role = "control-plane", type = var.cp_machine_type }
    node01 = { ip = "10.240.0.21", role = "worker", type = var.worker_machine_type }
    node02 = { ip = "10.240.0.22", role = "worker", type = var.worker_machine_type }
  }
  hosts = join("\n", [for n, v in local.nodes : "${v.ip} ${n}"])
}

resource "google_project_service" "apis" {
  for_each           = toset(["compute.googleapis.com", "artifactregistry.googleapis.com", "cloudbuild.googleapis.com", "iap.googleapis.com", "iam.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "vpc" {
  name                    = "cka-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "nodes" {
  name          = "cka-nodes"
  network       = google_compute_network.vpc.id
  region        = var.region
  ip_cidr_range = "10.240.0.0/24"
}

# Node <-> node: everything (API server, kubelet, etcd, Calico VXLAN on UDP 4789, pod traffic).
resource "google_compute_firewall" "internal" {
  name          = "cka-allow-internal"
  network       = google_compute_network.vpc.id
  source_ranges = ["10.240.0.0/24", "192.168.0.0/16"]
  allow { protocol = "all" }
}

# SSH only through Identity-Aware Proxy (no SSH open to the internet).
resource "google_compute_firewall" "iap_ssh" {
  name          = "cka-allow-iap-ssh"
  network       = google_compute_network.vpc.id
  source_ranges = ["35.235.240.0/20"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "public" {
  name          = "cka-allow-nodeports-api"
  network       = google_compute_network.vpc.id
  source_ranges = [var.allowed_cidr]
  allow {
    protocol = "tcp"
    ports    = ["6443", "30000-32767"]
  }
}

# Same bootstrap token on both sides: kubeadm init --token X / kubeadm join --token X.
resource "random_string" "token_id" {
  length  = 6
  special = false
  upper   = false
}
resource "random_string" "token_secret" {
  length  = 16
  special = false
  upper   = false
}

# Key pair so that student@cp1 can `ssh node01` like in the exam.
resource "tls_private_key" "student" {
  algorithm = "ED25519"
}

resource "google_compute_instance" "node" {
  for_each     = local.nodes
  name         = each.key
  machine_type = each.value.type
  zone         = var.zone
  tags         = ["cka"]

  can_ip_forward = true # pod traffic leaves nodes with pod IPs as source

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 40
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.nodes.id
    network_ip = each.value.ip
    access_config {} # ephemeral public IP (apt/registry downloads; NodePort access)
  }

  scheduling {
    provisioning_model          = var.spot ? "SPOT" : "STANDARD"
    preemptible                 = var.spot
    automatic_restart           = !var.spot
    instance_termination_action = var.spot ? "STOP" : null
  }

  metadata = {
    enable-oslogin = "TRUE"
    student-key    = base64encode(tls_private_key.student.private_key_openssh)
    student-pub    = tls_private_key.student.public_key_openssh
    startup-script = templatefile("${path.module}/node-init.sh.tpl", {
      node_name       = each.key
      role            = each.value.role
      hosts           = local.hosts
      k8s_minor       = var.k8s_minor
      bootstrap       = var.bootstrap
      token           = "${random_string.token_id.result}.${random_string.token_secret.result}"
      platform_script = file("${path.module}/../common/platform.sh")
    })
  }

  service_account {
    scopes = ["cloud-platform"]
  }

  depends_on = [google_compute_firewall.internal]
}

resource "google_artifact_registry_repository" "images" {
  count         = var.create_registry ? 1 : 0
  location      = var.region
  repository_id = "shopfleet"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# Robot account the cluster uses to PULL images (via an imagePullSecret -- plain kubeadm
# clusters have no built-in Google credentials like GKE nodes do).
resource "google_service_account" "puller" {
  account_id   = "cka-image-puller"
  display_name = "CKA lab image puller"
}

resource "google_project_iam_member" "puller" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.puller.email}"
}

# Cloud Build identity (shared name with the GKE stack's pipeline) -- only builds/pushes here.
resource "google_service_account" "cloudbuild" {
  count        = var.create_registry ? 1 : 0
  account_id   = "shopfleet-cloudbuild"
  display_name = "ShopFleet Cloud Build"
}

resource "google_project_iam_member" "cloudbuild" {
  for_each = var.create_registry ? toset(["roles/artifactregistry.writer", "roles/logging.logWriter", "roles/storage.objectViewer"]) : toset([])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.cloudbuild[0].email}"
}
