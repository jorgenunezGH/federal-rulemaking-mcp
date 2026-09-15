"""Test whether stubbing pywin32 allows the MCP SDK to import.

The SDK's stdio transport imports pywin32 at module level, and that import
fires even when an HTTP transport is intended. This inserts placeholder
modules into sys.modules first, so the import resolves without loading the
blocked compiled extensions.

This is a diagnostic probe, not a recommended configuration. If the SDK
calls into pywin32 at runtime rather than only importing it, stdio transport
will fail — but HTTP transports may still work.

Run: python test_stub_pywin32.py
"""

import sys
from types import ModuleType


def stub(name: str, attrs: dict | None = None) -> None:
    """Insert a placeholder module so `import name` resolves."""
    mod = ModuleType(name)
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    sys.modules[name] = mod


# Stub the compiled modules that cannot load on this workstation.
stub("_win32sysloader")
stub("win32job", {
    "CreateJobObject": lambda *a, **k: None,
    "AssignProcessToJobObject": lambda *a, **k: None,
    "SetInformationJobObject": lambda *a, **k: None,
})
stub("win32api", {"GetCurrentProcess": lambda: None})
stub("win32con", {})
stub("pywintypes", {"error": Exception})

print("Stubs installed. Attempting SDK import...\n")

try:
    from mcp.server import MCPServer
    print("SUCCESS: MCPServer imported with stubs in place.")
    print(f"  {MCPServer}")
except ImportError as e:
    print(f"FAILED at import: {e}")
    raise SystemExit(1)

print("\nAttempting to instantiate a server...\n")

try:
    server = MCPServer("stub-test")
    print(f"SUCCESS: instantiated {server}")
except Exception as e:  # noqa: BLE001 - diagnostic probe
    print(f"FAILED at instantiation: {type(e).__name__}: {e}")
    raise SystemExit(1)

print("\nAttempting to register a tool...\n")

try:
    @server.tool()
    def probe(value: str) -> dict:
        """Diagnostic tool to confirm registration works."""
        return {"echo": value}

    print("SUCCESS: tool registered.")
except Exception as e:  # noqa: BLE001 - diagnostic probe
    print(f"FAILED at registration: {type(e).__name__}: {e}")
    raise SystemExit(1)

print("\nImport, instantiation, and registration all succeeded.")
print("HTTP transport may be viable. stdio will not work.")
