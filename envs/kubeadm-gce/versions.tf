terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = ">= 6.0, < 8.0" }
    random = { source = "hashicorp/random", version = ">= 3.5" }
    tls    = { source = "hashicorp/tls", version = ">= 4.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
