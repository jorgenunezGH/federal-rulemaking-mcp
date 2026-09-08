"""Initial API feasibility probe for the Regulations.gov API v4.

This is a manual diagnostic script, not a pytest suite. It prints
observed responses so a human can assess endpoint availability, field
shapes, and rate limit headers. Automated assertions live in tests/.

Run: python api_probe.py
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.regulations.gov/v4"

# Read the key at module level but validate before use, so a missing key
# produces one clear message rather than four confusing HTTP 403s.
_API_KEY = os.getenv("REGULATIONS_API_KEY")


def _headers() -> dict[str, str]:
    """Build request headers, failing clearly if the key is absent."""
    if not _API_KEY:
        raise RuntimeError(
            "REGULATIONS_API_KEY not found. Check your .env file."
        )
    return {"X-Api-Key": _API_KEY}


def probe_search_documents() -> None:
    """Confirm the document search endpoint returns usable results."""
    print("\n--- PROBE 1: Search Documents ---")
    params: dict[str, str | int] = {
        "filter[searchTerm]": "federal rulemaking",
        "filter[documentType]": "Proposed Rule",
        "page[size]": 5,
    }
    resp = requests.get(
        f"{BASE_URL}/documents",
        headers=_headers(),
        params=params,
        timeout=10,
    )
    print(f"Status Code: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error: {resp.text[:300]}")
        return

    data = resp.json().get("data", [])
    print(f"Documents returned: {len(data)}")
    for doc in data:
        title = doc.get("attributes", {}).get("title", "No title")
        print(f"  - {doc.get('id')}: {title[:80]}")


def probe_get_single_document() -> None:
    """Confirm single-document retrieval returns full metadata."""
    print("\n--- PROBE 2: Get Single Document ---")
    # A stable, long-published FDA notice used as a fixed reference point.
    doc_id = "FDA-2021-N-0270-0001"
    resp = requests.get(
        f"{BASE_URL}/documents/{doc_id}",
        headers=_headers(),
        timeout=10,
    )
    print(f"Status Code: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error: {resp.text[:300]}")
        return

    attrs = resp.json().get("data", {}).get("attributes", {})
    print(f"Title: {attrs.get('title', 'N/A')[:80]}")
    print(f"Type:  {attrs.get('documentType', 'N/A')}")
    print(f"FR No: {attrs.get('frDocNum', 'N/A')}")


def probe_get_docket() -> None:
    """Confirm docket retrieval returns agency and title."""
    print("\n--- PROBE 3: Get Docket ---")
    docket_id = "FDA-2021-N-0270"
    resp = requests.get(
        f"{BASE_URL}/dockets/{docket_id}",
        headers=_headers(),
        timeout=10,
    )
    print(f"Status Code: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error: {resp.text[:300]}")
        return

    attrs = resp.json().get("data", {}).get("attributes", {})
    print(f"Docket Title: {attrs.get('title', 'N/A')[:80]}")
    print(f"Agency:       {attrs.get('agencyId', 'N/A')}")


def probe_rate_limit_headers() -> None:
    """Report rate limit headers so quota can be tracked during development.

    Uses page[size]=5 because the API rejects smaller values with a 400,
    which would obscure the headers we are trying to read.
    """
    print("\n---PROBE 4: Rate Limit Headers ---")
    resp = requests.get(
        f"{BASE_URL}/documents",
        headers=_headers(),
        params={"page[size]": 5},
        timeout=10,
    )
    print(f"Status Code: {resp.status_code}")
    print(f"Limit:     {resp.headers.get('X-RateLimit-Limit', 'Not reported')}")
    print(f"Remaining: {resp.headers.get('X-RateLimit-Remaining', 'Not reported')}")
    print(f"Resets at: {resp.headers.get('X-RateLimit-Reset', 'Not reported')}")


if __name__ == "__main__":
    print("Regulations.gov API v4 Feasibility Probe")
    print("=" * 45)
    probe_search_documents()
    probe_get_single_document()
    probe_get_docket()
    probe_rate_limit_headers()
    print("\n" + "=" * 45)
    print("Probe complete - review observed responses above")
