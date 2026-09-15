"""S13: end-to-end verification through an actual MCP client.

Connects to a running server over streamable-http, discovers the advertised
tools, and invokes each one. This exercises the full protocol path rather
than calling tool logic directly.

Requires run_server_http.py running in another terminal.

Run: python verify_end_to_end.py
"""

import asyncio
import sys
from types import ModuleType


def _stub(name: str, attrs: dict | None = None) -> None:
    """Insert a placeholder module so `import name` resolves."""
    mod = ModuleType(name)
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    sys.modules[name] = mod


# The client imports the same package tree as the server, so the same
# stubs are required. See docs/environment_constraints.md.
_stub("_win32sysloader")
_stub("win32job", {
    "CreateJobObject": lambda *a, **k: None,
    "AssignProcessToJobObject": lambda *a, **k: None,
    "SetInformationJobObject": lambda *a, **k: None,
})
_stub("win32api", {"GetCurrentProcess": lambda: None})
_stub("win32con", {})
_stub("pywintypes", {"error": Exception})

# Import must follow stub installation; the client imports the same package
# tree as the server and would otherwise trigger the blocked extension.
from mcp.client import Client

SERVER_URL = "http://127.0.0.1:8000/mcp"


async def main() -> None:
    print(f"Connecting to {SERVER_URL}\n")

    async with Client(SERVER_URL) as client:
        print("=== Tool discovery ===\n")
        result = await client.list_tools()

        # list_tools returns a ListToolsResult wrapper, not a bare list.
        tools = result.tools

        print(f"Server advertises {len(tools)} tools:\n")
        for tool in tools:
            print(f"  {tool.name}")
            print(f"  {'-' * len(tool.name)}")
            print(f"{tool.description}\n")

        print("=== Invoking search_rulemaking_documents ===\n")
        search = await client.call_tool(
            "search_rulemaking_documents",
            {"search_term": "federal buildings energy efficiency", "page_size": 5},
        )
        print(f"  Result type: {type(search).__name__}")
        for attr in ("content", "structuredContent", "structured_content"):
            value = getattr(search, attr, None)
            if value is not None:
                print(f"  {attr}: {str(value)[:400]}")
        print()

        print("=== Invoking get_comment_requirements ===\n")
        comment = await client.call_tool(
            "get_comment_requirements",
            {"document_id": "FDA-2021-N-0270-0001"},
        )
        for attr in ("content", "structuredContent", "structured_content"):
            value = getattr(comment, attr, None)
            if value is not None:
                print(f"  {attr}: {str(value)[:700]}")
        print()

        print("=== Invoking with a malformed ID (expect graceful handling) ===\n")
        bad = await client.call_tool(
            "get_document_details",
            {"document_id": "not a valid id !!!"},
        )
        for attr in ("content", "structuredContent", "structured_content"):
            value = getattr(bad, attr, None)
            if value is not None:
                print(f"  {attr}: {str(value)[:400]}")
        print()

        print("=== S13 complete ===")
        print("Tool discovery and invocation succeeded through the MCP protocol.")

if __name__ == "__main__":
    asyncio.run(main())
