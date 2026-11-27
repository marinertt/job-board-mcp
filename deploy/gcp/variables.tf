variable "project_id" {
  description = "Existing GCP project ID with billing enabled."
  type        = string
}

variable "region" {
  description = "Cloud Run region. Choose a region near the operator and Firestore."
  type        = string
  default     = "us-east1"
}

variable "firestore_location" {
  description = "Firestore database location, selected once at creation."
  type        = string
  default     = "nam5"
}

variable "container_image" {
  description = "Immutable Artifact Registry image URL, preferably pinned by digest."
  type        = string
}

variable "max_instance_count" {
  description = "Hard cap on concurrent Cloud Run instances for this demo."
  type        = number
  default     = 1

  validation {
    condition     = var.max_instance_count >= 1 && var.max_instance_count <= 3
    error_message = "Use between 1 and 3 instances for the demo deployment."
  }
}

variable "invoker_members" {
  description = "IAM member strings allowed to call Cloud Run, e.g. user:you@example.com. Do not use allUsers."
  type        = list(string)
  default     = []
}

variable "secret_env" {
  description = "Optional map of Cloud Run env var names to pre-created Secret Manager secret IDs and numeric versions. Secret payloads stay outside Terraform."
  type = map(object({
    secret_id = string
    version   = string
  }))
  default = {}
}
