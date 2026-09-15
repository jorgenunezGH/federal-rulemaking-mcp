"""Attempt to run the actual server over HTTP transport.

Installs stubs for the blocked pywin32 compiled extensions before importing
the SDK, then loads the real server module and reports what it found.

The stubs are a diagnostic workaround for a managed-workstation constraint,
not a supported configuration. See docs/environment_constraints.md.

Run: python run_server_http.py
"""

import sys
from types import ModuleType


def _stub(name: str, attrs: dict | None = None) -> None:
    """Insert a placeholder module so `import name` resolves."""
    mod = ModuleType(name)
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    sys.modules[name] = mod


# Must run before any mcp import. The SDK imports its stdio transport at
# package level, which pulls in these Windows-specific modules even when an
# HTTP transport is intended.

_stub("_win32sysloader")
_stub("win32job", {
    "CreateJobObject": lambda *a, **k: None,
    "AssignProcessToJobObject": lambda *a, **k: None,
    "SetInformationJobObject": lambda *a, **k: None,
})
_stub("win32api", {"GetCurrentProcess": lambda: None})
_stub("win32con", {})
_stub("pywintypes", {"error": Exception})

print("Stubs installed.\n")

from src.server import mcp

print(f"Server loaded: {mcp}\n")

print("Registered tools:")
for attr in ("_tool_manager", "_tools", "tools"):
    holder = getattr(mcp, attr, None)
    if holder is None:
        continue
    print(f"  (via {attr})")
    tools = getattr(holder, "_tools", holder)
    if isinstance(tools, dict):
        for name in tools:
            print(f"    {name}")
    else:
        print(f"    {tools}")
    break
else:
    print("  Could not locate the tool registry. Inspecting object:")
    print(f"    {[a for a in dir(mcp) if not a.startswith('__')]}")

print("\nAttempting streamable-http transport...")
print("Valid transports per SDK source: stdio, sse, streamable-http")
print("stdio is unavailable on this workstation.\n")

try:
    print("Starting on streamable-http. Press Ctrl+C to stop.\n")
    mcp.run(transport="streamable-http")
except KeyboardInterrupt:
    print("\nStopped by user. Transport works.")
except Exception as e:  # noqa: BLE001 - diagnostic probe
    print(f"\n{type(e).__name__}: {e}")
    print("\nFalling back to sse...\n")
    try:
        mcp.run(transport="sse")
    except KeyboardInterrupt:
        print("\nStopped by user. SSE transport works.")
    except Exception as inner:  # noqa: BLE001 - diagnostic probe
        print(f"{type(inner).__name__}: {inner}")