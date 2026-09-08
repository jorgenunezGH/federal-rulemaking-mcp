"""Standalone smoke test against the live Regulations.gov API.

Run manually:  python smoke_test.py
Not part of the pytest suite (makes real network calls).
"""

from src.clients.regulations_client import search_documents
from src.models import DocumentResult, SearchInput, SearchOutput

REG_GOV_BASE = "https://www.regulations.gov/document/"


def run() -> None:
    print("--- 1. Validating input model ---")
    params = SearchInput(search_term="federal buildings energy efficiency")
    print(f"OK: '{params.search_term}' (page_size={params.page_size})")

    print("\n--- 2. Calling Regulations.gov ---")
    raw = search_documents(
        search_term=params.search_term,
        page_size=params.page_size,
    )
    data = raw.get("data", [])
    print(f"OK: {len(data)} documents returned")

    print("\n--- 3. Mapping to output model ---")
    results = [
        DocumentResult(
            document_id=d.get("id", "unknown"),
            title=d.get("attributes", {}).get("title", "No title"),
            document_type=d.get("attributes", {}).get("documentType"),
            posted_date=d.get("attributes", {}).get("postedDate"),
            source_url=f"{REG_GOV_BASE}{d.get('id', '')}",
        )
        for d in data
    ]
    out = SearchOutput(
        total_found=raw.get("meta", {}).get("totalElements", len(data)),
        results=results,
    )
    print(f"OK: total_found={out.total_found}")

    print("\n--- 4. Sample results with provenance ---")
    for r in out.results[:3]:
        print(f"  - {r.document_id}")
        print(f"    {r.title[:70]}")
        print(f"    {r.source_url}")

    print("\nSMOKE TEST PASSED - client and models work end to end.")


if __name__ == "__main__":
    run()