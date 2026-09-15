"""Determine whether the MCP SDK offers a transport that avoids pywin32.

Reads SDK source files as text rather than importing them, since the
import chain is what fails on this workstation. Reports which modules
reference the Windows-specific dependency and which entry points might
avoid it.

Run: python inspect_sdk_transports.py
"""

from pathlib import Path

SDK_ROOT = Path(".venv/Lib/site-packages/mcp")

# Markers indicating a module pulls in the Windows-specific chain.
WINDOWS_MARKERS = ("pywintypes", "win32job", "_win32sysloader", "os.win32")


def scan() -> None:
    if not SDK_ROOT.exists():
        print(f"SDK not found at {SDK_ROOT}")
        return

    print("=== Server-side modules and their Windows dependencies ===\n")

    server_files = sorted(SDK_ROOT.glob("server/**/*.py"))
    for path in server_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = [m for m in WINDOWS_MARKERS if m in text]
        rel = path.relative_to(SDK_ROOT.parent)
        flag = "WINDOWS DEP" if hits else "clean"
        print(f"  [{flag:<11}] {rel}")
        if hits:
            print(f"                {', '.join(hits)}")

    print("\n=== Available transport modules ===\n")
    for path in server_files:
        name = path.stem
        if any(k in name for k in ("stdio", "sse", "http", "streamable", "websocket")):
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = [m for m in WINDOWS_MARKERS if m in text]
            status = "requires pywin32" if hits else "no pywin32 reference"
            print(f"  {path.relative_to(SDK_ROOT.parent)}  ->  {status}")

    print("\n=== What mcp/server/__init__.py imports at module level ===\n")
    init = SDK_ROOT / "server" / "__init__.py"
    if init.exists():
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(("import ", "from ")):
                print(f"  {line.strip()}")

    print("\n=== Done ===")


if __name__ == "__main__":
    scan()
