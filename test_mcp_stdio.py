"""Local stdio MCP test — initialization, tool discovery, and every tool call.

Run with:
    .venv_new/bin/python test_mcp_stdio.py

The test spins up run_mcp_server.py as a child process over stdin/stdout and
drives it with the MCP Python SDK client. It requires REGULATIONS_API_KEY to be
set in .env (or the environment) for the live tool calls to succeed.
"""

import asyncio
import os
import sys
from pathlib import Path

# Ensure we can find the mcp package inside the project venv regardless of how
# this script is invoked.
VENV_SITE = Path(__file__).parent / ".venv_new" / "lib"
for p in VENV_SITE.glob("python*/site-packages"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = str(Path(__file__).parent / "run_mcp_server.py")
PYTHON = str(Path(__file__).parent / ".venv_new" / "bin" / "python")

# ── helpers ──────────────────────────────────────────────────────────────────

PASS = "\033[32m✔\033[0m"
FAIL = "\033[31m✘\033[0m"

def ok(msg: str) -> None:
    print(f"  {PASS}  {msg}")

def fail(msg: str) -> None:
    print(f"  {FAIL}  {msg}")
    raise SystemExit(1)


# ── test cases ────────────────────────────────────────────────────────────────

async def run_tests() -> None:
    server_params = StdioServerParameters(
        command=PYTHON,
        args=[SERVER_SCRIPT],
        env={**os.environ, "PYTHONPATH": str(Path(SERVER_SCRIPT).parent)},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:

            # ── 1. Initialization ─────────────────────────────────────────
            print("\n── 1. Initialization ──────────────────────────────────────")
            result = await session.initialize()
            server_name = result.server_info.name
            print(f"     server name : {server_name}")
            print(f"     protocol    : {result.protocol_version}")
            if server_name != "federal-rulemaking-read":
                fail(f"unexpected server name: {server_name}")
            ok("server handshake complete")

            # ── 2. Tool discovery ──────────────────────────────────────────
            print("\n── 2. Tool discovery ──────────────────────────────────────")
            tools_result = await session.list_tools()
            tools = {t.name: t for t in tools_result.tools}
            print(f"     tools found : {sorted(tools.keys())}")

            expected = {
                "search_rulemaking_documents",
                "get_document_details",
                "get_docket_details",
                "get_comment_requirements",
            }
            missing = expected - set(tools.keys())
            if missing:
                fail(f"tools missing from server: {missing}")
            ok(f"all {len(expected)} expected tools present")

            # Verify each tool has a description
            for name in expected:
                desc = tools[name].description or ""
                if not desc.strip():
                    fail(f"tool '{name}' has no description")
            ok("all tools have descriptions")

            # ── 3. Tool calls ──────────────────────────────────────────────
            print("\n── 3. Tool calls ──────────────────────────────────────────")

            api_key = os.getenv("REGULATIONS_API_KEY", "")
            if not api_key:
                print("     ⚠  REGULATIONS_API_KEY not set — "
                      "tool calls will return error messages (schema still verified)")

            # 3a. search_rulemaking_documents ─────────────────────────────
            print("\n  3a. search_rulemaking_documents")
            r = await session.call_tool(
                "search_rulemaking_documents",
                {"search_term": "clean air act", "page_size": 5},
            )
            content = r.content[0].text if r.content else ""
            import json
            payload = json.loads(content)
            if "total_found" not in payload:
                fail(f"missing 'total_found' key in response: {payload}")
            ok(f"response has 'total_found': {payload['total_found']}")

            # 3b. get_document_details — invalid ID → validation error ─────
            print("\n  3b. get_document_details (invalid ID → validation error)")
            r = await session.call_tool(
                "get_document_details",
                {"document_id": "BAD"},
            )
            payload = json.loads(r.content[0].text)
            if "message" not in payload:
                fail(f"expected 'message' key for invalid ID: {payload}")
            ok(f"validation error surfaced correctly: {payload['message'][:80]}")

            # 3c. get_document_details — well-formed ID ────────────────────
            print("\n  3c. get_document_details (well-formed ID)")
            r = await session.call_tool(
                "get_document_details",
                {"document_id": "EERE-2022-BT-TP-0028-0021"},
            )
            payload = json.loads(r.content[0].text)
            if "document_id" not in payload and "message" not in payload:
                fail(f"unexpected response shape: {payload}")
            ok(f"document detail: {payload.get('title', payload.get('message',''))[:80]}")

            # 3d. get_docket_details — well-formed docket ID ───────────────
            print("\n  3d. get_docket_details (well-formed ID)")
            r = await session.call_tool(
                "get_docket_details",
                {"docket_id": "EERE-2022-BT-TP-0028"},
            )
            payload = json.loads(r.content[0].text)
            if "docket_id" not in payload and "message" not in payload:
                fail(f"unexpected response shape: {payload}")
            ok(f"docket detail: {payload.get('title', payload.get('message',''))[:80]}")

            # 3e. get_comment_requirements ─────────────────────────────────
            print("\n  3e. get_comment_requirements (well-formed ID)")
            r = await session.call_tool(
                "get_comment_requirements",
                {"document_id": "EERE-2022-BT-TP-0028-0021"},
            )
            payload = json.loads(r.content[0].text)
            if ("status" not in payload and "accepting_comments" not in payload
                    and "message" not in payload):
                fail(f"unexpected response shape: {payload}")
            ok(f"comment status: {payload.get('status', payload.get('message',''))[:80]}")

    print("\n\033[32m══ All tests passed ══\033[0m\n")


if __name__ == "__main__":
    asyncio.run(run_tests())
