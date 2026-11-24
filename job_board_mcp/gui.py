"""Local interactive demo UI for the job-board MCP server."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit
import webbrowser

from mcp import StdioServerParameters

from .client import MCPClient
from .sources import load_sources


MAX_REQUEST_BYTES = 1_000_000
TOOL_TIMEOUT_SECONDS = 90
DEMO_FIXTURES = json.loads(
    files("job_board_mcp").joinpath("data/gui-demo.json").read_text(encoding="utf-8")
)["fixtures"]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class MCPBridge:
    """Keep one MCP stdio client/server session alive for GUI requests."""

    def __init__(self, database: Path, profile_path: Path, sources_path: Path) -> None:
        env = dict(os.environ)
        env.update({
            "JOB_MONITOR_DB": str(database),
            "JOB_MONITOR_PROFILE": str(profile_path),
            "JOB_MONITOR_SOURCES": str(sources_path),
        })
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from job_board_mcp.server import MCPServer; MCPServer().run()"],
            env=env,
            cwd=str(Path.cwd()),
        )
        self.client = MCPClient(parameters)
        self.loop = asyncio.new_event_loop()
        self.requests: asyncio.Queue | None = None
        self.ready = threading.Event()
        self.startup_error: BaseException | None = None
        self.thread = threading.Thread(target=self._serve_loop, name="job-board-mcp", daemon=True)
        self.thread.start()
        if not self.ready.wait(TOOL_TIMEOUT_SECONDS):
            raise TimeoutError("Timed out connecting the GUI to its MCP server.")
        if self.startup_error is not None:
            raise RuntimeError(f"Could not connect to MCP server: {self.startup_error}") from self.startup_error

    def _serve_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.requests = asyncio.Queue()
        self.loop.create_task(self._client_worker())
        self.loop.run_forever()
        self.loop.close()

    async def _client_worker(self) -> None:
        try:
            await self.client.connect()
        except BaseException as exc:
            self.startup_error = exc
            self.ready.set()
            self.loop.call_soon(self.loop.stop)
            return
        self.ready.set()
        try:
            while True:
                request = await self.requests.get()
                if request is None:
                    return
                operation, args, kwargs, result = request
                try:
                    method = getattr(self.client, operation)
                    result.set_result(await method(*args, **kwargs))
                except BaseException as exc:
                    result.set_exception(exc)
        finally:
            await self.client.close()
            self.loop.call_soon(self.loop.stop)

    def call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.client, operation, None)
        if method is None or operation.startswith("_"):
            raise ValueError(f"Unsupported MCP client operation: {operation}")
        del method
        result: Future = Future()
        self.loop.call_soon_threadsafe(
            self.requests.put_nowait, (operation, args, kwargs, result)
        )
        try:
            return result.result(timeout=TOOL_TIMEOUT_SECONDS)
        except FutureTimeout as exc:
            raise TimeoutError(f"MCP operation {operation} timed out.") from exc

    def close(self) -> None:
        if self.thread.is_alive() and self.loop.is_running() and self.requests is not None:
            self.loop.call_soon_threadsafe(self.requests.put_nowait, None)
            self.thread.join(timeout=TOOL_TIMEOUT_SECONDS)


class JobBoardDemo:
    """Manage private demo configuration and the MCP connection."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = (data_dir or Path.cwd() / ".local" / "job-board-demo").resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.data_dir / "profile.json"
        self.sources_path = self.data_dir / "sources.json"
        self.database_path = self.data_dir / "jobs.sqlite3"
        self._copy_default("profile.json", "profile.example.json")
        self._copy_default("sources.json", "sources.example.json")
        self.bridge = MCPBridge(self.database_path, self.profile_path, self.sources_path)

    def _copy_default(self, target: str, packaged: str) -> None:
        path = self.data_dir / target
        if not path.exists():
            path.write_bytes(files("job_board_mcp").joinpath("data", packaged).read_bytes())

    def save_configuration(self, profile: Any, sources: Any) -> None:
        if not isinstance(profile, dict) or not isinstance(profile.get("regions"), list):
            raise ValueError("Profile must be a JSON object with a regions array.")
        if not isinstance(sources, list) or not all(isinstance(item, dict) for item in sources):
            raise ValueError("Sources must be a JSON array of source objects.")
        if not isinstance(profile.get("discovery_days"), int) or profile["discovery_days"] < 1:
            raise ValueError("Profile discovery_days must be a positive integer.")
        profile_backup = self.profile_path.read_bytes()
        sources_backup = self.sources_path.read_bytes()
        self._atomic_json(self.profile_path, profile)
        self._atomic_json(self.sources_path, sources)
        old_bridge = self.bridge
        old_bridge.close()
        try:
            load_sources(str(self.sources_path))
            self.bridge = MCPBridge(self.database_path, self.profile_path, self.sources_path)
        except Exception:
            self.profile_path.write_bytes(profile_backup)
            self.sources_path.write_bytes(sources_backup)
            self.bridge = MCPBridge(self.database_path, self.profile_path, self.sources_path)
            raise

    @staticmethod
    def _atomic_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def state(self) -> dict[str, Any]:
        return {
            "profile": json.loads(self.profile_path.read_text(encoding="utf-8")),
            "sources": json.loads(self.sources_path.read_text(encoding="utf-8")),
            "tools": _jsonable(self.bridge.call("list_tools").tools),
            "resources": _jsonable(self.bridge.call("list_resources").resources),
            "resource_templates": _jsonable(
                self.bridge.call("list_resource_templates").resourceTemplates
            ),
            "prompts": _jsonable(self.bridge.call("list_prompts").prompts),
            "config_directory": str(self.data_dir),
        }

    def results(self, view: str) -> Any:
        if view not in {"raw", "Career Transition Jobs"}:
            profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
            if view not in profile.get("regions", []):
                raise ValueError("Unknown results tab.")
        arguments = {"limit": 1000}
        if view != "raw":
            arguments["region"] = view
        return self.bridge.call("call_tool", "filter_jobs", arguments)

    def close(self) -> None:
        self.bridge.close()


class DemoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], demo: JobBoardDemo) -> None:
        self.demo = demo
        super().__init__(address, DemoRequestHandler)


class DemoRequestHandler(BaseHTTPRequestHandler):
    server: DemoHTTPServer
    server_version = "JobBoardDemo/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[job-board-demo] {self.address_string()} {format % args}", file=sys.stderr)

    def _check_local_request(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost"}:
            self._send_json({"error": "Only local browser requests are allowed."}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin:
            origin_host = (urlsplit(origin).hostname or "").lower()
            if origin_host not in {"127.0.0.1", "localhost"}:
                self._send_json({"error": "Cross-origin requests are not allowed."}, 403)
                return False
        return True

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(_jsonable(payload), ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Any:
        length_text = self.headers.get("Content-Length", "")
        if not length_text.isdigit() or int(length_text) > MAX_REQUEST_BYTES:
            raise ValueError("Request body must be valid JSON under 1 MB.")
        return json.loads(self.rfile.read(int(length_text)))

    def do_GET(self) -> None:
        if not self._check_local_request():
            return
        parts = urlsplit(self.path)
        if parts.path == "/":
            page = files("job_board_mcp").joinpath("data/gui.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)
            return
        try:
            if parts.path == "/api/state":
                self._send_json(self.server.demo.state())
            elif parts.path == "/api/results":
                view = parse_qs(parts.query).get("view", ["raw"])[0]
                self._send_json(self.server.demo.results(view))
            elif parts.path == "/api/resource":
                uri = parse_qs(parts.query).get("uri", [""])[0]
                if not uri:
                    raise ValueError("Resource URI is required.")
                result = self.server.demo.bridge.call("read_resource", uri)
                self._send_json(result)
            else:
                self._send_json({"error": "Not found."}, 404)
        except (ValueError, KeyError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 502)

    def do_POST(self) -> None:
        if not self._check_local_request():
            return
        try:
            body = self._read_json()
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object.")
            if self.path == "/api/config":
                self.server.demo.save_configuration(body.get("profile"), body.get("sources"))
                self._send_json(self.server.demo.state())
            elif self.path == "/api/tool":
                name = body.get("name")
                arguments = body.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError("Tool name and arguments are required.")
                available = {tool["name"] for tool in self.server.demo.state()["tools"]}
                if name not in available:
                    raise ValueError("That tool is not registered by the active server.")
                result = self.server.demo.bridge.call("call_tool", name, arguments)
                self._send_json(result)
            elif self.path == "/api/prompt":
                name = body.get("name")
                arguments = body.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError("Prompt name and arguments are required.")
                result = self.server.demo.bridge.call("get_prompt", name, arguments)
                self._send_json(result)
            elif self.path == "/api/demo-seed":
                result = self.server.demo.bridge.call(
                    "call_tool",
                    "import_hits",
                    {"hits": DEMO_FIXTURES, "search_stream": "GUI demo fixtures (not live jobs)"},
                )
                self._send_json(result)
            else:
                self._send_json({"error": "Not found."}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 502)


def run_gui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Start the local demo GUI; only loopback binding is supported."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The demo GUI can only bind to localhost.")
    demo = JobBoardDemo()
    server = DemoHTTPServer((host, port), demo)
    url = f"http://{host}:{server.server_port}/"
    print(f"Job Board MCP demo: {url}", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        demo.close()


if __name__ == "__main__":
    run_gui()
