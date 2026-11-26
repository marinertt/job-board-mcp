# Job Board MCP

An exportable job-monitor workflow with an MCP server, a small Python MCP client, and a local SQLite audit. It captures broadly, verifies availability separately, and keeps application decisions separate from whether an employer is still hiring.

This is a **working starting point, not a universal job-board scraper**.
Automated discovery supports configured Greenhouse and Lever employer boards.
Each enabled supported source gets a source-specific search tool, named by its
optional `tool_name` setting or derived from its source ID. LinkedIn, Built In,
Indeed, USAJOBS, international-development sites and other catalog entries
remain discovery/manual-import sources until a permitted adapter is
implemented. An inaccessible page is not an open job.

## The MCP pieces

| Piece | In this project | Example |
| --- | --- | --- |
| Backing source | The external job board or employer API | A Greenhouse employer board |
| Resource | Read-only context exposed by the server | `jobboards://catalog`, `jobs://audit` |
| Tool | A typed operation the client can call | `search_jobs`, `search_<source-id>`, `filter_jobs` |
| Prompt | A reusable instruction template for an assistant | `run_job_check`, `assess_job` |
| Client | An importable async class for connecting to an MCP server | `MCPClient` |

A job board is a backing data source; the MCP **resource** is the catalog or snapshot the server exposes from it. Filters are tool arguments over captured data. Prompts guide the workflow but do not search, schedule themselves, or call a language model on their own.

Each enabled Greenhouse/Lever source is exposed as a dedicated search tool.
Set its `tool_name` in `config/sources.example.json`, or let the server derive
one from the source ID. Disabled and manual-only entries do not get search
tools. The generic `search_jobs(source_id, ...)` remains available.

## Python API

Requires Python 3.11+. The implementation targets the official MCP SDK's v1
maintenance line, `mcp>=1.29,<2`; it does not claim to target the latest major
SDK. The client and server are importable classes, not executable scripts or
console commands.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

`MCPServer` owns the configured FastMCP instance. An owning application can
embed that instance or start its transport programmatically:

```python
from job_board_mcp.server import MCPServer

monitor = MCPServer(
    database=".local/jobs.sqlite3",
    profile_path="config/profile.example.json",
    sources_path="config/sources.example.json",
)
monitor.run(transport="stdio")
```

`MCPClient` manages an async stdio session with a server process configured by
the owning application:

```python
from mcp import StdioServerParameters
from job_board_mcp.client import MCPClient

parameters = StdioServerParameters(command="your-host-configured-server")
async with MCPClient(parameters) as client:
    tools = await client.list_tools()
    sources = await client.call_tool("list_sources")
```

The client performs the MCP handshake and invokes protocol operations; it does
not call server functions directly. Configure database/profile/source paths
through `MCPServer` arguments or the `JOB_MONITOR_DB`, `JOB_MONITOR_PROFILE`,
and `JOB_MONITOR_SOURCES` environment variables. Without overrides, packaged
examples are loaded and employer-board placeholders remain disabled.

Search adapters audit every specific posting they fetch before returning a
filtered view. Search query/location arguments do not turn a public employer
feed into a global search engine. Manual hits start unverified; importing a
posting, finding its text, or supplying an Apply-looking link does not prove
applications are open.

## Interactive demo GUI

Run the local GUI with:

```bash
python -m job_board_mcp.gui
```

It binds to loopback and opens a browser. The left panel edits a private profile
and source configuration; **Save & reconnect** writes copies under
`.local/job-board-demo/` and restarts the MCP subprocess. The default right
panel focuses on the raw audit and curated result tabs. The **How it works**
view preserves the client/server walkthrough, including protocol requests and
responses, registered tools, resources, and prompts. It can run enabled source
tools, verify postings, and request promotion through the existing gates.

Use **Load demo fixtures** to populate the raw audit with clearly labeled
`.invalid` sample URLs. They are never real jobs and remain `Unclear`; the GUI
does not bypass availability or promotion policy. Live source searches require
an explicitly enabled and configured Greenhouse/Lever board. The GUI does not
edit spreadsheets or submit applications.

### Add the demo to the SPARKTACUS site

The existing company site is `marinertt.github.io`; the workbench belongs at
`/job-monitor/` in that site, not as a separate Pages deployment. Build its
static page directly into a local checkout of the site:

```bash
python scripts/build_pages.py --output /path/to/marinertt.github.io/job-monitor
```

Then run the site checks and publish changes from the website repository. The
static page labels client/server actions as simulated: GitHub Pages cannot run
the Python process or MCP stdio connection, so searches, availability checks,
promotions, and workbook writes are disabled. Configuration and synthetic
`.invalid` sample rows stay in each visitor's browser storage. Use the local
GUI above for actual MCP operations.

## Connect an MCP host

There is no standalone launcher or host-specific MCP configuration in this
package. Integrate `MCPServer.server` into an in-process MCP host, or call
`MCPServer.run()` from an application that owns the server lifecycle. The
default stdio transport avoids opening a network listener. Optional
Streamable HTTP is for trusted local use; do not expose it publicly without an
authenticated deployment design.

## Monitor rules preserved

- Keep all specific discovered postings, including exclusions and unverified or closed links; omit generic search/career pages.
- Preserve first seen, posting-date uncertainty, last checked and dated availability history.
- Never derive Open from snippets or job text alone. Respect expired deadlines and generic/expired redirects.
- Keep raw capture separate from filtering, fit assessment and curated promotion.
- Preserve application statuses and personal notes across checks and imports.
- Recheck promoted/recent entries each run and rotate older raw-only Open entries weekly.
- Use only the regions and location rules defined by the active profile; keep career-transition scope configurable.
- Keep unknown pay, authorization, clearance, travel, scheduling, and other personal constraints explicit rather than inventing favorable answers.

The portable monitor instructions are in `prompts/monitor.md`; the MCP `run_job_check` prompt includes the active profile so its tabs, regions, search scope, and constraints can be reconfigured without editing the instructions. Source-specific details remain in the configured source catalog. A private exact automation export, if included in the delivered bundle, lives in ignored `.local/` and must not be published. This export does not change or cancel the original monitor.

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
