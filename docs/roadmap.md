# Parallel workstreams

The first version is divided into small components with explicit interfaces. Each can be developed and tested independently; integration checks must still exercise the real client/server protocol.

| Workstream | Deliverable | Acceptance check |
| --- | --- | --- |
| Policy and audit | Models, SQLite store, original schema mappings, portable profile and monitor export | First seen/history survive; user fields never overwritten; promotion gate fails safely |
| Sources and availability | Greenhouse/Lever adapters and conservative exact-posting verifier | Fixture tests prove Open/Closed/Unclear and reject unsafe destinations |
| MCP protocol | Server tools/resources/prompts and a Python CLI client | Client discovers and calls the server over stdio |
| Documentation and delivery | Setup, architecture, migration, examples and CI | Commands match actual help; clean checkout can install and test |

## Version 1 boundaries

The first deliverable is repository-ready code, not a production hosted service. It is useful for learning MCP, capturing allowed public-board results, importing manually discovered URLs, auditing availability and exporting interoperable CSV.

No application submission, hidden browser session reuse, secret extraction, automatic spreadsheet mutation or unattended public HTTP deployment is included.

## Next milestones

1. **Portable run orchestration:** deterministic run manifests and delta reports; distinguish a completed full sweep from failed or partial discovery. Schedule with an external scheduler or an explicitly configured host.
2. **More permitted source adapters:** USAJOBS and relevant official feeds; separately evaluate Canadian/development-sector APIs. Search-provider adapters need explicit credentials and licensing.
3. **Richer assessment:** structured technical/cultural evidence and location/eligibility constraints. Never promote a strong culture rating based only on remote work or a single review.
4. **Sheets synchronization:** opt-in dry-run plans, conflict checks, protected user fields, row visibility and current workbook validation.
5. **SDK evolution:** evaluate the current MCP major release and add migration tests before removing the explicit v1 compatibility pin.
6. **Hosted transport:** authenticated HTTP deployment, authorization scopes, per-user data isolation, audit logs and quotas.

## GitHub publishing

No GitHub publication is implied by a local checkout or ZIP. Before pushing:

- Choose an existing repository or create a new private repository.
- Confirm the connected GitHub account and destination.
- Review `git status` and the staged diff.
- Exclude `.local/`, databases, CSV exports, credentials, exact private monitor exports and application records.
- Run the unit and protocol tests.

The exact saved monitor prompt may include a private workbook link. Keep the sanitized `prompts/monitor.md` in Git and the exact export private unless you intentionally choose otherwise.
