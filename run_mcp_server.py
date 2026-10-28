"""Entrypoint wrapper — runs the federal rulemaking MCP server.

This shim exists because src/server.py uses absolute ``src.*`` imports and
cannot be executed directly as a script.  Running via ``-m`` from the
package_root resolves the imports correctly.
"""

import sys
import os

# Make sure the package root (this file's directory) is on the path.
sys.path.insert(0, os.path.dirname(__file__))

from src.server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()
