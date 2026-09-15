# Evaluation Document

**Server under test:** [Federal Rulemaking Read MCP Server](https://github.com/jorgenunezGH/federal-rulemaking-mcp)
**Track:** Service "Read" Integration
**Data source:** Regulations.gov API v4
**Author:** Jorge Nunez, PBS Office of Program & Project Management
**Date:** 2026-09-15

---

## 1. Overview and Objectives

The goal of this evaluation is to determine whether the server returns
accurate, well-formed responses; handles failure conditions gracefully;
resists hostile input; and resolves contradictory upstream data into answers
an analyst can act on.

The server exposes four read-only tools: `search_rulemaking_documents`,
`get_document_details`, `get_docket_details`, and
`get_comment_requirements`. The fourth is the analytical core — it collapses
five upstream comment status fields that can contradict one another into a
single documented status.

Evaluation proceeded in four layers:

| Layer | What it verifies | Network |
| --- | --- | --- |
| Unit tests | Input validation, date parsing, status precedence, error handling | None |
| Adversarial tests | Injection and traversal payloads rejected before any request | None |
| Mocked failure injection | Upstream 404, 403, 429, 500, timeout, credential non-leakage | None |
| Live verification | Real API responses across three agencies, plus full MCP protocol round trip | Yes |

**What this evaluation does not cover:** whether an LLM selects the correct
tool from the descriptions alone. Tool descriptions were verified as
transmitting in full through the protocol, but no model was placed in the
loop. Detailed in section 3.

---

## 2. Testing Methodology

### Framework and reproducibility

Tests run under pytest with `testpaths = tests` in `pytest.ini`. All unit
tests are offline and complete in under a second:

```powershell
python -m pytest -v
```

Live verification uses standalone scripts invoked directly, kept out of the
pytest suite so the test run neither depends on network availability nor
consumes API quota:

```powershell
python demo.py                    # analyst workflow walkthrough
python verify_document_tool.py    # document tool, 4 cases
python verify_docket_tool.py      # docket tool, 4 cases
python verify_comment_tool.py     # comment status, 7 cases
python verify_empty_search.py     # empty result handling
python measure_latency.py         # per-call latency
python api_probe.py               # initial endpoint feasibility probe
python find_open_comment_docs.py  # locate live open comment windows
```

Protocol verification requires two terminals:

```powershell
python run_server_http.py         # terminal 1: start the server
python verify_end_to_end.py       # terminal 2: connect a client
```

Linting via `python -m ruff check .` — clean.

Dependencies pinned in `requirements.txt`. Developed on Python 3.14.5 with
MCP Python SDK 2.0.0.

### Scenario set

Twenty-two scenarios are declared in `evaluations/scenario_set.json`, each
with concrete inputs, an expected outcome, and the metrics it exercises.

| Category | Count | Purpose |
| --- | --- | --- |
| happy_path | 5 | Correct retrieval with provenance |
| edge_case | 5 | Empty results, missing records, absent data |
| validation | 2 | Malformed input rejected before network calls |
| resilience | 6 | Rate limiting, server errors, timeouts, latency consistency |
| security | 3 | Adversarial input, bounded parameters, free-text boundary |
| integration | 1 | End-to-end MCP protocol flow |

Live scenarios use real document and docket identifiers across FDA, EERE, and
FAA — deliberately spanning agencies with different conventions, since
document ID segment counts and field presence vary by agency.

### Metrics

Six metrics defined in `evaluations/rubric.md`, scored PASS (1) or FAIL (0):

| Metric | Pass condition |
| --- | --- |
| task_success | Correct data returned for valid input |
| tool_selection_accuracy | Correct tool invoked from name and description alone |
| response_validity | Output matches the declared schema; required fields present |
| provenance_completeness | `source_url` present and correct in every result |
| graceful_failure | Clear message; no stack trace reaches the caller |
| latency | Under 5 seconds for a single-tool call |

### Test design rationale

Four failure modes matter most for a read-only server over authoritative
federal data:

**Returning wrong data.** Addressed by verifying against live records across
three agencies, and by testing the comment status precedence logic against
synthetic dates covering all six branches.

**Fabricating an answer when the source is ambiguous.** Addressed by
`test_contradictory_flags_reports_uncertainty`, which asserts the tool
returns "could not be determined" rather than inferring a status when the
upstream flags do not resolve.

**Leaking context through errors.** Addressed by
`test_rate_limit_error_does_not_leak_response_body`, which mocks a 429 whose
response body contains a credential-like string and asserts it does not
appear in the raised error.

**Accepting hostile input.** Addressed by 39 parameterized tests exercising
13 injection payloads against all three ID-taking tools.

### Time-dependent scenarios

Scenario S10 verifies the `open` comment status against a live document.
Comment windows close, so the specific identifier will not remain open
indefinitely.

| Document | Window closes | Verified |
| --- | --- | --- |
| EERE-2022-BT-OT-0004-0001 | 2026-09-09 | 2026-09-02 |
| FAA-2026-9967-0001 | 2026-10-17 | 2026-09-03 |

If S10 is re-run after a window closes, the tool will correctly report
`closed`. That is expected behavior, not a regression. The
`find_open_comment_docs.py` utility locates current open-period documents to
refresh the scenario. The `open` branch is additionally covered by
`test_open_period_reports_days_remaining`, which uses synthetic dates and is
therefore time-independent.

---

## 3. Performance Metrics

### Metrics tracked

1. **Scenario pass rate** — share of executed scenarios meeting all
   applicable metrics
2. **Unit test pass rate** — offline verification of validation, logic, error
   handling, and adversarial input rejection
3. **Latency** — per-call timing against a 5-second threshold
4. **Outcome consistency** — whether repeated calls produce identical results

### Unit test results

```text
85 passed in 0.49s
```

| Module | Tests | Covers |
| --- | --- | --- |
| `test_search.py` | 4 | Search input validation |
| `test_document_details.py` | 5 | Document ID pattern enforcement |
| `test_docket_details.py` | 6 | Docket ID pattern; segment count differs from documents |
| `test_comment_requirements.py` | 15 | All six status branches, date parsing, precedence |
| `test_rate_limit.py` | 8 | Mocked 429, 404, 403, 500; credential non-leakage |
| `test_tool_error_handling.py` | 3 | Validation ordering; timeout configuration |
| `test_adversarial_input.py` | 44 | 13 injection payloads across 3 tools; bounded parameters |

All offline. No network dependency, no MCP SDK import.

### Scenario results

| Measure | Result |
| --- | --- |
| Scenarios defined | 22 |
| Scenarios executed | 22 |
| Scenarios passed | 22 of 22 |
| Mean latency, live calls | 1.35s |
| Max latency, live calls | 2.34s |
| Threshold | 5.00s |

Validation and security scenarios record 0.00s because they never reach the
network. That is evidence of fail-closed ordering, not a measurement gap.

### Protocol verification

Scenario S13 was executed over streamable-http transport. An MCP client
connected, discovered all four tools, and invoked three of them.

| Verification | Result |
| --- | --- |
| Tools advertised | 4 |
| Tool descriptions received | Full multi-paragraph text including sequencing guidance |
| `search_rulemaking_documents` | Returned 69,013 matching documents |
| `get_comment_requirements` | Resolved to `closed_late_comments_accepted`, 1,937 days elapsed |
| `get_document_details` with malformed ID | Structured validation message, not a protocol error |
| Server request log | Ten successful `POST /mcp` round trips |

Transcript committed as `evaluations/end_to_end_output.txt`.

**Important qualification.** The workstation blocks the MCP SDK from loading
its `pywin32` dependency. Protocol verification required stubbing those
compiled modules in `sys.modules`. That confirmed the restriction is
import-time only and that the server and protocol work correctly. It is not a
supported deployment configuration — stubbing `win32job` disables process
lifecycle management whose absence was not evaluated. See
`docs/environment_constraints.md`.

### Adversarial input results

Thirteen payloads tested against three ID-taking tools, 39 combinations, all
rejected before any network call:

path traversal · Windows path traversal · traversal appended to a valid ID ·
SQL syntax · script tags · query parameter injection · page size injection ·
null byte · space substitution · JNDI lookup · header injection · absolute
URL substitution · 5,000-character string

Bounded parameters also verified: `page_size` values of 999999, -1, and 0 are
all rejected by Pydantic bounds.

### Failure analysis

**No scenario failed.** That warrants scrutiny rather than confidence, so the
following is an honest account of what the scenario set does and does not
stress.

**What the scenarios do stress:**

- Contradictory upstream data, where the correct answer requires overriding
  one API field with another
- Absent optional fields across three agencies with different conventions
- Six distinct comment status branches, including two unreachable with live
  data on demand
- Injection-style input against three separately validated ID patterns
- Five upstream failure conditions
- Full protocol round trip including error propagation

**What they do not stress:**

- Concurrent requests
- Sustained load approaching the 1,000-per-hour quota
- Malformed upstream responses, as opposed to error status codes
- Agent-level tool selection under ambiguity

**The most instructive near-failure** was not a scenario but a design question
surfaced while writing tests: when the API reports
`withinCommentPeriod: true` but the published end date has passed,
`days_remaining` computes negative. That would have shipped as
"-10 days remaining" had the test not exposed it. It is now clamped to zero
with a distinct message. Covered by
`test_within_period_flag_overrides_stale_end_date`.

### What was not measured

**Tool selection accuracy.** This metric requires an LLM agent choosing among
the four tools from their names and descriptions alone. Cells for this metric
are left **blank** in `results.csv` rather than scored.

An earlier draft scored them as passing, which was not defensible — no LLM
had selected anything. The tools were invoked directly.

**Partial progress.** Protocol verification confirmed the full descriptions
transmit to a client, including explicit sequencing guidance:

- `get_document_details` — "Use this after finding a document through search"
- `get_docket_details` — "Use this after get_document_details returns a docket_id"

So the disambiguating information verifiably reaches a model. Whether a model
acts on it correctly remains untested.

**No-tool baseline.** The hackathon evaluation guidance recommends comparing
agent performance with and without the MCP server to demonstrate lift. That
comparison requires an LLM client. A golden dataset of twelve stratified
question/answer pairs is prepared in `evaluations/golden_dataset.csv` so the
comparison can be run promptly if an LLM client becomes available.

---

## 4. Security Considerations

### Data classification

All data retrieved is **public** — published federal rulemaking documents and
dockets from Regulations.gov. No PII or CUI appears in queries, responses, or
logs.

The server requests document and docket metadata only. It does not retrieve
public comment submissions, which can contain submitter names and contact
information.

### Credential handling

Unlike a fully open API, Regulations.gov requires an API key. That creates a
real secret to protect.

| Control | Implementation |
| --- | --- |
| Storage | Environment variable via `.env`, excluded by `.gitignore` |
| Template | `.env.example` contains a placeholder only |
| Logging | Key never written to logs, responses, or error messages |
| Error messages | Rate limit message is a fixed string; other upstream bodies truncated to 200 characters |
| Verification | `test_rate_limit_error_does_not_leak_response_body` mocks a 429 containing a credential-like string and asserts it does not appear in the raised error |
| Repository scan | All tracked Python, Markdown, JSON, CSV, text, and INI files scanned for assigned key values; none found |

### Attack surface

| # | Surface | Risk | Mitigation |
| --- | --- | --- | --- |
| 1 | MCP protocol endpoint | Malformed or hostile tool input | Pydantic validation on every input; ID format patterns; bounded `page_size`; validation precedes any network call; 39 injection payloads verified rejected |
| 2 | Outbound HTTP requests | Server abused to reach unintended endpoints | Base URL hardcoded in `regulations_client.py`; no user-supplied URLs; path segments only from validated ID patterns |
| 3 | API key handling | Credential leaked through logs or errors | See credential handling above |
| 4 | Upstream response content | Malformed response propagating to the agent | Responses parsed into Pydantic output models; absent fields normalized; unresolvable flags return explicit uncertainty |
| 5 | Resource exhaustion | Unresponsive upstream hanging the agent | 10-second timeout on every request, verified by `test_client_timeout_is_configured` |

### Untrusted input and injection

Tool inputs originate from an LLM agent, which may itself be acting on
untrusted user text. Both are treated as untrusted.

**Search terms are deliberately not pattern-restricted.** `search_term`
accepts free text including punctuation. This is a documented decision, not
an oversight:

- The term is sent as a query parameter **value**, never a URL path segment
- Base URL and parameter names are fixed in the client
- `requests` handles encoding
- Restricting punctuation would break legitimate searches such as
  `section 508 & accessibility (2026)`

Covered by `test_search_term_with_injection_syntax_is_treated_as_text`, which
asserts the payload is preserved as data rather than rejected — documenting
where the boundary actually lies.

### Scope containment

The server cannot change state in any government system. Only HTTP GET is
issued. No POST, PUT, PATCH, or DELETE.

`get_comment_requirements` suppresses `comment_url` when comments cannot be
filed, so the agent is not handed a submission link for a closed window.

**Authorization is out of scope by design.** MCP exposes capabilities and
context; it does not determine who holds organizational authority. That
decision remains with the calling system and the human operator.

### Not applicable

| Concern | Why |
| --- | --- |
| Write operations | Only HTTP GET is issued |
| Data confidentiality | All retrieved data is published federal information |
| Persistence | No caching or storage beyond the request |
| User data | No user identifiers collected, logged, or transmitted |

### Out of scope

This security model assumes:

- The server is used to access Regulations.gov data only, as designed
- Users trust Regulations.gov as the authoritative source
- Network egress to `api.regulations.gov` is permitted in the deployment
  environment
- The MCP client enforces whatever authorization the calling organization
  requires

### A note on the stubbed dependencies

Protocol verification required stubbing `win32job`, `win32api`, `win32con`,
`pywintypes`, and `_win32sysloader` in `sys.modules`. This warrants explicit
mention in a security section because it is a deliberate weakening of the
runtime environment.

`win32job` manages Windows process job objects. The SDK presumably uses it
for subprocess lifecycle management under stdio transport. Under HTTP
transport it appears unused, but that was inferred from a successful run
rather than verified by reading the SDK's call paths.

**This is acceptable for diagnostic verification and unacceptable for
production.** A deployment should use a Linux environment where the
dependency is not imported, or obtain an IT policy exception permitting the
compiled extension to load normally.

### Deferred write path controls

If a write integration is added, the following are required before any
submission capability is enabled:

- A verified approval artifact accompanying every write request
- Idempotency keys preventing duplicate submissions
- Correlation IDs linking prepare, approve, submit, and receipt
- An auditable receipt for every accepted submission
- Fail-closed behavior if approval or monitoring is unavailable

**No API submissions were performed at any point during this project.**

---

## 5. Lessons Learned and Recommendations

### What worked

**Designing the evaluation before the tool contracts.** Writing scenarios
first forced questions that would otherwise have been deferred. "Search
yields no results" made me decide whether an empty set is a failure or a
valid answer. "Comment requirements with no period" made me decide what to
return when the concept does not apply. Those became design decisions rather
than accidents.

**Decoupling analytical logic from the transport layer.** Comment status
resolution lives in `src/comment_status.py` with no MCP import. All 85 tests
run without loading the transport layer, which is why the substantive logic
remained verifiable through weeks when the MCP SDK could not start.

**Mocking upstream failures.** Eight tests inject 404, 403, 429, and 500
responses. They surfaced four behaviors already implemented but never
verified, including that a credential-like string in a 429 body does not
reach the caller.

**Testing the import-versus-runtime distinction.** The workstation blocker
looked like an environmental dead end for weeks. Determining that it fails at
import rather than at call time took fifteen minutes and unlocked full
protocol verification.

**Documenting decisions where source data is ambiguous.** The precedence rule
favoring the agency's flag over local date arithmetic is stated with its
rationale and encoded in a test. If someone reverses it, the test fails and
makes them justify the change.

### Limitations

**No agent-level evaluation.** The most significant gap. No LLM selected
among the tools or produced a natural-language answer. Tool-selection
accuracy is unmeasured, and no no-tool baseline exists to demonstrate lift.

**A 100% pass rate on 22 scenarios.** This warrants scrutiny. The scenarios
stress contradictory data, absent fields, injection input, and protocol error
propagation — but not concurrency, sustained load, or malformed upstream
responses. A more adversarial scenario set would likely produce failures worth
analyzing.

**Protocol verification used a weakened runtime.** The stubs that made S13
possible also mean the verification did not run against the SDK as shipped.

**Repeated-trial consistency is thin.** Latency was measured across live
calls, but not with a formal multi-trial harness reporting standard
deviation. Outcome consistency was observed rather than systematically
measured.

### Recommendations for other federal teams

**1. Keep analytical logic out of the transport module.**

Comment status resolution initially lived in `server.py`. When the MCP SDK
failed to import, the entire test suite failed to collect, because anything
importing the status logic inherited the transport dependency chain. Moving it
to its own module fixed that and produced better separation than the original
design.

Tool functions should validate input, delegate to logic elsewhere, and shape
the response. The substantive work stays testable when the protocol layer
cannot start.

**2. Determine whether a blocking dependency fails at import or at call time.**

This is the single most useful thing I learned. A compiled extension that
cannot load may still be stubbable if nothing calls into it during the code
path you need. Fifteen minutes of testing that distinction converted a
multi-week blocker into a completed scenario.

Related: test the exact submodule named in the traceback, not the top-level
package. `import cryptography` returned success while
`from cryptography.hazmat.bindings._rust import exceptions` reproduced the
failure. A diagnostic that can pass while the problem persists produces false
confidence and sends you down the wrong hypothesis.

**3. Read SDK source as text when you cannot import it.**

Inspecting `mcp/server/*.py` for references to the blocked dependency
revealed that only `stdio.py` requires it, and that `mcpserver/server.py`
imports stdio at module level — meaning transport choice cannot avoid the
problem. That finding shaped everything that followed and required no working
import.

**4. Mock upstream failures alongside the happy path.**

They are cheap, run offline, and convert implicit correctness into verified
correctness. One test now proves a security property that the governance
documentation had only asserted.

**5. Test adversarial input even for read-only servers.**

Tool inputs arrive from an LLM that may be acting on untrusted text.
Parameterized tests across a payload list cost little and document exactly
where your validation boundary sits — including where you deliberately chose
not to restrict, and why.

**6. Name what you did not measure.**

An earlier draft of `results.csv` scored tool-selection accuracy as passing.
No LLM had selected anything. The score measured nothing.

Leave unmeasured metrics blank and state why. A reader who compares your
methodology against your results will notice a metric the described procedure
could not have produced.

**7. Verify that changes landed before building on them.**

Three items in this project were believed complete but were not — an
adversarial test file that was never created, four scenario rows never added,
and a documentation file created but never populated. Each cost time to
discover downstream.

After any substantial change, run one cheap check that would fail if the
change had not landed. A test count, a row count, a `Test-Path`. This is the
same discipline as the previous recommendation applied to your own process
rather than your code.

**8. Expect an IT policy exception on managed federal workstations.**

The official MCP Python SDK depends on compiled Windows extensions that may
be blocked. Nine hypotheses were tested and ruled out across two filesystem
locations before concluding the cause was endpoint policy rather than
configuration.

| Recommendation | Reason |
| --- | --- |
| Request WSL2 early | The failing dependency is Windows-specific and not imported under Linux; the hackathon documentation recommends it |
| Invoke packages via `python -m <tool>` | Executable shims in the user profile may be blocked while the module loader works |
| Budget time for tooling requests | Git, Node.js, and a policy exception each took days to move through approval |

**9. Document time-dependent test data.**

Any scenario depending on live external state needs a note explaining what
will change and when. Otherwise a reviewer re-running your tests later draws
the wrong conclusion from correct behavior.

---

## Appendix: Artifacts

| Artifact | Location |
| --- | --- |
| Scenario definitions | `evaluations/scenario_set.json` |
| Scoring rubric | `evaluations/rubric.md` |
| Executed results | `evaluations/results.csv` |
| Protocol verification transcript | `evaluations/end_to_end_output.txt` |
| Golden dataset, prepared but unexecuted | `evaluations/golden_dataset.csv` |
| Demo transcript | `demo_output.txt` |
| Tool contracts | `docs/tool_contracts.md` |
| API feasibility assessment | `docs/api_feasibility.md` |
| Governance boundary | `docs/security_and_governance.md` |
| Environment diagnostics | `docs/environment_constraints.md` |
| Extended lessons learned | `docs/lessons_learned.md` |
