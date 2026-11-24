"""Read-only public employer-board adapters.

Adapters deliberately return every fetched specific posting. Filtering happens only
after persistence in the server. Job-board APIs are discovery, not availability proof.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import ipaddress
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .models import JobHit


ALLOWED_HOSTS = frozenset({
    "boards-api.greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "api.lever.co", "api.eu.lever.co", "jobs.lever.co", "jobs.eu.lever.co",
})
MAX_BYTES = 4 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_SOURCES_PATH = files("job_board_mcp").joinpath("data/sources.example.json")
SUPPORTED_ADAPTERS = frozenset({"greenhouse", "lever"})


class SourceUnavailable(ValueError):
    """The source cannot be safely or completely fetched; no jobs were invented."""


def validate_url_shape(url: str) -> str:
    """Only fixed, trusted ATS hosts; no configurable arbitrary fetch endpoint."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SourceUnavailable("Invalid URL") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise SourceUnavailable("Only allowlisted public HTTPS ATS hosts are supported")
    if parsed.username or parsed.password or port not in (None, 443):
        raise SourceUnavailable("Credentials and nonstandard ports are not allowed")
    if "\\" in url or any(ord(c) < 32 for c in url):
        raise SourceUnavailable("Malformed URL")
    return host


async def validate_public_dns(host: str) -> None:
    """Reject private, loopback, link-local, reserved and mixed public/private DNS."""
    try:
        records = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourceUnavailable("ATS hostname could not be resolved") from exc
    if not records:
        raise SourceUnavailable("ATS hostname had no DNS answers")
    for record in records:
        ip = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        if not ip.is_global or (getattr(ip, "ipv4_mapped", None) and not ip.ipv4_mapped.is_global):
            raise SourceUnavailable("ATS hostname resolves to a non-public address")


async def safe_get(url: str, *, client: httpx.AsyncClient | None = None) -> httpx.Response:
    """Bounded GET with per-hop allowlist/DNS validation and proxies disabled.

    Fixed ATS hostnames are trusted infrastructure, not user-supplied domains.
    DNS is checked before every request; this is not a general web-fetch facility.
    No credential entry, POST, browser-session reuse, or challenge bypass is done.
    """
    if client is None:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                                     headers={"User-Agent": "job-board-mcp/0.1 (read-only public job audit)"}) as owned:
            return await safe_get(url, client=owned)
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        host = validate_url_shape(current)
        await validate_public_dns(host)
        async with client.stream("GET", current, follow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise SourceUnavailable("Redirect missing Location")
                target = urljoin(current, location)
                validate_url_shape(target)
                current = target
                continue
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > MAX_BYTES:
                raise SourceUnavailable("Response exceeds safe body limit")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise SourceUnavailable("Response exceeds safe body limit")
            # aiter_bytes has already decompressed the body. Do not ask httpx to
            # decode it a second time when constructing the buffered response.
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in ("content-encoding", "content-length")}
            return httpx.Response(response.status_code, headers=headers,
                                  content=bytes(data), request=httpx.Request("GET", current))
    raise SourceUnavailable("Too many redirects")


def load_sources(path: str | None = None) -> list[dict]:
    """Load configured sources; manual boards remain visible, not fake adapters."""
    explicit = path or os.environ.get("JOB_MONITOR_SOURCES")
    location = Path(explicit) if explicit else DEFAULT_SOURCES_PATH
    payload = json.loads(location.read_text(encoding="utf-8"))
    sources = payload.get("sources", []) if isinstance(payload, dict) else payload
    if not isinstance(sources, list) or not all(isinstance(x, dict) for x in sources):
        raise ValueError("Sources configuration must be an array or a sources array")
    identifiers = [str(x.get("id", "")) for x in sources]
    if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("Every source needs a unique nonempty id")
    registered_tools = [
        source_tool_name(source)
        for source in sources
        if source.get("enabled") is not False
        and source_adapter_name(source) in SUPPORTED_ADAPTERS
    ]
    if len(set(registered_tools)) != len(registered_tools):
        raise ValueError("Enabled source tools must have unique tool_name values")
    return sources


def source_adapter_name(source: dict) -> str:
    """Return the configured adapter type, defaulting to manual discovery."""
    return str(source.get("adapter") or source.get("type") or source.get("kind") or "manual").lower()


def source_tool_name(source: dict) -> str:
    """Return the configured MCP tool name or derive one from its source id."""
    configured = source.get("tool_name")
    if configured is None:
        source_id = str(source.get("id") or "")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", source_id).strip("_")
        if not slug:
            raise ValueError("Source id must contain characters usable in an MCP tool name")
        configured = f"search_{slug}"
    if not isinstance(configured, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_-]{0,63}", configured
    ):
        raise ValueError(
            "Source tool_name must be 1-64 letters, digits, underscores, or hyphens, starting with a letter"
        )
    return configured


def _token(source: dict) -> str:
    token = str(source.get("board_token") or source.get("board") or source.get("site") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", token):
        raise ValueError("Source must specify a valid employer board_token/board/site")
    return token


def _date(value: Any) -> str:
    if not value:
        return "Unknown"
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return "Unknown"
    return str(value)


def _text(value: Any) -> str:
    return re.sub(r"<[^>]+>", " ", html.unescape(str(value or ""))).strip()


async def _json(url: str) -> Any:
    response = await safe_get(url)
    if response.status_code != 200:
        raise SourceUnavailable(f"Employer board returned HTTP {response.status_code}; discovery unavailable")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceUnavailable("Employer board did not return valid JSON") from exc


async def search_source(source: dict, query: str = "", location: str = "") -> list[JobHit]:
    """Fetch ALL specific jobs from one configured employer board, without filtering.

    query/location intentionally do not constrain upstream collection: every fetched
    posting goes to the audit first. The MCP server applies result filters afterward.
    """
    kind = source_adapter_name(source)
    if source.get("enabled") is False:
        raise ValueError("This source is disabled")
    if kind not in SUPPORTED_ADAPTERS:
        raise ValueError("Manual discovery source: use its search prompt, then import specific posting URLs; no scraper is implemented")
    token = _token(source)
    source_id = str(source["id"])
    company = str(source.get("company") or source.get("name") or token)
    hits: list[JobHit] = []
    if kind == "greenhouse":
        payload = await _json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise SourceUnavailable("Unexpected Greenhouse board payload")
        for item in payload["jobs"]:
            if not isinstance(item, dict):
                continue
            # Prospect/general-interest posts are not specific vacancies.
            if item.get("internal_job_id") is None:
                continue
            identifier = str(item.get("id", ""))
            if not identifier.isdigit():
                continue
            url = str(item.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{identifier}")
            hits.append(JobHit(company=company, title=str(item.get("title", "Unknown")),
                location=str((item.get("location") or {}).get("name", "Unknown")),
                url=url, source_id=source_id, source_job_id=identifier,
                description=_text(item.get("content")), posting_date=_date(item.get("first_published")),
                posting_date_note="Greenhouse updated_at is not a posting date; retained as Unknown unless first_published is available.",
                application_url=f"https://job-boards.greenhouse.io/{token}/jobs/{identifier}",
                deadline=str(item.get("application_deadline") or "")))
    else:
        api_host = "api.eu.lever.co" if source.get("region") == "eu" else "api.lever.co"
        for skip in range(0, 5000, 100):
            payload = await _json(f"https://{api_host}/v0/postings/{token}?mode=json&limit=100&skip={skip}")
            if not isinstance(payload, list):
                raise SourceUnavailable("Unexpected Lever board payload")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                identifier = str(item.get("id", ""))
                if not re.fullmatch(r"[a-fA-F0-9-]{36}", identifier):
                    continue
                categories = item.get("categories") or {}
                hits.append(JobHit(company=company, title=str(item.get("text", "Unknown")),
                    location=str(categories.get("location") or "Unknown"),
                    url=str(item.get("hostedUrl") or f"https://jobs.lever.co/{token}/{identifier}"),
                    source_id=source_id, source_job_id=identifier,
                    description=_text(item.get("descriptionPlain") or item.get("description")),
                    posting_date=_date(item.get("createdAt")),
                    work_arrangement=str(item.get("workplaceType") or "Unknown"),
                    application_url=str(item.get("applyUrl") or "")))
            if len(payload) < 100:
                break
        else:
            raise SourceUnavailable("Lever source exceeded 5,000 postings; narrow by employer board rather than silently truncating")
    # Specific aliases are preserved by the store; exact duplicate URLs need not repeat.
    return list({hit.url: hit for hit in hits}.values())
