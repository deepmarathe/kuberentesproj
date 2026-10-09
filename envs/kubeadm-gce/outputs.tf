output "public_ips" {
  value = { for n, i in google_compute_instance.node : n => i.network_interface[0].access_config[0].nat_ip }
}
output "store_url" {
  value = "http://${google_compute_instance.node["node01"].network_interface[0].access_config[0].nat_ip}:30080"
}
output "ssh" {
  value = "./envs/kubeadm-gce/ssh.sh          (or: gcloud compute ssh cp1 --zone ${var.zone} --tunnel-through-iap)"
}
output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/shopfleet"
}
output "puller_sa" {
  value = google_service_account.puller.email
}
