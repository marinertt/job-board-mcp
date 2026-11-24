"""Reusable asynchronous MCP client; this module has no command-line entry point."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl


class MCPClient:
    """Manage a stdio MCP session and expose its protocol operations."""

    def __init__(self, server_parameters: StdioServerParameters) -> None:
        self.server_parameters = server_parameters
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def connect(self) -> MCPClient:
        """Start the configured server process and initialize an MCP session."""
        if self._session is not None:
            raise RuntimeError("MCPClient is already connected.")

        exit_stack = AsyncExitStack()
        try:
            read_stream, write_stream = await exit_stack.enter_async_context(
                stdio_client(self.server_parameters)
            )
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
        except BaseException:
            await exit_stack.aclose()
            raise

        self._exit_stack = exit_stack
        self._session = session
        return self

    async def close(self) -> None:
        """Close the MCP session and its stdio subprocess."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    async def __aenter__(self) -> MCPClient:
        return await self.connect()

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCPClient is not connected; use `async with` or call connect().")
        return self._session

    async def list_tools(self) -> Any:
        return await self.session.list_tools()

    async def list_resources(self) -> Any:
        return await self.session.list_resources()

    async def list_resource_templates(self) -> Any:
        return await self.session.list_resource_templates()

    async def list_prompts(self) -> Any:
        return await self.session.list_prompts()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self.session.call_tool(name, arguments=arguments or {})

    async def read_resource(self, uri: str | AnyUrl) -> Any:
        resource_uri = uri if isinstance(uri, AnyUrl) else AnyUrl(uri)
        return await self.session.read_resource(resource_uri)

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> Any:
        return await self.session.get_prompt(name, arguments=arguments or {})
