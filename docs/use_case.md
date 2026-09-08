# Use Case — Federal Rulemaking Read MCP Server

**Track:** Service "Read" Integration
**Data Source:** Regulations.gov API v4
**Author:** Jorge Nunez
**Date:** 2026-08-19

---

## Persona

**Program Analyst, Public Buildings Service (PBS),
Office of Program & Project Management (OPPM).** This person is
responsible for evaluating new or modified legislation and proposed
regulations for their projected impact on PBS programs, services, and
resources. They routinely collect information from multiple sources,
reconcile conflicting data, and prepare briefings and recommendations
for higher-level management.

---

## Problem

Assessing the impact of proposed regulations on PBS programs requires
manually monitoring Regulations.gov, retrieving authoritative source
documents, and determining comment period status — often across
multiple agencies whose rules affect federal buildings, real property,
energy standards, and facilities operations.

This is time-consuming, easy to miss, and hard to keep current. An AI
agent with governed, read-only access to Regulations.gov can retrieve
and summarize authoritative regulatory information on demand — with
full source provenance suitable for management briefings — freeing the
analyst to focus on impact assessment and coordination rather than
manual data gathering.

---

## Organizational Value

The PBS Office of Program & Project Management employs numerous
program analysts across multiple project-delivery divisions
(program development, construction services, project controls, and
global project management). Proposed regulations affecting federal
buildings, energy standards, and real property touch all of these
divisions.

A read-only MCP server that surfaces authoritative regulatory
information on demand scales this benefit across the office —
reducing duplicated manual research and helping analysts flag
regulatory impacts earlier in the project lifecycle.

---

## User Workflow (Read-Only)

1. The analyst asks the AI agent about proposed rules on a topic.
2. The agent calls `search_rulemaking_documents` to find matches.
3. The analyst selects a document of interest.
4. The agent calls `get_document_details` for full metadata.
5. The agent calls `get_docket_details` to show the full rulemaking
   context and current status.
6. The agent calls `get_comment_requirements` to report whether the
   comment period is open and what the deadline is.
7. The analyst receives an authoritative summary with links back to
   the source records on Regulations.gov.

---

## Scope Boundaries

### In Scope (This Build)

- Search documents by keyword, agency, and type
- Retrieve full document metadata by ID
- Retrieve docket metadata by ID
- Report comment period status and requirements
- Return source URLs for every result (provenance)

### Out of Scope (Deferred)

- Submitting comments (POST) — pending sandbox confirmation
- Any write or state-changing operation
- Storing or caching retrieved data beyond the session
- Making recommendations on how to comment or what to say

---

## Success Definition

The server is successful if an AI agent can, through bounded tools:
- Find a relevant rulemaking document from a natural-language request, or clearly report when no match is found
- Retrieve accurate, complete metadata
- Report comment requirements correctly
- Always cite the authoritative source
- Fail gracefully on invalid IDs, empty results, and rate limits

---

## Illustrative Demo Scenario

A proposed federal buildings energy-efficiency rule is published
(e.g., a DOE/EERE rule on clean energy for new federal buildings).
The analyst asks the agent to locate it, retrieve the authoritative
document and parent docket, and report the comment deadline —
producing a cited summary suitable for a management briefing on
projected PBS impact.