# GCP architecture for a bounded job-monitor demo

This design keeps the public `sparktacus.io/job-monitor/` experience as a static, fixture-backed demo. Visitors can explore the workflow without triggering live searches, spending cloud budget, or seeing personal job-tracking data. A separate private service can run the real monitor for its owner.

## Target layout

| Component | GCP choice | Purpose |
| --- | --- | --- |
| Public demo | Existing static GitHub Pages site | Demonstrates the UI with synthetic/example records only |
| Private MCP service | Cloud Run, Invoker IAM required | Runs the actual MCP tools for authenticated owner access |
| Persistent audit | Firestore | Durable job history; Cloud Run container files are ephemeral |
| Runtime identity | Dedicated user-managed service account | Minimum permissions to access Firestore and specific secrets |
| Credentials | Secret Manager | Holds only real API credentials if a later integration needs them |
| Build and deploy | Cloud Build or local `gcloud` | Builds and deploys a pinned container image |
| Cost and operations | Cloud Logging/Monitoring and Billing Budget alerts | Visibility and early warning; not a hard spend cap |

For a demo-only deployment, omit Cloud Scheduler and Cloud Tasks. Add them only if scheduled searches are later required; live work should be queued and rate-limited rather than started by a public page request.

## Request and data boundaries

1. The public site continues to serve sample fixtures in the browser. Its JavaScript has no cloud credentials, secret values, or live service URL.
2. The private MCP endpoint requires Google-signed credentials and a `roles/run.invoker` grant. Give that grant only to the operator identity and any explicitly chosen MCP host identity.
3. Cloud Run uses a dedicated service account. It gets only the Firestore permissions needed by the application and Secret Manager access to named secrets, not project-wide Editor.
4. Firestore stores monitor records. Do not store the database on the Cloud Run container filesystem; instances can restart or scale independently.
5. The live service stays separate from the public page. The page cannot invoke private tools or read the private audit.

## Implementation included here

The backend now has an opt-in Firestore store, Cloud Run host/port configuration, a container build, and Terraform for a private Cloud Run service, Firestore, a dedicated runtime identity, and optional secret references. SQLite remains the local default. The linked website is a separate static frontend and remains fixture-backed.

Before a real deployment, set the project ID, region, image digest, and approved invoker identity in a private `terraform.tfvars`. Set a Billing Budget first. The current Greenhouse and Lever integrations do not require credentials, so leave `secret_env` empty unless a future integration adds a secret.

The webpage is intentionally not connected to this private service. A browser-only public page cannot safely hold Google service-account credentials; its fixture mode remains the public demo path.

## Deploy

1. Create/select a project and set a Billing Budget. Enable billing and install `gcloud`, Terraform, and Docker. Enable the Artifact Registry and Cloud Build APIs before building the image:

   ```bash
   gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com
   ```
2. Build and push an immutable image to Artifact Registry (replace the placeholders):

   ```bash
   gcloud config set project YOUR_PROJECT_ID
   gcloud artifacts repositories create job-monitor --repository-format=docker --location=us-east1
   gcloud builds submit --tag us-east1-docker.pkg.dev/YOUR_PROJECT_ID/job-monitor/job-monitor:demo .
   ```

3. If you want configured job sources or a private profile in Cloud Run, create Secret Manager secrets for the JSON documents. Put `JOB_MONITOR_SOURCES_JSON` and optionally `JOB_MONITOR_PROFILE_JSON` in `secret_env` with a numeric version. Keep the JSON payload in Secret Manager and outside Terraform variables and git.
4. Copy `deploy/gcp/terraform.tfvars.example` to `deploy/gcp/terraform.tfvars`, set `project_id`, `container_image` to the image digest, and add your operator as an IAM member such as `user:you@example.com`.
5. Apply the infrastructure:

   ```bash
   cd deploy/gcp
   terraform init
   terraform plan -out=tfplan
   terraform apply tfplan
   ```

6. The output URL is IAM-protected. Invoke it with a Google ID token whose audience is that service URL and a caller identity granted `roles/run.invoker`. No anonymous caller should succeed. For an MCP client, pass the same authenticated bearer token to its Streamable HTTP connection; do not put a token in the public webpage.
7. To roll out a new build, publish a new immutable image digest and apply again. Keep Terraform state private and backed up. The Firestore database and Cloud Run service have deletion protection enabled in Terraform.

## Prompt-injection and tool-safety controls

System prompts cannot be made impossible to override. Treat them as one defense layer, and keep enforceable rules in code and IAM:

- Treat job titles, descriptions, employer pages, imported text, and model output as untrusted data. Never interpret their contents as tool instructions.
- Keep policy and permission checks in server code. Do not let a model choose arbitrary URLs, secret names, filesystem paths, Firestore collections, or cloud APIs.
- Preserve the existing outbound host allowlist, HTTPS requirement, redirect revalidation, timeouts, and response-size limits for employer fetches.
- Keep tools narrowly scoped. The monitor may fetch public job pages and update its own audit; it must not submit applications, send mail, make purchases, or change cloud infrastructure.
- Validate tool inputs and cap batch sizes, result counts, and fetch time. Require fresh employer evidence for promotion as the existing store does.
- Keep public demo data synthetic. Add per-user authorization and data partitioning before any multi-user use.
- Log operational events and error codes, but do not log secrets, authorization headers, or unnecessary personal notes.

## Cost and abuse limits

- Leave Cloud Run minimum instances at zero and set a small maximum instance count.
- Bound request body size, tool-call batch size, fetch count, runtime, and outbound domains in the application.
- Use GCP quotas where available and alerts for anomalous request volume.
- Set a Billing Budget and alert thresholds. Budget alerts notify; they do not automatically stop usage. A hard ceiling needs explicit automated shutdown/quota controls and careful validation.
- Keep public visitors on the static mock path, which makes the most important cost boundary structural rather than prompt-based.

## Rollout sequence

1. Finish the backend's Firestore and Cloud Run support locally.
2. Create or select a GCP project and set its billing budget before enabling services.
3. Enable Cloud Run, Artifact Registry, Firestore, Secret Manager, Cloud Build, and logging APIs as needed.
4. Create Firestore in a region near the expected operator and Cloud Run service.
5. Create the dedicated runtime service account and grant minimal Firestore/secret permissions.
6. Build the container, deploy Cloud Run with Invoker IAM required, min instances `0`, and a small max instance count.
7. Grant `roles/run.invoker` to only the operator and chosen host identities. Confirm an anonymous request is rejected.
8. Run a small owner-only smoke workflow: search a configured test source, read the audit, verify a posting, and export data.
9. Confirm the public website still uses fixtures and cannot reach or invoke the private service.
10. Review logs and billing before considering any scheduled or multi-user features.

## Current boundary

This repository contains a deployment scaffold, but no GCP resources have been created. A project ID, billing configuration, operator identity, and deployment credentials were not provided. The public frontend source is in a separate repository and remains unchanged.

## Official references

- [Cloud Run authentication overview](https://cloud.google.com/run/docs/authenticating/overview)
- [Cloud Run service identity](https://cloud.google.com/run/docs/securing/service-identity)
- [Cloud Run Secret Manager configuration](https://cloud.google.com/run/docs/configuring/services/secrets)
- [Firestore with Python](https://cloud.google.com/python/docs/getting-started)
- [Cloud Billing budgets](https://cloud.google.com/billing/docs/how-to/budgets)
