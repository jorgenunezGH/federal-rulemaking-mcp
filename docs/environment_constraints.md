# Environment Constraints — Managed Federal Workstation

**Project:** Federal Rulemaking Read MCP Server
**Author:** Jorge Nunez
**Last updated:** 2026-08-27

This document records development environment findings on a managed
GSA Windows 11 workstation. It is included because these constraints
are relevant to any federal team attempting to build or run MCP servers
locally.

---

## Environment

| Item | Value |
| --- | --- |
| Operating system | Windows 11 (managed federal workstation) |
| Shell | PowerShell |
| Python | 3.14.5 |
| Virtual environment | `.venv` created via `python -m venv` |
| MCP Python SDK | 2.0.0 |
| Editor | Visual Studio Code |
| Project path tested | `C:\Users\jorgenunez\Documents\GitHub\federal-rulemaking-mcp` |
| Alternate path tested | `C:\dev\federal-rulemaking-mcp` |

---

## What Works

The following are verified working on this workstation:

| Capability | Command | Result |
| --- | --- | --- |
| Virtual environment activation | `.\.venv\Scripts\Activate.ps1` | Works after setting execution policy |
| Package installation | `python -m pip install -r requirements.txt` | Works |
| Offline unit tests | `python -m pytest -v` | 4/4 passing |
| Linting | `python -m ruff check src/` | Works |
| Live API calls | `python smoke_test.py` | Passing against Regulations.gov |
| Compiled extensions (some) | `pydantic_core`, `rpds_py`, `charset_normalizer` | Load successfully |

The Regulations.gov API client, Pydantic models, input validation, and
evaluation design are fully functional and independent of the MCP runtime.

---

## Constraint 1 — PowerShell Execution Policy

**Symptom:** Virtual environment activation script will not run by default.

**Workaround:**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Scoped to the current process only. Must be repeated in new terminal
sessions.

**Status:** Resolved via workaround.

---

## Constraint 2 — Package Executable Shims Blocked

**Symptom:** Generated `.exe` launchers in `.venv\Scripts\` fail to run.

```text
Program 'pytest.exe' failed to run: Access is denied
Program 'mcp.exe' failed to run: Access is denied
```

**Workaround:** Invoke packages through the Python module loader instead
of the generated executables.

| Instead of | Use |
| --- | --- |
| `pytest -v` | `python -m pytest -v` |
| `ruff check .` | `python -m ruff check .` |
| `pip install X` | `python -m pip install X` |

**Status:** Resolved via workaround. Documented in `README.md`.

---

## Constraint 3 — `cryptography` Compiled Extension Load Denied

**Symptom:** MCP SDK import failed when loading the `cryptography`
dependency.

```text
ImportError: DLL load failed while importing _rust: Access is denied.
```

**Diagnostic note:** `import cryptography` succeeds and is therefore
misleading — it does not load the compiled binding. The accurate test is:

```powershell
python -c "from cryptography.hazmat.bindings._rust import exceptions; print('OK')"
```

### Hypotheses tested

| Hypothesis | Test performed | Result |
| --- | --- | --- |
| OneDrive folder redirection | Checked `MyDocuments` path; checked for OneDrive process | Not the cause — path is local, OneDrive not running |
| OneDrive placeholder file | Checked file attributes | Not the cause — attributes were `Archive` only |
| File truncated or stubbed by AV | Checked file size | Not the cause — 9,943,040 bytes, intact |
| NTFS permission denial | `Get-Acl` on `_rust.pyd` | Not the cause — user has FullControl and ReadAndExecute |
| Defender quarantine | `Get-MpThreatDetection` | Not the cause — no detections logged |
| Mark-of-the-Web | `Unblock-File` on all `.pyd` and `.dll` recursively | Not the cause |
| Corrupt download | `pip install --force-reinstall --no-cache-dir` (multiple attempts) | Not the cause |
| Version-specific defect | Tested `cryptography` 50.0.1 and 45.0.7 | Not the cause |
| Path-based policy | Reproduced in both `C:\Users\...` and `C:\dev\...` | Not the cause |

### Outcome

The restriction stopped occurring later in the same working session,
without any further reinstallation or configuration change. Subsequent
imports proceeded past `cryptography` into deeper layers of the MCP SDK.

### Assessment

The most plausible explanation is that endpoint protection software
holds a temporary lock on newly written compiled binaries while
scanning, returning `Access is denied` to load attempts until the scan
completes. Repeated `--force-reinstall` operations appear to have reset
this window each time. This has not been confirmed with IT.

**Status:** No longer reproducing. Cause not formally confirmed.

---

## Constraint 4 — `pywin32` Compiled Extension Load Denied (Current Blocker)

**Symptom:** MCP server startup fails when the SDK imports its Windows
stdio support layer.

```powershell
python src/server.py
```

```text
ImportError: DLL load failed while importing _win32sysloader:
Access is denied.
```

### Import chain to failure point

```text
mcp.server
  mcp.server.mcpserver
    mcp.server.mcpserver.server
      mcp.server.stdio
        mcp.os.win32.utilities
          pywintypes
            _win32sysloader   <- Access is denied
```

### Remediation attempted

| Attempt | Command | Result |
| --- | --- | --- |
| Clear Mark-of-the-Web | `Get-ChildItem -Recurse .venv -Include *.pyd,*.dll \| Unblock-File` | No change |
| Force reinstall | `python -m pip install --force-reinstall --no-cache-dir pywin32` | No change |
| Run post-install script | `python .venv\Scripts\pywin32_postinstall.py -install -silent` | Failed — script itself requires `win32api`, which is also denied |
| Place system DLLs alongside bindings | Copied `pywin32_system32\*.dll` into `win32\lib\` | No change |
| Wait for scan lock to clear | Retried after delay | No change as of last test |

`pywin32_system32` contains `pythoncom314.dll` and `pywintypes314.dll`,
both present and intact.

**Status:** Open. IT ticket submitted.

---

## Constraint 5 — Node.js Not Installed

**Symptom:**

```text
node : The term 'node' is not recognized as the name of a cmdlet...
```

**Impact:** The MCP Inspector cannot be run locally. Inspector is the
standard interactive tool for listing and exercising MCP tools during
development.

**Mitigation:** A pure-Python verification path was built instead:

- `python -m pytest -v` — offline input validation tests
- `python smoke_test.py` — live end-to-end client and model verification

**Status:** Open. IT request submitted for Node.js LTS.

---

## Constraint 6 — Git Not Installed

**Symptom:**

```text
git : The term 'git' is not recognized as the name of a cmdlet...
```

**Impact:** Repository cannot be initialized or version-controlled yet.
`.gitignore` has been prepared in advance.

**Status:** Approved by IT; installation pending.

---

## Key Discriminator

Not all compiled extensions are affected. The following load successfully
from the same virtual environment:

| Package | Type | Loads? |
| --- | --- | --- |
| `pydantic_core` | Compiled Rust extension | Yes |
| `rpds_py` | Compiled Rust extension | Yes |
| `charset_normalizer` | Compiled C extension | Yes |
| `cryptography._rust` | Compiled Rust extension | Initially denied, later succeeded |
| `pywin32._win32sysloader` | Compiled Windows extension | Denied |

This indicates the restriction is not a blanket prohibition on compiled
extensions in user-profile virtual environments. Specific binaries are
affected while others load normally.

---

## Impact on the Project

| Area | Impact |
| --- | --- |
| API feasibility | None — fully verified |
| Regulations.gov client | None — working against live API |
| Pydantic input/output models | None — validating real payloads |
| Input validation tests | None — 4/4 passing offline |
| Live end-to-end verification | None — smoke test passing |
| Tool contract design | None — documented |
| Evaluation scenario design | None — documented |
| Governance documentation | None — documented |
| MCP server runtime startup | Blocked |
| MCP Inspector interactive testing | Blocked |
| Version control | Blocked until Git is installed |

The core logic of this project was deliberately decoupled from the MCP
SDK so that development could continue while these constraints are
resolved.

---

## Workaround Pattern in Use

```powershell
# Activate environment
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install -r requirements.txt

# Run offline validation tests
python -m pytest -v

# Run live API verification
python smoke_test.py

# Lint
python -m ruff check src/
```

Avoid the generated `.exe` shims in `.venv\Scripts\`.

---

### Transport alternatives investigated

To determine whether an alternative transport avoids the blocked
dependency, SDK source files were inspected as text rather than imported,
using `inspect_sdk_transports.py`.

**Finding:** of the SDK's server-side modules, only `mcp/server/stdio.py`
references the Windows-specific chain (`mcp.os.win32`). The HTTP transports
— `sse.py`, `streamable_http.py`, and `streamable_http_manager.py` — contain
no such reference.

**Why that does not resolve the blocker:** `mcp/server/mcpserver/server.py`
imports `stdio` at module level. Because `mcp/server/__init__.py` imports
`MCPServer` from `.mcpserver`, any import of `mcp.server` triggers the stdio
import chain before a transport is selected. Transport choice is a runtime
decision; the failing import happens earlier.

**Implication for federal deployment:** a team blocked on pywin32 cannot
work around it by preferring HTTP transport. The dependency is structural to
the package import, not conditional on configuration. A Linux environment or
an IT policy exception is required.

## Outstanding IT Requests

| Request | Status |
| --- | --- |
| Git installation | Approved; installation pending |
| Node.js LTS installation | Requested |
| Review of blocked compiled extension loads (`pywin32`) | Requested |
| Exclusion for Python virtual environments in an approved development directory | Requested |

---

## Relevance to Federal MCP Adoption

These findings are recorded because they are likely to affect other
federal participants:

1. The official MCP Python SDK depends on compiled extensions
   (`cryptography`, `pywin32` on Windows) that may be restricted on
   managed federal workstations.

2. Failures surface as `Access is denied` during DLL load, which can be
   mistaken for a code defect rather than an environment restriction.

3. `import cryptography` succeeding is misleading; only importing the
   compiled submodule reveals the restriction.

4. Package `.exe` shims may be blocked while `python -m <tool>` works.

5. MCP Inspector requires Node.js, which may not be present by default.

6. Teams should expect to need either an IT policy exception or a hosted
   development environment to validate MCP runtime behavior.

These constraints support the value of the hackathon's hosted
environment track for participants on managed workstations.

---

## Verification Commands

To reproduce the current blocker:

```powershell
# Confirm cryptography compiled binding loads
python -c "from cryptography.hazmat.bindings._rust import exceptions; print('cryptography OK')"

# Confirm pywin32 compiled binding loads
python -c "import _win32sysloader; print('pywin32 OK')"

# Attempt MCP SDK import
python -c "from mcp.server import MCPServer; print('MCPServer OK')"

# Attempt server startup
python src/server.py
```

---

## Summary

| Constraint | Status |
| --- | --- |
| PowerShell execution policy | Resolved via process-scoped workaround |
| Package `.exe` shims blocked | Resolved via `python -m` invocation |
| `cryptography` compiled extension | No longer reproducing; cause unconfirmed |
| `pywin32` compiled extension | Open — current blocker |
| Node.js not installed | Open — requested |
| Git not installed | Open — approved, pending install |

Remaining blockers are environmental. They do not reflect defects in the
API integration, tool design, validation logic, or evaluation approach.
