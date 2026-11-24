"""End-to-end MCP tests: real stdio subprocess, no network or model key."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import StdioServerParameters

from job_board_mcp.client import MCPClient
from job_board_mcp.server import _csv


ROOT = Path(__file__).resolve().parents[1]


class MCPClientTests(unittest.TestCase):
    def test_csv_formula_mitigation_preserves_underlying_values(self) -> None:
        rows = [{"title": " =HYPERLINK(\"https://example.invalid\")", "salary": -12}]
        rendered = _csv(rows, ["title", "salary"])
        self.assertIn("' =HYPERLINK", rendered)
        self.assertIn(",-12", rendered)
        self.assertEqual(rows[0]["title"], " =HYPERLINK(\"https://example.invalid\")")

    def test_client_requires_connection_before_protocol_calls(self) -> None:
        client = MCPClient(StdioServerParameters(command=sys.executable))
        with self.assertRaises(RuntimeError):
            _ = client.session


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_resources_prompts_audit_and_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env["JOB_MONITOR_DB"] = str(Path(directory) / "jobs.sqlite3")
            sources_path = Path(directory) / "sources.json"
            sources_path.write_text(json.dumps([
                {
                    "id": "test_board",
                    "name": "Test Board",
                    "adapter": "greenhouse",
                    "tool_name": "search_test_board",
                    "board": "example",
                    "enabled": True,
                },
                {
                    "id": "disabled_board",
                    "adapter": "lever",
                    "tool_name": "search_disabled_board",
                    "board": "example",
                    "enabled": False,
                },
                {"id": "manual", "name": "Manual imports", "adapter": "manual"},
            ]), encoding="utf-8")
            env["JOB_MONITOR_SOURCES"] = str(sources_path)
            env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-c", "from job_board_mcp.server import MCPServer; MCPServer().run()"],
                env=env,
                cwd=str(ROOT),
            )
            async with asyncio.timeout(30):
                async with MCPClient(parameters) as client:
                    tools = {tool.name for tool in (await client.list_tools()).tools}
                    self.assertEqual(tools, {
                        "list_sources", "search_jobs", "filter_jobs", "import_hits",
                        "check_posting", "get_recheck_queue", "promote_job", "export_jobs",
                        "search_test_board",
                    })
                    self.assertNotIn("search_disabled_board", tools)
                    resources = {str(item.uri) for item in (await client.list_resources()).resources}
                    self.assertIn("jobboards://catalog", resources)
                    self.assertIn("policy://profile", resources)
                    self.assertIn("jobs://audit", resources)
                    templates = (await client.list_resource_templates()).resourceTemplates
                    self.assertTrue(any(str(item.uriTemplate) == "jobboard://{source_id}" for item in templates))
                    prompts = {item.name for item in (await client.list_prompts()).prompts}
                    self.assertEqual(prompts, {"discovery_queries", "run_job_check", "assess_job", "report_changes"})

                    catalog = await client.read_resource("jobboards://catalog")
                    configured_sources = json.loads(catalog.contents[0].text)
                    self.assertTrue(configured_sources)
                    self.assertEqual(configured_sources[0]["tool_name"], "search_test_board")
                    source_details = await client.read_resource("jobboard://test_board")
                    self.assertEqual(
                        json.loads(source_details.contents[0].text)["tool_name"],
                        "search_test_board",
                    )
                    run_prompt = await client.get_prompt("run_job_check", {"as_of": "2026-11-23"})
                    self.assertIn("2026-11-23", run_prompt.messages[0].content.text)
                    self.assertIn("ACTIVE PROFILE (authoritative configuration)", run_prompt.messages[0].content.text)
                    self.assertIn('"recent_recheck_days": 14', run_prompt.messages[0].content.text)

                    imported = await client.call_tool("import_hits", {"hits": [{
                        "company": "Protocol Example",
                        "title": "Data Engineer",
                        "location": "Example Region",
                        "url": "https://jobs.lever.co/example/01234567-89ab-cdef-0123-456789abcdef",
                        "source_id": "manual",
                    }]})
                    self.assertFalse(imported.isError)
                    hits = await client.call_tool("filter_jobs", {"query": "Protocol Example"})
                    payload = hits.structuredContent
                    jobs = payload if isinstance(payload, list) else (payload or {}).get("result")
                    if jobs is None:
                        jobs = json.loads(hits.content[0].text)
                    self.assertEqual(len(jobs), 1)
                    job = jobs[0]
                    self.assertEqual(job.get("current_status", job.get("availability")), "Unclear")
                    key = job.get("unique_key", job.get("url"))

                    rejected = await client.call_tool("promote_job", {
                        "unique_key": key, "region": "Example Region", "overall_fit": "possible",
                    })
                    self.assertTrue(rejected.isError)
                    csv_result = await client.call_tool("export_jobs", {"view": "raw"})
                    self.assertFalse(csv_result.isError)
                    self.assertIn("Search date,First seen,Posting date", csv_result.content[0].text)
                    self.assertIn("Protocol Example", csv_result.content[0].text)
                    assessed = await client.get_prompt("assess_job", {"unique_key": key})
                    self.assertIn("UNTRUSTED JOB DATA", assessed.messages[0].content.text)


if __name__ == "__main__":
    unittest.main()
