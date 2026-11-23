# Job Board MCP

An exportable job-monitor workflow with an MCP server, a small Python MCP client, and a local SQLite audit. It captures broadly, verifies availability separately, and keeps application decisions separate from whether an employer is still hiring.

This is a **working starting point, not a universal job-board scraper**. Automated discovery supports configured Greenhouse and Lever employer boards. LinkedIn, Built In, Indeed, USAJOBS, international-development sites and other catalog entries remain discovery/manual-import sources until a permitted adapter is implemented. An inaccessible page is not an open job.

## The MCP pieces

| Piece | In this project | Example |
| --- | --- | --- |
| Backing source | The external job board or employer API | A Greenhouse employer board |
| Resource | Read-only context exposed by the server | `jobboards://catalog`, `jobs://audit` |
| Tool | A typed operation the client can call | `search_jobs`, `filter_jobs`, `check_posting` |
| Prompt | A reusable instruction template for an assistant | `run_job_check`, `assess_job` |
| Client | A program that discovers and invokes those capabilities | `python -m job_board_mcp.client` |

A job board is a backing data source; the MCP **resource** is the catalog or snapshot the server exposes from it. Filters are tool arguments over captured data. Prompts guide the workflow but do not search, schedule themselves, or call a language model on their own.

## Quick start

Requires Python 3.11+. The implementation targets the official MCP SDK's v1 maintenance line, `mcp>=1.29,<2`; it does not claim to target the latest major SDK. No LLM API key is needed for this CLI.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .

python -m job_board_mcp.client discover
python -m job_board_mcp.client sources
python -m job_board_mcp.client resource jobboards://catalog
python -m job_board_mcp.client prompt run_job_check
python -m unittest discover -s tests -v
```

The client starts the server over stdio, performs the MCP handshake, and uses actual MCP protocol calls. It is not a shortcut that imports and calls server functions. An LLM host such as an MCP-enabled desktop assistant can connect to the same server instead.

Optional global arguments go **before** the command: `--db PATH`, `--profile PATH`, and `--sources PATH`. Without overrides the packaged example profile/catalog is loaded; employer board placeholders are disabled until you configure them.

### Run a search

Copy `config/sources.example.json` to a private file, fill in a real Greenhouse board token or Lever site name, and set that employer entry's `enabled` field to `true`. Then use `--sources /path/to/your/sources.json sources` to inspect it. Replace `SOURCE_ID` below with the enabled identifier:

```bash
python -m job_board_mcp.client --sources /path/to/your/sources.json search SOURCE_ID --query "data engineer" --location "United States"
python -m job_board_mcp.client jobs --query "data engineer"
python -m job_board_mcp.client queue
```

The adapter captures every specific posting it fetched **before** returning a filtered view. That is intentional: weak-fit and excluded postings still belong in the raw audit. Search query and location arguments do not turn a public employer feed into a global search engine.

### Verify, then promote

Use a `unique_key` returned by `jobs`; quote URL keys in your shell:

```bash
python -m job_board_mcp.client verify 'SPECIFIC_JOB_URL'
python -m job_board_mcp.client promote 'SPECIFIC_JOB_URL' --region 'Fully Remote' --fit possible
python -m job_board_mcp.client export --view raw --output raw-search-hits.csv
```

Promotion requires fresh, job-specific Open evidence and must pass configured exclusions. `Open`, `Closed`, and `Unclear` describe availability. `new`, `applied`, `no`, `interviewing`, and `phone screen` describe the user's application decision. Discovery and availability checks cannot overwrite the user's application status or `my_notes`.

### Import external discovery

```bash
python -m job_board_mcp.client import-hits docs/examples/manual-hits.json
python -m job_board_mcp.client jobs --query "Example"
```

Manual hits start unverified. Importing a posting, finding its text, or supplying an Apply-looking link is not proof that applications are open. The example uses reserved `.invalid` URLs so it never pretends to be a real opportunity.

## Connect an MCP host

Configure the host to launch the installed server with an absolute Python path. Adapt this common JSON shape to your host's documented configuration format:

```json
{
  "mcpServers": {
    "job-board-monitor": {
      "command": "/ABSOLUTE/PATH/TO/.venv/bin/python",
      "args": ["-m", "job_board_mcp.server"],
      "env": {
        "JOB_MONITOR_DB": "/ABSOLUTE/PRIVATE/PATH/jobs.sqlite3",
        "JOB_MONITOR_PROFILE": "/ABSOLUTE/PATH/TO/config/profile.example.json",
        "JOB_MONITOR_SOURCES": "/ABSOLUTE/PRIVATE/PATH/sources.json"
      }
    }
  }
}
```

The sources path above should contain your configured copy of `config/sources.example.json`; the shipped employer entries are disabled placeholders. The default stdio transport avoids opening a network listener. Optional Streamable HTTP is for trusted local use; do not expose it publicly without an authenticated deployment design.

## Monitor rules preserved

- Keep all specific discovered postings, including exclusions and unverified or closed links; omit generic search/career pages.
- Preserve first seen, posting-date uncertainty, last checked and dated availability history.
- Never derive Open from snippets or job text alone. Respect expired deadlines and generic/expired redirects.
- Keep raw capture separate from filtering, fit assessment and curated promotion.
- Preserve application statuses and personal notes across checks and imports.
- Recheck promoted/recent entries each run and rotate older raw-only Open entries weekly.
- Use the active regions Canada & International, NY NJ PA, DC VA MD, Fully Remote, and Albuquerque; retain Career Transition Jobs separately. Arizona is retired from new discovery.
- Keep unknown pay, authorization, clearance, travel, scheduling and small-dog feasibility explicit rather than inventing favorable answers.

The portable monitor instructions are in `prompts/monitor.md`. A private exact automation export, if included in the delivered bundle, lives in ignored `.local/` and must not be published. This export does not change or cancel the original monitor.

## What is—and is not—included

| Included | Not included |
| --- | --- |
| MCP tools, resources and prompts | A built-in LLM or autonomous agent loop |
| SQLite audit, safe imports and CSV exports | Automatic Google Sheets writes |
| Greenhouse/Lever adapters | Universal LinkedIn/Indeed/Built In scraping |
| Conservative job-specific verification | A guarantee that every employer page is crawlable |
| A recheck queue | An always-running scheduler |
| Repository-ready source and tests | A claim that this repository has been published to GitHub |

## More detail

- [Architecture and capability map](docs/architecture.md)
- [Configure and migrate the monitor](docs/migration.md)
- [Add a job source](docs/source-adapters.md)
- [Parallel workstreams and next milestones](docs/roadmap.md)
- [Security and privacy boundaries](docs/security.md)

Primary references: [official MCP Python SDK v1 documentation](https://py.sdk.modelcontextprotocol.io/v1/), [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html), and [Lever Postings API](https://github.com/lever/postings-api).
