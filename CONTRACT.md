# Implementation contract

Python 3.11+, official MCP Python SDK v1 compatibility line (`mcp>=1.29,<2`, installed/tested version 1.29). stdio transport by default. No LLM API key needed for CLI.

Ownership: root owns models.py, store.py, policy.py, config, exported prompt, packaging and integration tests. Sources agent owns sources.py, verification.py and source/verification tests. Protocol agent owns server.py, client.py and protocol tests. Documentation agent owns README.md, docs/* and .github/workflows/tests.yml. Do not modify another owner's files. All edits via apply_patch.

## Root-provided interfaces

`models.py`: Pydantic `JobHit(company:str,title:str,location:str,url:str,source_id:str,description:str='',posting_date:str='Unknown',posting_date_note:str='',work_arrangement:str='Unknown',source_job_id:str='',application_url:str='',deadline:str='')`; `Verification(status:Literal['Open','Closed','Unclear'],checked_at:str,evidence_url:str,reason:str,evidence_type:str='unverified')`; `JobFilters(query:str='',location:str='',source_id:str='',availability:str='',region:str='',application_status:str='',limit:int=100)`.

`store.py`: `JobStore(path: str)`; `upsert_hits(hits:list[JobHit],search_stream:str='')->dict` stores ALL before filtering; `list_jobs(filters:JobFilters|None=None)->list[dict]`; `get_job(unique_key:str)->dict`; `record_verification(key:str,verification:Verification)->dict`; `promote(key:str,region:str,overall_fit:str='possible',notes:str='')->dict`; `recheck_queue(today:str|None=None)->list[dict]`; `export_rows(view:str='raw')->list[dict]`; `import_csv(path:str,view:str='raw')->dict`. SQLite DB private runtime data, concurrent safe connections, no applicant status overwrite by discovery/recheck/import. Preserve first_seen/status_history; only promotion sets default new; no deleting rows.

`policy.py`: `load_profile(path:str|None=None)->dict`; `assess_exclusions(job:dict,profile:dict)->list[str]`; `build_queries(profile:dict,as_of:str|None=None)->list[dict]`. Constants `RAW_HEADERS`, `REGIONAL_HEADERS`, `CAREER_HEADERS`; `DEFAULT_PROFILE_PATH`.

## Sources / verification interfaces

`sources.py`: `load_sources(path:str|None=None)->list[dict]`; `async search_source(source:dict,query:str='',location:str='')->list[JobHit]`. Built-in example employer boards for Greenhouse and Lever only. Board query/location are advisory but adapter must return all fetched specific posts to audit before server filtering. Catalog includes manual-only LinkedIn/Built In/etc; raise descriptive ValueError for unsupported discovery, never fake working scraper. Sources config paths local env. Public GET only; no applications, authentication bypass, or search-service claims. httpx `MockTransport` tests.

`verification.py`: `async verify_posting(job:dict)->Verification`. Actual employer-specific apply evidence required for Open; description/snippet alone => Unclear. Explicit closure/expired deadline/generic redirect=>Closed. Safe network controls: public HTTPS allowlisted hosts, DNS public IP validation, redirect target revalidation, timeouts/body limit. No arbitrary internal URLs. Recognize exact Greenhouse/Lever requisitions and structured application forms; application action must be tied to exact job, not footer. Closed/reopen transitions only evidence. Unsupported hosts => Unclear. Use dict keys url or job_link, source_id, source_job_id, deadline, application_url.

## Server / client

`server.py`: use FastMCP. Tools list_sources, search_jobs(source_id,query='',location='',search_stream=''), filter_jobs(query='',location='',source_id='',availability='',region='',application_status='',limit=100), import_hits(hits:list[dict],search_stream=''), check_posting(unique_key), get_recheck_queue, promote_job(unique_key,region,overall_fit='possible',notes=''), export_jobs(view='raw') returns CSV string (no arbitrary file writes). JobStore promotion gate checks fresh Open evidence, exclusions, supported region. No tool edits applicant status/my_notes. Resources `jobboards://catalog`, `jobboard://{source_id}`, `jobs://audit`, `policy://profile`. Prompts `discovery_queries`, `run_job_check`, `assess_job`, `report_changes`; exact exported monitor prompt loaded from prompts/monitor.md (portable/sanitized); prompts are instruction templates, not execution/scheduling. Server reads JOB_MONITOR_DB, JOB_MONITOR_PROFILE, JOB_MONITOR_SOURCES; CLI optional `--transport stdio|streamable-http` loopback default. `create_server()` for tests.

`client.py`: argparse CLI protocol client using ClientSession+stdio_client, call initialize then list_tools/resources/prompts; commands discover, sources, search, jobs, verify, queue, promote, import-hits FILE, export --view --output, prompt NAME --arg KEY=VALUE, resource URI; JSON outputs. CLI launches server same sys.executable -m job_board_mcp.server. default stdio. Read-only default demo via fixtures, tests must exercise real protocol over stdio. `main()` synchronous entry wrapper.

## Scope

No live spreadsheet mutation in v1. CSV imports/exports match original headers and keep application status/my_notes separate. Curated regional and career schemas preserved; career detail data carried in metadata. Raw audit distinct URLs, canonicalize tracking-only query keys but retain specific-link aliases. Fresh Open <=24h required before promotion. Fit strong needs explicit technical+cultural evidence; no inferred culture. Primary regions Canada & International, NY NJ PA, DC VA MD, Fully Remote, Albuquerque, Career Transition Jobs; Arizona retired. No auto-applying. No credentials/personal application records committed. Current monitor exact prompt and schedule exported to ignored `.local/` only; sanitized prompt committed.
