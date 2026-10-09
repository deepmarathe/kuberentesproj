output "cluster_name" {
  value = google_container_cluster.this.name
}

output "cluster_location" {
  value = google_container_cluster.this.location
}

output "get_credentials" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --zone ${var.zone} --project ${var.project_id}"
}

output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "ingress_ip" {
  value = google_compute_global_address.ingress.address
}

output "reports_bucket" {
  value = google_storage_bucket.reports.name
}

output "cloudbuild_service_account" {
  value = google_service_account.cloudbuild.email
}
