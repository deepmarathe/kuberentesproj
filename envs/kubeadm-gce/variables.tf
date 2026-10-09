variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "k8s_minor" {
  description = "Kubernetes minor version to install (e.g. 1.34). Pick one BELOW the CKA's current version so you can practise upgrading."
  type        = string
  default     = "1.34"
}

variable "bootstrap" {
  description = "auto = kubeadm init/join + CNI + add-ons done for you. manual = only prerequisites (practise the install yourself: docs/cka-ckad/labs/01-kubeadm-install.md)."
  type        = string
  default     = "auto"
  validation {
    condition     = contains(["auto", "manual"], var.bootstrap)
    error_message = "bootstrap must be auto or manual"
  }
}

variable "cp_machine_type" {
  type    = string
  default = "e2-standard-2" # kubeadm requires >= 2 CPUs on the control plane
}
variable "worker_machine_type" {
  type    = string
  default = "e2-standard-2"
}
variable "spot" {
  description = "Spot VMs: ~70% cheaper, but Google may stop them at any time (they restart with 'gcloud compute instances start')."
  type        = bool
  default     = false
}
variable "allowed_cidr" {
  description = "Who may reach NodePorts (the store on :30080) and the API server (:6443). Tighten to your IP/32."
  type        = string
  default     = "0.0.0.0/0"
}
variable "create_registry" {
  description = "Create the 'shopfleet' Artifact Registry repo. Set false if the GKE stack (terraform/) already created it."
  type        = bool
  default     = true
}
