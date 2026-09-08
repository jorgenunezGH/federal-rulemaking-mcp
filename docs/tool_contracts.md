# Tool Contracts — Federal Rulemaking Read MCP Server

**Track:** Service "Read" Integration
**Total tools:** 4
**Status:** All four implemented and verified against the live API
**Last updated:** 2026-09-03

All tools are read-only. Only HTTP GET requests are issued. Every
successful response includes a `source_url` pointing to the authoritative
Regulations.gov record.

---

## Tool 1: search_rulemaking_documents

**Purpose:** Find rulemaking documents matching a topic, optionally scoped
by agency or document type.

### Inputs

| Field | Type | Required | Validation | Notes |
| --- | --- | --- | --- | --- |
| `search_term` | string | Yes | min length 1 | Keyword or phrase |
| `agency_id` | string | No | — | Agency code, e.g. `EERE`, `FDA` |
| `document_type` | string | No | — | e.g. `Proposed Rule`, `Notice` |
| `page_size` | integer | No | 5 to 250 | Default 10 |

### Output

| Field | Type | Notes |
| --- | --- | --- |
| `total_found` | integer | Total matches available upstream |
| `results` | list | Each item is a `DocumentResult` |
| `message` | string or null | Present on empty results or failure |

**`DocumentResult` fields:** `document_id`, `title`, `document_type`,
`posted_date`, `source_url`

### Failure modes

| Condition | Behavior |
| --- | --- |
| Empty `search_term` | Validation error before any network call |
| `page_size` below 5 or above 250 | Validation error before any network call |
| No matches | Empty list with message "No matching documents found." |
| Rate limit (429) | Message with retry guidance |
| Upstream error | Truncated, sanitized message |

**Design note:** The upstream API rejects `page_size` below 5 with an HTTP
400. That floor is enforced at the model layer so the agent receives a
specific validation reason rather than an opaque upstream failure.

---

## Tool 2: get_document_details

**Purpose:** Retrieve the full authoritative record for one document.

### Inputs

| Field | Type | Required | Validation |
| --- | --- | --- | --- |
| `document_id` | string | Yes | min length 5, pattern `^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}$` |

### Output

| Field | Type | Notes |
| --- | --- | --- |
| `document_id` | string | Echoed from the upstream response |
| `title` | string | Full, untruncated |
| `document_type` | string or null | |
| `subtype` | string or null | |
| `agency_id` | string or null | |
| `docket_id` | string or null | Parent docket; input to Tool 3 |
| `posted_date` | string or null | ISO 8601 |
| `fr_document_number` | string or null | Federal Register document number |
| `fr_citation` | string or null | e.g. `86 FR 17165` |
| `page_count` | integer or null | |
| `withdrawn` | boolean or null | |
| `open_for_comment` | boolean or null | Raw API flag; see Tool 4 |
| `within_comment_period` | boolean or null | Raw API flag; see Tool 4 |
| `comment_end_date` | string or null | ISO 8601 |
| `source_url` | string | |
| `message` | string or null | Present on failure |

### Failure modes

| Condition | Behavior |
| --- | --- |
| Malformed ID | Validation error before any network call |
| ID not found (404) | `DocumentNotFoundError` caught; clear "not found" message |
| Rate limit (429) | Message with retry guidance |
| Upstream error | Truncated, sanitized message |

**Design note:** ID format is validated before the request. A malformed ID
sent upstream returns a 404 indistinguishable from a genuinely missing
document, which is a worse signal for an agent to reason about.

**Verified:** `FDA-2021-N-0270-0001`, `EERE-2022-BT-TP-0028-0021`,
`FAA-2026-9967-0001`

---

## Tool 3: get_docket_details

**Purpose:** Retrieve the parent docket, giving the full regulatory
context for a document. A document is one filing; a docket is the entire
regulatory action and may span years.

### Inputs

| Field | Type | Required | Validation |
| --- | --- | --- | --- |
| `docket_id` | string | Yes | min length 3, pattern `^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){1,}$` |

### Output

| Field | Type | Notes |
| --- | --- | --- |
| `docket_id` | string | |
| `title` | string | |
| `short_title` | string or null | |
| `agency_id` | string or null | |
| `program` | string or null | Sub-agency program office |
| `docket_type` | string or null | `Rulemaking` or `Nonrulemaking` |
| `is_rulemaking` | boolean or null | **Derived** — see design note |
| `rin` | string or null | Regulation Identifier Number |
| `keywords` | list of strings | Normalized; empty list when absent |
| `effective_date` | string or null | ISO 8601 |
| `modify_date` | string or null | ISO 8601 |
| `source_url` | string | Uses the `/docket/` path, not `/document/` |
| `message` | string or null | Present on failure |

### Failure modes

Same as Tool 2. Reuses `DocumentNotFoundError` — from the agent's
perspective, "the record you requested does not exist" is the same
actionable outcome for both documents and dockets.

**Design notes:**

`is_rulemaking` is derived, not returned by the API. The upstream field is
a `docketType` string with values including `Rulemaking` and
`Nonrulemaking`. Deriving the boolean means the agent does not need to know
that "Nonrulemaking" is the negative case, or that the comparison is
case-sensitive. Left as null when `docketType` is absent rather than
guessing.

`keywords` may be absent or null upstream. It is normalized to an empty
list so the agent always receives a consistent type.

Dockets use a different Regulations.gov URL path than documents, so
`source_url` is built from a separate base constant.

**Verified:** `FDA-2021-N-0270` (Nonrulemaking, no RIN),
`EERE-2022-BT-TP-0028` (Rulemaking, RIN 1904-AF49),
`FAA-2026-9967` (Rulemaking, RIN 2120-AA66)

---

## Tool 4: get_comment_requirements

**Purpose:** Determine whether a document is accepting public comments and
by when. Resolves several upstream fields that can appear to contradict one
another into a single unambiguous status.

### Inputs

| Field | Type | Required | Validation |
| --- | --- | --- | --- |
| `document_id` | string | Yes | Same pattern as Tool 2 |

### Output

| Field | Type | Notes |
| --- | --- | --- |
| `document_id` | string | |
| `title` | string | |
| `status` | enum | See status values below |
| `summary` | string | Plain-language, quotable in a briefing |
| `accepting_comments` | boolean | True for `open` and `closed_late_comments_accepted` |
| `comment_start_date` | string or null | ISO 8601 |
| `comment_end_date` | string or null | ISO 8601 |
| `days_remaining` | integer or null | Present when status is `open`; clamped at 0 |
| `days_since_closed` | integer or null | Present when the window has closed |
| `allows_late_comments` | boolean or null | Raw API flag |
| `withdrawn` | boolean or null | Raw API flag |
| `source_url` | string | |
| `comment_url` | string or null | **Suppressed when not accepting** |
| `message` | string or null | Present on failure |

### Status values

| Value | Meaning |
| --- | --- |
| `open` | Currently within the comment window |
| `closed_late_comments_accepted` | Window closed, but late comments permitted |
| `closed` | Window closed, late comments not permitted |
| `not_yet_open` | Window has a future start date |
| `withdrawn` | Document withdrawn; not accepting comments |
| `not_applicable` | No comment period, or status unresolvable |

### Resolution precedence

Evaluated in this order:

1. **`withdrawn` is true** → `WITHDRAWN`. A withdrawn document cannot
   meaningfully accept comments regardless of its date fields.
2. **No start and no end date** → `NOT_APPLICABLE`.
3. **Start date is in the future** → `NOT_YET_OPEN`, with days until open.
4. **`withinCommentPeriod` is true** → `OPEN`, with days remaining.
5. **End date has passed** → `CLOSED_LATE_ACCEPTED` if
   `allowLateComments`, otherwise `CLOSED`, with days elapsed.
6. **Otherwise** → `NOT_APPLICABLE` with an explicit "could not be
   determined" summary.

**Why the agency flag wins over local date arithmetic:** The API exposes
both a `withinCommentPeriod` boolean and explicit dates, and they can
disagree. An agency may extend a window without the published end date
reflecting the change. Trusting local arithmetic over the agency's own flag
risks reporting a window closed when it is not, which is the more
consequential of the two possible errors.

**Consequence:** when the flag reports open but the end date has passed,
`days_remaining` computes negative. It is clamped to zero and paired with
the message "closing today or already past the published end date."
Covered by `test_within_period_flag_overrides_stale_end_date`.

**Why `comment_url` is conditional:** Offering a submission link on a
closed window invites a wasted action, so it is returned only when
`accepting_comments` is true.

**No additional API call:** Comment data lives on the document record, so
this tool reuses the same request as Tool 2 rather than issuing a second.

### Failure modes

| Condition | Behavior |
| --- | --- |
| Malformed ID | Validation error before any network call |
| ID not found (404) | Clear "not found" message |
| Unresolvable flags | `NOT_APPLICABLE` with explicit uncertainty stated |
| Rate limit (429) | Message with retry guidance |

### Verified status branches

| Status | Verified on |
| --- | --- |
| `open` | `FAA-2026-9967-0001` (live, 43 days remaining) |
| `open` | `EERE-2022-BT-OT-0004-0001` (live, verified 2026-09-02) |
| `closed_late_comments_accepted` | `FDA-2021-N-0270-0001` (live, 1,925 days closed) |
| `not_applicable` | `EERE-2022-BT-TP-0028-0021`, `EERE-2010-BT-STD-0031-0128` |
| `not_yet_open` | Unit test, synthetic dates |
| `closed` | Unit test, synthetic dates |
| `withdrawn` | Unit test, synthetic dates |

---

## Design Principles Applied

| Principle | Evidence |
| --- | --- |
| Goal-oriented, not REST-mirroring | Tool 4 has no equivalent upstream endpoint; it synthesizes five fields |
| Provenance on every result | `source_url` on all four tools |
| Bounded and read-only | Four tools; only GET requests issued |
| Validation before network calls | Malformed input consumes zero rate limit |
| Predictable failures | Every tool documents its failure modes ||  Derived fields over raw vocabulary | `is_rulemaking` from `docketType`; `status` enum from five flags |
| Uncertainty stated, not guessed | Tool 4 returns "could not be determined" rather than inferring |
| Consistent types | `keywords` normalized to a list; absent optionals return null, never omitted |

---

## Test Coverage

| Tool or concern | Test file | Tests |
| --- | --- | --- |
| `search_rulemaking_documents` | `tests/test_search.py` | 4 |
| `get_document_details` | `tests/test_document_details.py` | 5 |
| `get_docket_details` | `tests/test_docket_details.py` | 6 |
| `get_comment_requirements` | `tests/test_comment_requirements.py` | 15 |
| Upstream failure handling | `tests/test_rate_limit.py` | 8 |
| Validation ordering and timeouts | `tests/test_tool_error_handling.py` | 3 |
| **Total** | | **41** |

All tests run offline with no network dependency and no MCP SDK import.
Upstream failure conditions are verified by mocking the HTTP layer rather
than by exhausting the live rate limit.