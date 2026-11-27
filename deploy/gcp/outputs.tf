output "service_name" {
  value = google_cloud_run_v2_service.job_monitor.name
}

output "service_url" {
  description = "Private Cloud Run URL; invocation still requires Cloud Run IAM credentials."
  value       = google_cloud_run_v2_service.job_monitor.uri
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}
