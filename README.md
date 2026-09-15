# Federal Rulemaking Read MCP Server

**Repository:** https://github.com/jorgenunezGH/federal-rulemaking-mcp

A Model Context Protocol (MCP) server that gives AI agents governed,
read-only access to federal rulemaking data from the Regulations.gov API.

**Hackathon Track:** Service "Read" Integration
**Data Source:** [Regulations.gov API v4](https://open.gsa.gov/api/regulationsgov/)

---

## Purpose

Program analysts in the Public Buildings Service evaluate proposed
regulations for their impact on federal buildings, energy standards, and
real property. This server lets an AI agent retrieve authoritative
rulemaking documents, docket context, and comment period status on demand,
with a source citation on every result.

A single topic search on Regulations.gov returns nearly 69,000 documents.
Determining which are still open for comment requires reconciling several
API fields that can appear to contradict one another.

See [`docs/use_case.md`](docs/use_case.md) for the full use case.

---

## Tools

| Tool | Analyst question it answers |
| --- | --- |
| `search_rulemaking_documents` | What rules exist on this topic? |
| `get_document_details` | What does this specific filing say? |
| `get_docket_details` | Is this an active rulemaking or a one-off notice? |
| `get_comment_requirements` | Can we still comment, and by when? |

Full input and output contracts, including failure modes:
[`docs/tool_contracts.md`](docs/tool_contracts.md)

---

## The Problem This Solves

Consider document `FDA-2021-N-0270-0001`. The API reports:

```text
openForComment:       true
withinCommentPeriod:  false
allowLateComments:    true
commentEndDate:       2021-05-27
```

A thin API wrapper would pass `openForComment: true` to the agent. The
comment window closed over 1,900 days ago. It reads as open only because
late comments are permitted.

`get_comment_requirements` resolves this:

```text
status:             closed_late_comments_accepted
days_since_closed:  1925
comment_url:        null
summary:            "The formal comment period closed on 2021-05-27
                (1,925 days ago). This document still accepts late
                     comments, but submissions fall outside the official
                     window."
```

There is no Regulations.gov endpoint that returns this. It is computed
from five fields with a documented precedence rule.

---

## Design Principles

- **Goal-oriented tools** — mapped to analyst tasks, not REST endpoints
- **Provenance always** — every result includes a `source_url`
- **Read-only** — only HTTP GET requests are issued
- **Fail closed** — validation before network calls; no partial results
- **Uncertainty stated** — contradictory data returns "could not be
  determined" rather than an inferred answer
- **Derived fields over raw vocabulary** — the agent should not need to
  know that `docketType: "Nonrulemaking"` is the negative case

---

## Architecture

Four layers, each testable in isolation:

```text
src/
├── clients/regulations_client.py   HTTP boundary
├── comment_status.py               analytical logic
├── models.py                       typed contracts
└── server.py                       MCP transport
```

The analytical logic is deliberately free of MCP dependencies. All 30
tests run without importing the transport layer, which also means the
status resolution rules can be reviewed without reading server plumbing.

This separation was forced by a workstation constraint (see
[`docs/environment_constraints.md`](docs/environment_constraints.md)) and
produced a better structure than the original design.

---

## Requirements

- Python 3.11 or later (developed on 3.14.5)
- A Regulations.gov API key from [api.data.gov/signup](https://api.data.gov/signup/)
- MCP Python SDK 2.0.0
- Node.js LTS — required only for the MCP Inspector

---

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your key:

```text
REGULATIONS_API_KEY=your_api_key_here
```

`.env` is excluded from version control and must never be committed.

---

## Running Tests

Offline unit tests, no network or MCP dependency:

```powershell
python -m pytest -v
```

Live API verification scripts:

```powershell
python demo.py                    # presentation walkthrough
python verify_document_tool.py    # document tool, 4 cases
python verify_docket_tool.py      # docket tool, 4 cases
python verify_comment_tool.py     # comment status, 7 cases
python measure_latency.py         # per-call latency
python api_probe.py               # initial feasibility probe
python find_open_comment_docs.py  # locate live open comment windows
```

> On managed Windows workstations, invoke tools through the Python module
> loader (`python -m pytest`) rather than the `.venv\Scripts\*.exe` shims.
> See [`docs/environment_constraints.md`](docs/environment_constraints.md).

---

## Verified Results

## Unit tests

```text
41 passed in 1.50s
```

All tests run offline. Upstream failure conditions — rate limiting, server
errors, timeouts, unauthorized responses — are verified by mocking the HTTP
layer rather than by exhausting the live 1,000-per-hour quota.

### Evaluation scenarios

| Measure | Result |
| --- | --- |
| Scenarios defined | 18 |
| Scenarios executed | 17 |
| Scenarios passed | 17 of 17 |
| Mean latency, live calls | 1.35s |
| Max latency, live calls | 2.34s |
| Rubric threshold | 5.00s |

## Comment status branches verified

| Status | Verified on |
| --- | --- |
| `open` | `FAA-2026-9967-0001` (live), `EERE-2022-BT-OT-0004-0001` (live) |
| `closed_late_comments_accepted` | `FDA-2021-N-0270-0001` (live) |
| `not_applicable` | Two EERE documents (live) |
| `not_yet_open` | Unit test, synthetic dates |
| `closed` | Unit test, synthetic dates |
| `withdrawn` | Unit test, synthetic dates |

A captured demo transcript is committed as `demo_output.txt`.

---

## MCP Runtime

The server is written for the MCP Python SDK v2.0 pattern:

```python
from mcp.server import MCPServer
```

The v1 `FastMCP` class and the `mcp.server.fastmcp` import path are not
used; both were removed in SDK v2.

Intended invocation:

```powershell
python src/server.py
```

On the current managed Windows workstation, MCP runtime startup is blocked
by a compiled-extension loading restriction in the `pywin32` dependency:

```text
ImportError: DLL load failed while importing win32job:
Access is denied.
```

The API client, models, analytical logic, and test suite all work
independently of the MCP transport layer. Runtime validation is pending IT
remediation, Node.js installation, or access to a hosted environment.

---

## Evaluation

| File | Purpose |
| --- | --- |
| [`evaluations/scenario_set.json`](evaluations/scenario_set.json) | Deterministic test scenarios |
| [`evaluations/rubric.md`](evaluations/rubric.md) | Pass/fail scoring criteria |
| [`evaluations/results.csv`](evaluations/results.csv) | Executed results with latency |

Scenarios cover successful search, agency filtering, empty results,
malformed input, document and docket retrieval, missing-record handling,
all comment status branches, rate limiting, and an end-to-end workflow.

One scenario remains unexecuted:

| Scenario | Reason |
| --- | --- |
| S13 end-to-end MCP flow | Requires MCP runtime; blocked by workstation constraint |

---

## Governance and Security

This server is read-only and does not determine organizational authority.
Authorization decisions remain with the calling system and the human
operator.

- Only HTTP GET requests are issued
- No persistence or caching beyond the request
- No personally identifiable information retrieved
- API key read from an environment variable; never logged
- Error messages truncate upstream responses to avoid leaking context
- Every result includes a `source_url` for provenance

Full detail, including the documented precedence decision on contradictory
API flags: [`docs/security_and_governance.md`](docs/security_and_governance.md)

---

## API Feasibility Summary

| Endpoint | Method | Result |
| --- | --- | --- |
| `/documents` | GET | Working |
| `/documents/{id}` | GET | Working |
| `/dockets/{id}` | GET | Working |
| `/comments` | POST | Not tested — deferred pending sandbox confirmation |

**Read path:** GO
**Write path:** Deferred / Conditional

Rate limit: 1,000 requests per hour.

Detail: [`docs/api_feasibility.md`](docs/api_feasibility.md)

---

## Project Structure

```text
federal-rulemaking-mcp/
├── README.md
├── requirements.txt
├── pytest.ini
├── .env.example
├── .gitignore
├── demo.py                        presentation walkthrough
├── demo_output.txt                captured transcript
├── api_probe.py                   initial feasibility probe
├── smoke_test.py                  client and model verification
├── measure_latency.py             per-call latency measurement
├── verify_document_tool.py
├── verify_docket_tool.py
├── verify_comment_tool.py
├── verify_empty_search.py
├── find_open_comment_docs.py
├── src/
│   ├── __init__.py
│   ├── models.py
│   ├── comment_status.py
│   ├── server.py
│   ├── clients/
│   │   ├── __init__.py
│   │   └── regulations_client.py
│   └── tools/
│       └── __init__.py
├── tests/
│   ├── test_search.py
│   ├── test_document_details.py
│   ├── test_docket_details.py
│   └── test_comment_requirements.py
├── docs/
│   ├── use_case.md
│   ├── tool_contracts.md
│   ├── api_feasibility.md
│   ├── security_and_governance.md
│   └── environment_constraints.md
└── evaluations/
    ├── scenario_set.json
    ├── rubric.md
    └── results.csv
```

---

## Deferred Write Integration

A Service "Write" Integration may be added only if a safe, non-production
submission environment is confirmed. Required controls before enabling any
write operation:

- Verified approval artifact
- Idempotency key to prevent duplicate submissions
- Correlation ID linking prepare, approve, submit, and receipt
- Auditable receipt for every accepted submission
- Fail-closed behavior if approval or monitoring is unavailable

**Current status:** Not implemented. **No API submissions were performed
at any point during this project.**

---

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/use_case.md`](docs/use_case.md) | Persona, workflow, organizational value |
| [`docs/tool_contracts.md`](docs/tool_contracts.md) | Inputs, outputs, failure modes, design notes |
| [`docs/api_feasibility.md`](docs/api_feasibility.md) | Endpoint tests and GO decision |
| [`docs/security_and_governance.md`](docs/security_and_governance.md) | Governance boundary and documented decisions |
| [`docs/environment_constraints.md`](docs/environment_constraints.md) | Managed-workstation diagnostics |
| [`docs/lessons_learned.md`](docs/lessons_learned.md) | What worked, what did not, recommendations |

---

## Status

| Component | Status |
| --- | --- |
| API feasibility (Read) | Verified |
| Regulations.gov client | Working against live API |
| Pydantic models | Validating real payloads |
| Comment status resolution | 6 branches verified |
| Tools implemented | 4 of 4 |
| Unit test suite | 41 passing |
| Evaluation scenarios | 17 of 18 executed, 17 of 17 passed |
| Local MCP runtime | Blocked — managed-workstation DLL restriction |
| MCP Inspector | Pending Node.js installation |
| Write integration | Deferred |

Remaining blockers are environmental, not design-related.
