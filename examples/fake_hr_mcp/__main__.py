"""Fake HR API exposed as a stdio MCP server (test fixture)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_SALARIES: dict[str, int] = {
    "Manuel Pernigotto": 62000,
    "Andrea Tuscano": 71000,
    "Maria Rossi": 55000,
}


def _build_server() -> Server:
    server = Server("fake-hr-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_salary",
                description="Return the annual gross salary in EUR for a given full name.",
                inputSchema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name != "get_salary":
            raise ValueError(f"unknown tool: {name}")
        person = arguments["name"]
        salary = _SALARIES.get(person, 0)
        return [TextContent(type="text", text=json.dumps({"name": person, "salary": salary}))]

    return server


async def _amain() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
