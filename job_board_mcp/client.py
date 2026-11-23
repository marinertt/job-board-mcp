"""A real MCP stdio client CLI, usable without an LLM provider account."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Job-board monitor MCP client.")
    parser.add_argument("--db", help="Local SQLite path passed to the child server.")
    parser.add_argument("--profile", help="Trusted local profile JSON path.")
    parser.add_argument("--sources", help="Trusted local sources JSON path.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover", help="List MCP tools, resources, templates, and prompts.")
    commands.add_parser("sources", help="List configured job sources.")
    search = commands.add_parser("search", help="Discover jobs from a supported public employer feed.")
    search.add_argument("source_id")
    search.add_argument("--query", default="")
    search.add_argument("--location", default="")
    search.add_argument("--search-stream", default="")
    jobs = commands.add_parser("jobs", help="Filter the saved audit; no network search.")
    for option in ("query", "location", "source-id", "availability", "region", "application-status"):
        jobs.add_argument("--" + option, default="")
    jobs.add_argument("--limit", type=int, default=100)
    verify = commands.add_parser("verify", help="Check the exact posting application state.")
    verify.add_argument("unique_key")
    commands.add_parser("queue", help="List records due for recheck.")
    promote = commands.add_parser("promote", help="Promote a fresh verified-open job after assessment.")
    promote.add_argument("unique_key")
    promote.add_argument("--region", required=True)
    promote.add_argument("--fit", choices=("strong", "good", "possible"), default="possible")
    promote.add_argument("--notes", default="")
    imp = commands.add_parser("import-hits", help="Read a local JSON array and audit its specific URLs.")
    imp.add_argument("file", type=Path)
    imp.add_argument("--search-stream", default="")
    export = commands.add_parser("export", help="Return CSV; optionally save to an explicit local path.")
    export.add_argument("--view", default="raw")
    export.add_argument("--output", type=Path)
    prompt = commands.add_parser("prompt", help="Render an MCP prompt without executing it.")
    prompt.add_argument("name")
    prompt.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE")
    resource = commands.add_parser("resource", help="Read an MCP resource URI.")
    resource.add_argument("uri")
    return parser


def _prompt_args(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected prompt argument KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError("Prompt argument key must not be empty.")
        result[key] = value
    return result


async def run(args: argparse.Namespace) -> tuple[Any, bool]:
    """Connect to a child server and return (payload, is_error)."""
    env = dict(os.environ)
    for name in ("db", "profile", "sources"):
        if getattr(args, name, None):
            env["JOB_MONITOR_" + name.upper()] = str(getattr(args, name))
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "job_board_mcp.server"],
        env=env,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if args.command == "discover":
                return {
                    "tools": (await session.list_tools()).model_dump(mode="json"),
                    "resources": (await session.list_resources()).model_dump(mode="json"),
                    "resource_templates": (await session.list_resource_templates()).model_dump(mode="json"),
                    "prompts": (await session.list_prompts()).model_dump(mode="json"),
                }, False
            if args.command == "prompt":
                result = await session.get_prompt(args.name, arguments=_prompt_args(args.arg))
                return result.model_dump(mode="json"), False
            if args.command == "resource":
                result = await session.read_resource(AnyUrl(args.uri))
                return result.model_dump(mode="json"), False

            tool_name: str
            arguments: dict[str, Any]
            if args.command == "sources":
                tool_name, arguments = "list_sources", {}
            elif args.command == "search":
                tool_name = "search_jobs"
                arguments = {
                    "source_id": args.source_id,
                    "query": args.query,
                    "location": args.location,
                    "search_stream": args.search_stream,
                }
            elif args.command == "jobs":
                tool_name = "filter_jobs"
                arguments = {
                    field: getattr(args, field)
                    for field in ("query", "location", "source_id", "availability", "region", "application_status", "limit")
                }
            elif args.command == "verify":
                tool_name, arguments = "check_posting", {"unique_key": args.unique_key}
            elif args.command == "queue":
                tool_name, arguments = "get_recheck_queue", {}
            elif args.command == "promote":
                tool_name, arguments = "promote_job", {
                    "unique_key": args.unique_key,
                    "region": args.region,
                    "overall_fit": args.fit,
                    "notes": args.notes,
                }
            elif args.command == "import-hits":
                hits = json.loads(args.file.read_text(encoding="utf-8"))
                if not isinstance(hits, list):
                    raise ValueError("import-hits file must contain a JSON array.")
                tool_name, arguments = "import_hits", {"hits": hits, "search_stream": args.search_stream}
            elif args.command == "export":
                tool_name, arguments = "export_jobs", {"view": args.view}
            else:
                raise ValueError(f"Unsupported command: {args.command}")

            result = await session.call_tool(tool_name, arguments=arguments)
            if args.command == "export" and not result.isError:
                text = "".join(block.text for block in result.content if block.type == "text")
                if args.output:
                    # Deliberately local, explicit, and non-overwriting: no server write-path tool.
                    with args.output.open("x", encoding="utf-8", newline="") as handle:
                        handle.write(text)
                    return {"output": str(args.output.resolve()), "view": args.view}, False
                return text, False
            return result.model_dump(mode="json"), bool(result.isError)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result, error = asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
    if isinstance(result, str) and args.command == "export":
        print(result, end="")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
