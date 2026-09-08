# Lessons Learned and Recommendations

**Project:** Federal Rulemaking Read MCP Server
**Author:** Jorge Nunez, Program Analyst, PBS Office of Program & Project Management
**Track:** Service "Read" Integration
**Date:** 2026-09-03

This document records what worked, what did not, and what I would tell
another federal team attempting the same thing.

---

## 1. A Workstation Constraint Produced a Better Architecture

### What happened

My first working version placed the comment status resolution logic inside
`server.py`, alongside the MCP tool definitions. That seemed reasonable —
the logic served one tool, so it lived next to it.

Then the MCP SDK stopped importing. A compiled Windows dependency was
blocked from loading on my managed workstation, and because `server.py`
imported `MCPServer` at module level, **anything that imported my status
logic inherited the blocker.** My entire test suite failed to collect.

### What I changed

I moved the logic into `src/comment_status.py` with no MCP dependency. The
project now has four layers:

```text
src/
├── clients/regulations_client.py   HTTP boundary
├── comment_status.py               analytical logic
├── models.py                       typed contracts
└── server.py                       MCP transport
```

### Why this mattered more than the immediate fix

The separation was better on its own merits, independent of the blocker:

- 41 tests run in under two seconds with no network and no MCP import
- The status resolution rules are reviewable without reading server plumbing
- The analytical logic can be verified even though the transport layer cannot run

I would not have arrived at this structure without the constraint forcing
it. That is worth stating plainly rather than presenting the design as
foresight.

### Recommendation

**Keep analytical logic out of the MCP transport module from the start.**
Tool functions should validate input, call into logic that lives elsewhere,
and shape the response. If the logic is complex enough to need tests, it is
complex enough to need its own module.

---

## 2. A Misleading Diagnostic Cost Real Time

### What happened

The MCP SDK failed to import with:

```text
ImportError: DLL load failed while importing _rust: Access is denied.
```

The failing module was `cryptography`, a transitive SDK dependency. To
check whether the package was broken, I ran:

```powershell
python -c "import cryptography; print('OK')"
```

It printed `OK`. I concluded the problem had resolved and moved on. It had
not.

### Why the test was wrong

`import cryptography` loads the package's Python-level `__init__`. It does
**not** load the compiled Rust extension. Only importing the submodule
does:

```powershell
python -c "from cryptography.hazmat.bindings._rust import exceptions"
```

That command reproduced the failure immediately.

### The cost

I spent time pursuing the wrong hypothesis — assuming an antivirus scan
lock had cleared — before testing the compiled binding directly. I also
reported a false resolution in my own notes, which I later had to correct.

### Recommendation

**When a compiled Python extension fails to load, test the specific
submodule named in the traceback, not the top-level package.** The
traceback tells you exactly which import failed. Use that path verbatim.

More generally: a diagnostic that can pass while the underlying problem
persists is worse than no diagnostic, because it produces false confidence.

---

## 3. Designing the Evaluation Before the Server Shaped the Tools

### What I did

Before writing the server, I drafted 13 test scenarios and a six-metric
rubric. Only then did I define the tool contracts.

### What that changed

Writing the scenarios forced questions I would otherwise have deferred:

| Scenario I wrote | Question it forced |
| --- | --- |
| "Search yields no results" | Is an empty result set a failure or a valid answer? |
| "Invalid document ID rejected" | Should validation happen before or after the network call? |
| "Comment requirements — no period" | What does the tool return when the concept does not apply? |

The answers became design decisions rather than accidents. Empty results
return a message, not an error. Validation runs first, consuming zero rate
limit. A document with no comment window returns an explicit
`not_applicable` status rather than null fields.

### Where it also helped

The rubric included a provenance metric — every result must carry a
`source_url`. Having written that down before implementing, I built it into
every output model rather than adding it later on request.

### Recommendation

**Write the evaluation scenarios before the tool contracts.** Not because
the tests are more important, but because articulating what "correct"
means surfaces design decisions while they are still cheap to make.

---

## 4. Judgment Under Contradictory Source Data

This was the substantive analytical work in the project, and the part I
would most want a reviewer to examine.

### The problem

The Regulations.gov API exposes five fields describing comment status.
They can disagree. Document `FDA-2021-N-0270-0001` returns:

```text
openForComment:       true
withinCommentPeriod:  false
allowLateComments:    true
commentStartDate:     2021-04-01
commentEndDate:       2021-05-27
```

Every field is accurate. The document does accept late comments, which is
why `openForComment` is true. But an analyst told "open for comment" does
not understand that the formal window closed 1,925 days ago.

### Decision 1: The agency flag wins over local date arithmetic

I could compute status from the dates alone. I chose to trust
`withinCommentPeriod` over my own comparison.

**Reasoning:** the agency owns the authoritative answer to whether it is
accepting comments. A published end date may lag an extension. If I trust
my arithmetic and the agency has extended the window, I tell an analyst the
period is closed when it is not — which is the more consequential of the
two possible errors.

**Consequence I had to handle:** if the flag says open but the end date has
passed, `days_remaining` computes negative. Reporting "-10 days remaining"
would be nonsense. I clamp to zero and substitute a distinct message:
"closing today or already past the published end date."

**Encoded in:** `test_within_period_flag_overrides_stale_end_date`, with a
docstring explaining the reasoning. If someone reverses the precedence
later, that test fails and makes them justify it.

### Decision 2: Suppress the submission link when closed

`get_comment_requirements` returns `comment_url` only when comments can
actually be filed. On a closed window it is null.

**Reasoning:** offering a submission link for a closed period invites a
wasted action.

**The boundary case I decided deliberately:** a document with status
`closed_late_comments_accepted` reports `accepting_comments: true` and
therefore *does* receive a link. Late comments genuinely are accepted;
suppressing the link would block a legitimate action. Both the status enum
name and the summary text state plainly that submissions fall outside the
official window.

This is a judgment call, not an obvious answer. A reviewer could
reasonably argue the other way.

### Decision 3: State uncertainty rather than infer

When the flags produce a combination the precedence rules cannot resolve,
the tool returns:

```text
status:  not_applicable
summary: "Comment period status could not be determined from the
          available fields. Consult the source record directly."
```

**Reasoning:** an analyst acting on a fabricated status is worse off than
one told to check the source. The hackathon guidance cautions against
unfounded claims; this is that principle applied at the field level.

**Encoded in:** `test_contradictory_flags_reports_uncertainty`

### Recommendation

**When source data contradicts itself, decide which field wins, document
why, and write a test that encodes the decision.** The test is the durable
artifact. A comment explains the choice to a reader; a test defends it
against a future change.

---

## 5. Derived Fields Are Where a Tool Earns Its Place

### The distinction

The judging criteria warn against tools that are "a thin wrapper around a
single REST call." Two examples from this project illustrate the line.

**Thin:** `get_document_details` mostly forwards fields. It flattens the
JSON:API nesting and adds a `source_url`, which is useful but modest.

**Not thin:** two derived fields that have no upstream equivalent.

| Derived field | Upstream reality | What the agent avoids |
| --- | --- | --- |
| `is_rulemaking` | A `docketType` string with values including `Rulemaking` and `Nonrulemaking` | Knowing that "Nonrulemaking" is the negative case, and that the comparison is case-sensitive |
| `status` enum | Five separate boolean and date fields that can contradict | Reconciling the contradiction itself |

Neither exists in the API. Both required deciding what the agent actually
needs to know.

### A smaller version of the same idea

The `keywords` field arrives as a list, as null, or absent. I normalize it
to an empty list so the agent always receives a consistent type. That is
not analysis, but it is the same instinct: absorb the API's inconsistency
so the consumer does not have to.

### Recommendation

**Ask what the agent would have to know in order to use the raw field
correctly, then encode that knowledge in the tool.** If the answer is
"nothing," forwarding the field is fine. If the answer involves agency
vocabulary, contradictory flags, or inconsistent types, that is where the
tool should do work.

---

## 6. Time-Dependent Test Data Needs Explicit Handling

### The problem

One scenario verifies the `open` comment status branch against a live
document. Comment windows close. A scenario that passes today will fail
next month, and the failure will look like a defect.

### What I did

- Documented the closing date of each verified document in `results.csv`
- Added a note to `rubric.md` explaining that a later `closed` result is
  correct behavior, not a regression
- Built `find_open_comment_docs.py` so the scenario can be refreshed
- Covered the same branch with a unit test using synthetic dates, which is
  time-independent

The unit test is the durable evidence. The live verification is
corroboration.

### Recommendation

**Any scenario that depends on live external state needs a note explaining
what will change and when.** Otherwise a reviewer re-running your tests
later draws the wrong conclusion.

---

## 7. What Did Not Go Well

Stated plainly, since a lessons-learned document that only reports
successes is not useful.

### One scenario remains unexecuted

| Scenario | Reason | Assessment |
| --- | --- | --- |
| S13 end-to-end MCP flow | Requires MCP runtime | Genuinely blocked |

S12, rate limit handling, was unexecuted for most of the project because I
prioritized building the fourth tool instead. I later closed it by mocking
the HTTP layer, which took under an hour — confirming it had been a choice
about sequencing rather than a constraint.

Writing those tests surfaced four behaviors that were already implemented
but never verified: that a 404 raises a subclass catchable as either a
specific or general error, that upstream response bodies truncate to 200
characters, that every request sets an explicit timeout, and that a 403
raises rather than returning empty results. None required new code. Testing
them converted implicit correctness into verified correctness.

The most useful of those tests mocks a 429 whose response body contains a
credential-like string and asserts it does not appear in the raised error.
My governance document claimed the API key is never leaked in error
messages. That test proves it.

### The MCP Inspector was never used

The standard interactive tool for exercising MCP servers during
development. Node.js was not installed and the SDK would not load. I built
a pure-Python verification path instead, which works but is not equivalent —
I have not confirmed that an actual MCP client can discover and invoke my
tools.

### Repeated force-reinstalls extended a blocker

While diagnosing the compiled extension failure, I ran
`pip install --force-reinstall` several times in quick succession. If the
cause was an endpoint-protection scan lock on newly written binaries, each
reinstall reset the window. I may have prolonged the problem I was trying
to diagnose.

### Documentation drifted from implementation

At one point my governance document claimed four tools were implemented
when only one was. My tool contracts described designed behavior as though
it were built. I caught and corrected both, but a reviewer reading those
files mid-project would have found claims the code did not support.

**Lesson:** update the status**Lesson:** update the status columns in the same commit that changes the
code. A stale claim is worse than an absent one, because it reads as either
carelessness or overstatement.

---

## 8. Recommendations for Federal Teams Adopting MCP

Consolidated guidance for anyone attempting this in a similar environment.

### Environment and tooling

| Recommendation | Why |
| --- | --- |
| Expect to need an IT exception, or use a hosted environment | The official MCP Python SDK depends on compiled extensions that may be blocked on managed workstations |
| Invoke packages via `python -m <tool>`, not `.venv\Scripts\*.exe` | Executable shims in the user profile may be blocked while the module loader works |
| Request WSL2 early if on Windows | The SDK's Windows-specific dependency chain does not execute under Linux; the hackathon documentation recommends this |
| Budget time for tooling requests, not just code | Git, Node.js, and a policy exception each took days to move through approval |

### Architecture

| Recommendation | Why |
| --- | --- |
| Keep analytical logic in modules with no MCP import | It stays testable when the transport layer cannot run |
| Separate the HTTP client from the tool definitions | Lets you verify API behavior independently of protocol behavior |
| Validate input before the network call | Malformed requests consume zero rate limit and return a specific reason |

### Design

| Recommendation | Why |
| --- | --- |
| Name tools after analyst questions, not endpoints | An LLM selects from the name and description alone |
| Return provenance on every result | Output that reaches a briefing needs traceable claims |
| Derive fields that require domain vocabulary to interpret | The agent should not need to know an agency's terminology |
| Report uncertainty explicitly | A fabricated answer is worse than a referral to the source |

### Process

| Recommendation | Why |
| --- | --- |
| Write evaluation scenarios before tool contracts | Articulating "correct" surfaces design decisions cheaply |
| Document decisions where source data is ambiguous | The reasoning is the deliverable, not just the outcome |
| Note what will change in time-dependent tests | Prevents a later reviewer misreading correct behavior as failure |
| Update status claims in the same commit as the code | Prevents documentation from asserting more than the code supports |

---

## 9. Federal Deployment Finding

Offered as a contribution to the organizing team and to other participants,
since it is likely to affect anyone working on a managed federal device.

### Finding

The official MCP Python SDK v2.0.0 does not run on a standard managed GSA
Windows 11 workstation without an IT policy exception. Its compiled Windows
dependencies fail to load with `Access is denied`.

### Diagnostic record

Nine hypotheses tested and ruled out, across two filesystem locations
(`C:\Users\<user>\Documents\...` and `C:\dev\...`):

| Hypothesis | Test performed | Result |
| --- | --- | --- |
| OneDrive folder redirection | Checked `MyDocuments` path and OneDrive process | Not the cause |
| OneDrive placeholder file | Checked file attributes for reparse or offline flags | Not the cause |
| File truncated by antivirus | Verified file size at 9,943,040 bytes | Not the cause |
| NTFS permission denial | `Get-Acl` showed FullControl and ReadAndExecute | Not the cause |
| Defender quarantine | `Get-MpThreatDetection` returned no detections | Not the cause |
| Mark-of-the-Web | Ran `Unblock-File` recursively on all `.pyd` and `.dll` | Not the cause |
| Corrupt download | `pip install --force-reinstall --no-cache-dir`, multiple attempts | Not the cause |
| Version-specific defect | Tested `cryptography` 50.0.1 and 45.0.7 | Not the cause |
| Path-based policy | Reproduced identically in both locations | Not the cause |

### Key discriminator

Not all compiled extensions are affected. From the same virtual
environment:

| Package | Type | Loads |
| --- | --- | --- |
| `pydantic_core` | Compiled Rust extension | Yes |
| `rpds_py` | Compiled Rust extension | Yes |
| `charset_normalizer` | Compiled C extension | Yes |
| `cryptography._rust` | Compiled Rust extension | Initially denied, later succeeded |
| `pywin32._win32sysloader` | Compiled Windows extension | Denied |
| `pywin32.win32job` | Compiled Windows extension | Denied |

This is not a blanket prohibition on compiled extensions in user-profile
virtual environments. Specific binaries are affected while others load
normally.

### Observed pattern

The `cryptography` restriction stopped reproducing later in the same
session without any configuration change, and the failure point advanced
deeper into the SDK import chain — from `cryptography` to
`pywin32._win32sysloader` to `pywin32.win32job`. This is consistent with
endpoint protection holding a temporary lock on newly written compiled
binaries while scanning, though I have not confirmed that with IT.

### Easy to misdiagnose

Two traps worth naming:

1. The error presents as `Access is denied` during a DLL load, which reads
   like a code defect or a permissions problem. It is neither.
2. `import cryptography` succeeds while the compiled submodule is blocked.
   A superficial check reports the package as healthy.

### Implication

Federal teams adopting MCP should plan for a hosted or containerized
development path, or budget time for an IT policy exception. The
organizers' own recommendation of WSL2 for Windows participants suggests
this may already be understood.

Full diagnostics: [`environment_constraints.md`](environment_constraints.md)

---

## 10. What I Would Do Differently

| Instead of | I would |
| --- | --- |
| Placing status logic in `server.py` | Start with a separate module for anything worth testing |
| Running `--force-reinstall` repeatedly | Wait between attempts, in case a scan lock is the cause |
| Testing `import cryptography` | Test the exact submodule path from the traceback |
| Deferring S12 until late | Mock upstream failures early; the tests are cheap and they surface untested behavior you did not know you had |
| Writing the governance doc against the plan | Write it against the code, and update it in the same commit |
| Assuming the demo would show the MCP Inspector | Build the pure-Python verification path from the start, as a floor |

---

## Summary

The substantive work in this project was not writing four tool functions.
It was deciding what an agent should be told when the authoritative source
contradicts itself — and then documenting and testing those decisions so
they can be reviewed and challenged.

The workstation constraint was genuinely disruptive. It also forced a
cleaner architecture than I had designed, and produced a diagnostic record
that may be useful to other federal teams. I would rather report that
honestly than present the separation as foresight.

Two evaluation scenarios remain unexecuted. One is blocked; one is a
choice I made about where to spend limited time. Both are marked as such
in `results.csv`.
