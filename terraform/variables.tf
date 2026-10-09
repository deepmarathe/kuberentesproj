variable "project_id" {
  description = "Your GCP project ID"
  type        = string
}

variable "region" {
  description = "Region for the VPC subnet and Artifact Registry"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone for the (zonal) GKE cluster. Zonal = cheaper; the GKE free tier covers one zonal cluster's management fee."
  type        = string
  default     = "us-central1-a"
}

variable "cluster_name" {
  type    = string
  default = "shopfleet"
}

variable "general_machine_type" {
  type    = string
  default = "e2-standard-2"
}

variable "general_min_nodes" {
  type    = number
  default = 1
}

variable "general_max_nodes" {
  type    = number
  default = 4
}

variable "spot_machine_type" {
  type    = string
  default = "e2-standard-2"
}

variable "spot_max_nodes" {
  type    = number
  default = 3
}

variable "alert_email" {
  description = "Email for alert notifications. Leave empty to create alert policies without a notification channel."
  type        = string
  default     = ""
}

variable "master_authorized_cidr" {
  description = "CIDR allowed to reach the Kubernetes API. 0.0.0.0/0 keeps Cloud Shell/Cloud Build working; tighten it in lab 13."
  type        = string
  default     = "0.0.0.0/0"
}
