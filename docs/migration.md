# Configure and migrate

## 1. Keep private data out of Git

The repository's portable prompt removes the original spreadsheet URL. An exact export of the saved automation prompt and schedule belongs under ignored `.local/`, alongside any private profile overrides and database. Review the archive before publishing; ignored files can still be present in a ZIP you share.

An automation export is a record of instructions and schedule, not an export of the service's execution environment, browser sessions, connector credentials or run history. This project neither moves nor changes the existing scheduled task.

## 2. Configure search sources and preferences

The server reads these environment variables:

| Variable | Purpose |
| --- | --- |
| `JOB_MONITOR_DB` | Local SQLite file; choose a private, writable directory |
| `JOB_MONITOR_PROFILE` | JSON search/promotion profile |
| `JOB_MONITOR_SOURCES` | JSON catalog and employer-board configuration |

Copy the shipped examples into your private configuration directory and edit them. Keep employer boards explicit: knowing that a company uses Greenhouse does not establish its board token. Use `sources` to inspect the loaded configuration before discovery.

The exported policy retains the monitor's active areas: Canada & International, NY NJ PA, DC VA MD, Fully Remote, Albuquerque, and Career Transition Jobs. Albuquerque/Rio Rancho replaces Arizona discovery. Historical Arizona rows need not be deleted or relabeled to make that change.

## 3. Optional historical CSV import

No existing spreadsheet data is silently downloaded. Export only the tabs you want from Google Sheets as CSV, inspect the headers, and import them locally. Treat these files as sensitive because statuses and personal notes may be present.

The trusted local Python API accepts a CSV path; it is intentionally not an MCP tool that reads arbitrary server filesystem paths:

```python
from job_board_mcp.store import JobStore

store = JobStore(".local/jobs.sqlite3")
summary = store.import_csv(".local/raw-search-hits.csv", view="raw")
print(summary)
```

Or use the local migration CLI (not an MCP filesystem tool):

```bash
python -m job_board_mcp.migrate .local/raw-search-hits.csv --view raw --db .local/jobs.sqlite3
python -m job_board_mcp.migrate .local/fully-remote.csv --view 'Fully Remote' --db .local/jobs.sqlite3
```

Import raw first, then curated views. The first curated import initializes its user-owned fields; later imports never overwrite existing application decisions or personal notes. Remove workbook title rows so the exact column header is the first CSV row. Historical Open labels become Unclear until the application is reverified; a historical Closed label stays Closed until positive reopening evidence.

`config/monitor-export.json` preserves the exported task's schedule and timezone as portable metadata. It is not an executable scheduler; the original task remains unchanged. The public-ready bundle intentionally omits `.local/monitor-export.json` and all live spreadsheet/application data.

Supported curated view names are the active region names and Career Transition Jobs. Run imports against a copied database first. Existing status and `my_notes` values are user-owned and must not be overwritten by reimport, discovery or rechecking. A portable export does not establish that a previously seen job is still open; queue and reverify it.

## Schema mappings

The Raw Search Hits export preserves the original A:R order:

| Column | Field | Column | Field |
| --- | --- | --- | --- |
| A | Search date | J | Company |
| B | First seen | K | Job title |
| C | Posting date | L | Location |
| D | Last seen | M | Work arrangement |
| E | Last checked | N | Source |
| F | Current status | O | Job link |
| G | Status changed | P | Tracker disposition |
| H | Status history | Q | Notes |
| I | Search stream | R | Unique key |

The regional schema remains:

```text
search_date,last_checked,status,search_stream,overall_fit,company,job_title,location,work_arrangement,source,job_link,notes,unique_key,my_notes
```

Career Transition Jobs retains its detailed schema, including schedule, employment type, pay period, paid training, credential path, physical demands, accommodations and review evidence. Its last four fields remain `Notes`, `Unique key`, `overall_fit`, and `my_notes`. Undisclosed pay is not invented; hourly pay is not annualized without known hours.

## 4. Connect a host or schedule a run

Use stdio for a local MCP host. An external scheduler can start the client, but invoking `prompt run_job_check` only prints instructions: it does not execute them. A host must orchestrate discovery, the recheck queue, verification, assessment, promotion and change reporting.

For a deterministic non-LLM runner, use a reviewed source list and explicit calls. Keep the discovery range and evidence gate in code, not merely in a natural-language prompt. Do not interpret an empty or partial source response as “nothing changed.”

## Future Google Sheets synchronization

A later Sheets adapter should be opt-in, dry-run first, and conflict-aware. It must resolve the exact workbook ID, read current metadata/headers/keys/user fields before writing, preserve statuses and `my_notes`, append notes, and update visibility without deleting history. It must not add new jobs to the legacy Job Tracker.

CSV export is the v1 boundary. It does **not** modify the live workbook, retain its filters/dropdowns, or claim that local hidden-state changes have reached Google Sheets.
