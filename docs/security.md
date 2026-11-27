# Security and privacy

## Scope and trust

Job descriptions and external pages are untrusted data. Their text must not become executable instructions, change the configured policy, request credentials or cause arbitrary tool calls. Prompts should treat any embedded “instructions” in a posting as content to inspect, not commands to follow.

The server performs public GET requests only. It does not submit job applications or attempt to authenticate to employers. There is no CAPTCHA, login-wall, robots or permission bypass.

## Network boundaries

Verification is deliberately restricted to supported HTTPS employer hosts. Public destination validation, redirect revalidation, timeouts and response-size limits reduce SSRF and resource-exhaustion risk. Unsupported hosts produce Unclear; do not expand the allowlist casually to arbitrary user URLs.

DNS and networking are security-sensitive. A production deployment needs an outbound proxy or equivalent network-layer controls to address DNS rebinding and deployment-specific routing. The local verifier is not a substitute for a hardened egress boundary.

## User-owned data

SQLite stores application-related history. Treat it, CSV exports and private profiles as sensitive. Do not commit them. `application_status` and `my_notes` belong to the user; discovery, verification and CSV reimport must not overwrite them. Availability and user decisions are different fields.

The exact source automation export may contain a workbook URL or personal preferences. It is kept under ignored `.local/`; the repository-facing prompt is sanitized. A ZIP can contain ignored files, so inspect the bundle before sharing or uploading it.

## Vulnerability detection and remediation

The free `Free quality and security checks` GitHub Actions workflow runs on
pushes, pull requests, weekly, and on demand. Ruff checks Python lint and
formatting, Bandit checks Python source for common security issues, and
pip-audit checks installed dependencies against known vulnerability data.
These tools are signals for review, not proof that the project is vulnerability
free. Bandit is heuristic and currently scans `job_board_mcp`; pip-audit uses
`--skip-editable`, so it audits installed dependencies but not this
unpublished editable project as a package. The workflow does not automatically
change or upgrade code or dependencies.

When a finding appears:

1. Triage the report and create a GitHub issue for every actionable
   vulnerability, including findings that still need confirmation. Include
   severity, affected component and versions, the scanner or review that
   identified it, impact, and remediation status. Do not include credentials
   or unnecessary exploit details. Close false positives with the reason
   documented; document any suppression with its rationale and a recheck
   condition.
2. For dependency findings, update the compatible version bounds in
   `pyproject.toml`, install the updated project, and rerun pip-audit and the
   test suite. Prefer a patched compatible release; do not suppress a finding
   just to make CI pass.
3. For source findings, fix the underlying unsafe behavior and add or update a
   focused regression test. Review the fix and rerun Bandit, Ruff, and the test
   suite before merging.
4. Treat critical and high-severity findings as release blockers until fixed
   or until maintainers document an explicit mitigation and acceptance. Track
   medium and low findings with an owner and a planned review.

Do not disclose details of an unpatched vulnerability in a public issue if
that could put users at risk. Use GitHub's private vulnerability reporting or
security-advisory flow if enabled, or contact a maintainer privately; create a
public tracking issue once disclosure is safe. The automated workflow reports
findings but does not create issues automatically. This project documents
triage expectations but does not promise a fixed remediation deadline.

## MCP transport

Stdio is the default and recommended local transport. `MCPClient` launches the
configured server process; this package has no standalone client/server
launcher. An owning application can call `MCPServer.run()` or embed its
`FastMCP` instance. Stdout is reserved for MCP messages; diagnostics belong on
stderr.

Optional Streamable HTTP is loopback-first. It is not production-ready public hosting. Add authentication, authorization, TLS, origin/host validation, rate limits and per-user data isolation before remote use. Do not expose an unauthenticated audit resource containing personal application history.

## CSV and files

The MCP export tool returns content instead of writing to an arbitrary server
path. Any file-writing host must choose an explicit local destination and avoid
overwriting existing files. The exporter prefixes formula-like text with an
apostrophe for spreadsheet safety; the stored audit remains unchanged. Review
CSV in the target application before further importing it.

Policy regex checks and place-name recognition are conservative helpers, not complete industry/citizenship/pay adjudication. The host or a human must assess the full prompt, including paid-training floors and unknown country authorization. Missing evidence must not be presented as a successful eligibility check. The v1 promotion API intentionally refuses `strong` unless both technical and culture evidence exist; collecting structured assessment evidence is a future extension.

Back up the database before migration. Test imports on a copy. Do not infer that an empty API response confirms closure of every previously captured job; verification must operate on individual exact postings.
