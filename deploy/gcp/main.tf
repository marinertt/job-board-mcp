terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "required" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_firestore_database" "job_monitor" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.required]

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "job-monitor-runtime"
  display_name = "Job Monitor Cloud Run runtime"
}

resource "google_project_iam_member" "firestore_access" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_secret_access" {
  for_each  = var.secret_env
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_service" "job_monitor" {
  project             = var.project_id
  name                = "job-monitor-mcp"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = false
  deletion_protection = true

  template {
    service_account                  = google_service_account.runtime.email
    max_instance_request_concurrency = 4
    timeout                          = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instance_count
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "JOB_MONITOR_STORE"
        value = "firestore"
      }
      env {
        name  = "JOB_MONITOR_FIRESTORE_COLLECTION"
        value = "job_monitor_jobs"
      }
      env {
        name  = "HOST"
        value = "0.0.0.0"
      }
      dynamic "env" {
        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = env.value.version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_firestore_database.job_monitor,
    google_project_iam_member.firestore_access,
    google_secret_manager_secret_iam_member.runtime_secret_access,
  ]
}

# No allUsers binding is created. Only listed operator identities may invoke.
resource "google_cloud_run_v2_service_iam_member" "operator_invoker" {
  for_each = toset(var.invoker_members)
  project  = var.project_id
  location = google_cloud_run_v2_service.job_monitor.location
  name     = google_cloud_run_v2_service.job_monitor.name
  role     = "roles/run.invoker"
  member   = each.value
}
