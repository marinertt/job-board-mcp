# Add a source

Start with official, documented public feeds where possible. Respect the source's terms, API access rules, rate limits and robots guidance. Do not bypass login, CAPTCHAs or access restrictions. A source's listing API and its application state are separate concerns.

## Implemented discovery

- **Greenhouse employer boards:** the public Job Board API lists published job posts and returns specific employer-board job details. `updated_at` is not silently treated as the original posting date.
- **Lever employer boards:** the public Postings API returns individual postings with hosted and application URLs. A listing is captured as a hit; verification separately checks the exact application path.

The catalog may also describe LinkedIn, Built In, Indeed, Dice, Wellfound, ZipRecruiter, Canadian boards, government and development-sector sites. Catalog presence means “recognized search source,” not “working scraper.” Manual-only entries must report that limitation explicitly.

Primary API documentation: [Greenhouse](https://developers.greenhouse.io/job-board.html) and [Lever](https://github.com/lever/postings-api).

## Adapter contract

`search_source(source, query, location)` returns normalized `JobHit` objects. Return every specific posting fetched from the chosen board; the server audits them before applying presentation filters. Do not hide exclusions, uncertain matches or closed results during normalization.

Each hit should include:

- Specific stable URL, source ID and employer requisition ID when known.
- Employer, title and the primary eligible location.
- Posting date only if established; otherwise `Unknown` and any relative wording in `posting_date_note`.
- Application URL and deadline if supplied by the exact source.
- Work arrangement only when supported by evidence; “remote” is not automatically “US-wide remote.”

Use a stable job-specific URL as the key. Keep job-defining query parameters. Never store a generic careers/search page as if it were one opening.

## Verification contract

`verify_posting(job)` returns a typed `Verification`, not a bare boolean. Record status, check timestamp, evidence URL, evidence type and reason. An accessible description or search card is not acceptance evidence.

For a new provider:

1. Validate the initial HTTPS URL, allowed host and public destination.
2. Fetch with a timeout, bounded response size and controlled redirects.
3. Revalidate every redirect destination. A generic or expired job search is a closure signal, not a successful application.
4. Check the exact requisition and deadline before accepting Apply evidence.
5. Require a job-bound application action/form or documented employer acceptance state for Open.
6. Return Unclear when the page is unsupported, blocked, inaccessible or ambiguous.

A generic footer “Apply,” unrelated recommended-job cards, an indexed Workday description and a login page without the exact requisition do not satisfy the gate. Employer 404/410 and explicit removed/expired signals should be handled deliberately; other failures must not be misreported as closure.

## Required tests

Use fixture bodies and `httpx.MockTransport`; do not make the test suite depend on today's public listings. Cover:

- All surfaced URLs are returned even when they fail a filter or policy exclusion.
- Query and location filtering happens after audit capture.
- Posting-date uncertainty and relative wording survive normalization.
- A genuine job-specific application path can establish Open.
- Plain job text, footer Apply links and broken pages remain Unclear.
- Expired deadline, removed-job text and generic redirects produce Closed.
- Private IPs, URL credentials, unapproved hosts, unsafe redirects and oversized bodies are rejected safely.
- Reopened postings need new positive evidence.

Future search-provider integration should be a separate adapter with user-supplied credentials. This repository does not export ChatGPT web-search access or reuse its connectors as if they were public APIs.
