"""End-to-end MCP tests: real stdio subprocess, no network or model key."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

from job_board_mcp.client import _prompt_args, build_parser
from job_board_mcp.server import _csv


ROOT = Path(__file__).resolve().parents[1]


class ClientParserTests(unittest.TestCase):
    def test_csv_formula_mitigation_preserves_underlying_values(self) -> None:
        rows = [{"title": " =HYPERLINK(\"https://example.invalid\")", "salary": -12}]
        rendered = _csv(rows, ["title", "salary"])
        self.assertIn("' =HYPERLINK", rendered)
        self.assertIn(",-12", rendered)
        self.assertEqual(rows[0]["title"], " =HYPERLINK(\"https://example.invalid\")")

    def test_cli_flags_are_passed_as_named_fields(self) -> None:
        args = build_parser().parse_args([
            "--db", "private.sqlite3", "jobs", "--source-id", "manual",
            "--application-status", "applied", "--limit", "12",
        ])
        self.assertEqual(args.db, "private.sqlite3")
        self.assertEqual(args.source_id, "manual")
        self.assertEqual(args.application_status, "applied")
        self.assertEqual(args.limit, 12)

    def test_prompt_arguments_preserve_equals_in_value(self) -> None:
        self.assertEqual(_prompt_args(["query=a=b", "as_of=2026-11-23"]), {
            "query": "a=b", "as_of": "2026-11-23",
        })
        with self.assertRaises(ValueError):
            _prompt_args(["not-key-value"])


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_resources_prompts_audit_and_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env["JOB_MONITOR_DB"] = str(Path(directory) / "jobs.sqlite3")
            env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "job_board_mcp.server"], env=env, cwd=str(ROOT)
            )
            async with asyncio.timeout(30):
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = {tool.name for tool in (await session.list_tools()).tools}
                        self.assertEqual(tools, {
                            "list_sources", "search_jobs", "filter_jobs", "import_hits",
                            "check_posting", "get_recheck_queue", "promote_job", "export_jobs",
                        })
                        resources = {str(item.uri) for item in (await session.list_resources()).resources}
                        self.assertIn("jobboards://catalog", resources)
                        self.assertIn("policy://profile", resources)
                        self.assertIn("jobs://audit", resources)
                        templates = (await session.list_resource_templates()).resourceTemplates
                        self.assertTrue(any(str(item.uriTemplate) == "jobboard://{source_id}" for item in templates))
                        prompts = {item.name for item in (await session.list_prompts()).prompts}
                        self.assertEqual(prompts, {"discovery_queries", "run_job_check", "assess_job", "report_changes"})

                        catalog = await session.read_resource(AnyUrl("jobboards://catalog"))
                        self.assertTrue(json.loads(catalog.contents[0].text))
                        run_prompt = await session.get_prompt("run_job_check", arguments={"as_of": "2026-11-23"})
                        self.assertIn("2026-11-23", run_prompt.messages[0].content.text)

                        imported = await session.call_tool("import_hits", arguments={"hits": [{
                            "company": "Protocol Example",
                            "title": "Data Engineer",
                            "location": "Albuquerque, NM",
                            "url": "https://jobs.lever.co/example/01234567-89ab-cdef-0123-456789abcdef",
                            "source_id": "manual",
                        }]})
                        self.assertFalse(imported.isError)
                        hits = await session.call_tool("filter_jobs", arguments={"query": "Protocol Example"})
                        payload = hits.structuredContent
                        jobs = payload if isinstance(payload, list) else (payload or {}).get("result")
                        if jobs is None:
                            jobs = json.loads(hits.content[0].text)
                        self.assertEqual(len(jobs), 1)
                        job = jobs[0]
                        self.assertEqual(job.get("current_status", job.get("availability")), "Unclear")
                        key = job.get("unique_key", job.get("url"))

                        rejected = await session.call_tool("promote_job", arguments={
                            "unique_key": key, "region": "Albuquerque", "overall_fit": "possible",
                        })
                        self.assertTrue(rejected.isError)
                        csv_result = await session.call_tool("export_jobs", arguments={"view": "raw"})
                        self.assertFalse(csv_result.isError)
                        self.assertIn("Search date,First seen,Posting date", csv_result.content[0].text)
                        self.assertIn("Protocol Example", csv_result.content[0].text)
                        assessed = await session.get_prompt("assess_job", arguments={"unique_key": key})
                        self.assertIn("UNTRUSTED JOB DATA", assessed.messages[0].content.text)


if __name__ == "__main__":
    unittest.main()
