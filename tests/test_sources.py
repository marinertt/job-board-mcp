import asyncio
import gzip
import json
import socket
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from job_board_mcp.sources import (
    SourceUnavailable, load_sources, safe_get, search_source, validate_public_dns,
    source_tool_name, validate_url_shape,
)


def response(payload, url="https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true", status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", url))


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_greenhouse_returns_all_before_filters_and_skips_prospects(self):
        source = {"id": "test", "adapter": "greenhouse", "board": "example", "company": "Example"}
        payload = {"jobs": [
            {"id": 1, "internal_job_id": 100, "title": "Data Engineer", "location": {"name": "DC"},
             "absolute_url": "https://job-boards.greenhouse.io/example/jobs/1", "updated_at": "2026-10-01", "content": "<p>Python</p>"},
            {"id": 2, "internal_job_id": 101, "title": "Sales Rep", "location": {"name": "Paris"},
             "absolute_url": "https://job-boards.greenhouse.io/example/jobs/2"},
            {"id": 3, "internal_job_id": None, "title": "General interest"},
        ]}
        with patch("job_board_mcp.sources.safe_get", AsyncMock(return_value=response(payload))):
            jobs = await search_source(source, query="Data Engineer", location="DC")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].posting_date, "Unknown")
        self.assertEqual(jobs[0].description, "Python")

    async def test_lever_returns_exact_specific_jobs(self):
        identifier = "11111111-1111-1111-1111-111111111111"
        source = {"id": "lever", "adapter": "lever", "board": "example", "region": "eu"}
        payload = [{"id": identifier, "text": "Data Engineer", "categories": {"location": "Paris"},
                    "hostedUrl": f"https://jobs.eu.lever.co/example/{identifier}",
                    "applyUrl": f"https://jobs.eu.lever.co/example/{identifier}/apply", "createdAt": 1792492800000}]
        with patch("job_board_mcp.sources.safe_get", AsyncMock(return_value=response(payload))) as mock:
            jobs = await search_source(source)
        self.assertEqual(jobs[0].source_job_id, identifier)
        self.assertEqual(jobs[0].posting_date, "2026-10-20")
        self.assertIn("api.eu.lever.co", mock.call_args.args[0])

    async def test_manual_sources_do_not_fake_scraping(self):
        with self.assertRaisesRegex(ValueError, "Manual discovery"):
            await search_source({"id": "linkedin", "adapter": "manual"})

    async def test_disabled_sources_do_not_fetch(self):
        with self.assertRaisesRegex(ValueError, "disabled"):
            await search_source({"id": "example", "adapter": "lever", "enabled": False})

    async def test_invalid_board_token_cannot_change_api_path(self):
        with self.assertRaisesRegex(ValueError, "valid employer"):
            await search_source({"id": "bad", "adapter": "greenhouse", "board": "../private"})

    async def test_api_failure_is_explicit(self):
        with patch("job_board_mcp.sources.safe_get", AsyncMock(return_value=response({}, status=403))):
            with self.assertRaisesRegex(SourceUnavailable, "HTTP 403"):
                await search_source({"id": "test", "adapter": "greenhouse", "board": "example"})

    async def test_mock_transport_bounded_safe_get(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
        async with httpx.AsyncClient(transport=transport) as client:
            with patch("job_board_mcp.sources.validate_public_dns", AsyncMock()):
                result = await safe_get("https://api.lever.co/v0/postings/example?mode=json", client=client)
        self.assertEqual(result.json(), {"ok": True})

    async def test_gzip_is_not_double_decoded(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200,
            content=gzip.compress(b'{"ok": true}'), headers={"content-encoding": "gzip"}))
        async with httpx.AsyncClient(transport=transport) as client:
            with patch("job_board_mcp.sources.validate_public_dns", AsyncMock()):
                result = await safe_get("https://api.lever.co/v0/postings/example?mode=json", client=client)
        self.assertEqual(result.json(), {"ok": True})

    async def test_redirect_to_internal_url_blocked_before_request(self):
        seen = []
        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("job_board_mcp.sources.validate_public_dns", AsyncMock()):
                with self.assertRaises(SourceUnavailable):
                    await safe_get("https://jobs.lever.co/example", client=client)
        self.assertEqual(len(seen), 1)

    async def test_every_redirect_dns_checked(self):
        def handler(request):
            if str(request.url).endswith("/start"):
                return httpx.Response(302, headers={"location": "/finish"})
            return httpx.Response(200, text="done")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("job_board_mcp.sources.validate_public_dns", AsyncMock()) as mock:
                result = await safe_get("https://jobs.lever.co/start", client=client)
        self.assertEqual(mock.await_count, 2)
        self.assertTrue(str(result.url).endswith("/finish"))

    async def test_body_limit(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345"))) as client:
            with patch("job_board_mcp.sources.validate_public_dns", AsyncMock()), patch("job_board_mcp.sources.MAX_BYTES", 4):
                with self.assertRaisesRegex(SourceUnavailable, "body limit"):
                    await safe_get("https://jobs.lever.co/example", client=client)

    async def test_dns_private_or_mixed_addresses_rejected(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("socket.getaddrinfo", return_value=answers):
            with self.assertRaisesRegex(SourceUnavailable, "non-public"):
                await validate_public_dns("jobs.lever.co")

    def test_only_exact_trusted_https_hosts(self):
        for url in ["http://jobs.lever.co/example", "https://jobs.lever.co.evil.test/a", "https://127.0.0.1/", "https://jobs.lever.co:444/a", "https://user:pass@jobs.lever.co/a"]:
            with self.assertRaises(SourceUnavailable, msg=url):
                validate_url_shape(url)

    def test_default_catalog_has_manual_sources(self):
        self.assertTrue(any(x.get("adapter") == "manual" for x in load_sources()))

    def test_source_tool_names_are_configurable_and_validated(self):
        self.assertEqual(source_tool_name({"id": "my-board"}), "search_my-board")
        self.assertEqual(
            source_tool_name({"id": "my-board", "tool_name": "search_custom"}),
            "search_custom",
        )
        with self.assertRaisesRegex(ValueError, "tool_name"):
            source_tool_name({"id": "my-board", "tool_name": "bad name"})


if __name__ == "__main__":
    unittest.main()
