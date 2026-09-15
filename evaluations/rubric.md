# Evaluation Rubric — Federal Rulemaking Read MCP Server

Each scenario is scored against the applicable metrics below.
Score each metric: **PASS (1)** or **FAIL (0)**. A scenario passes
only if all its applicable metrics pass.

---

## Metrics

| Metric | Definition | Pass Condition |
| --- | --- | --- |
| **task_success** | Tool accomplishes its stated goal | Correct data returned for valid input |
| **tool_selection_accuracy** | Agent picks the right tool for the request | Correct tool invoked; no wrong-tool calls |
| **response_validity** | Output matches the defined schema | All required fields present; correct types |
| **provenance_completeness** | Source is always cited | source_url present and valid in every result |
| **graceful_failure** | Errors handled cleanly | Clear message; no crash/stack trace to user |
| **latency** | Response returns in reasonable time | ≤ 5 seconds for single-tool calls |

---

## Scoring

- **Scenario Pass Rate** = (scenarios passed) / (total scenarios)
- **Target for pilot readiness:** ≥ 90% pass rate
- **Critical scenarios** (must pass): S04, S06, S07, S09 (all failure
  handling) — a crash here is a blocking defect

---

## Notes

- Validation scenarios (S04, S07) must fail *before* any API call —
  confirms input guarding
- Provenance is scored on every happy-path scenario — a missing
  source_url is an automatic scenario fail
- The integration scenario (S13) is weighted highest for demo value and has
  been executed over streamable-http transport

## Note on Time-Dependent Scenarios

Scenario S10 verifies the OPEN comment status branch against a live
document. Comment windows close, so the specific document ID will not
remain open indefinitely.

| Document | Window closes |
| --- | --- |
| EERE-2022-BT-OT-0004-0001 | 2026-09-09 |
| FAA-2026-9967-0001 | 2026-10-17 |

Both were verified as `status=open` on 2026-09-02. If S10 is re-run after
a window closes, the tool will correctly report `closed` — this is
expected behavior, not a regression.

The `find_open_comment_docs.py` utility locates current open-period
documents to refresh this scenario. The OPEN branch is additionally
covered by `test_open_period_reports_days_remaining`, which uses
synthetic dates and is therefore time-independent.

### Note on tool_selection_accuracy

This metric requires an LLM agent choosing among the four tools based on
their names and descriptions alone. Measuring it requires a running MCP
server, which is blocked by the workstation constraint documented in
`environment_constraints.md`.

Scenarios in this set were executed by invoking tool logic directly.
Tool-selection accuracy is therefore **not measured**, and cells for that
metric are left blank rather than scored.

Mitigation applied at design time: tool names state the analyst question
they answer, and descriptions specify when to use each tool relative to the
others. Whether that is sufficient for unambiguous LLM selection remains
unverified.
