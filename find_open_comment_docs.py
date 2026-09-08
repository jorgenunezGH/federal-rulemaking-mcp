"""Find documents with currently open comment periods.

Resolves the S10 scenario placeholder with a real, live document ID.
Searches several topic areas because open comment windows are transient.

Run: python find_open_comment_docs.py
"""

from src.clients.regulations_client import search_documents

# Multiple search strategies, ordered from most to least specific to the
# PBS use case. Open comment windows are short-lived, so a single query
# may return none.
SEARCHES = [
    {"search_term": "federal buildings", "document_type": "Proposed Rule"},
    {"search_term": "energy efficiency", "document_type": "Proposed Rule"},
    {"search_term": "energy conservation standards", "document_type": None},
    {"search_term": "building", "document_type": "Proposed Rule"},
    {"search_term": "federal real property", "document_type": None},
]


def scan(search_term: str, document_type: str | None) -> list[tuple]:
    """Return documents from one search that are within an open window."""
    raw = search_documents(
        search_term=search_term,
        document_type=document_type,
        page_size=100,
    )
    found = []
    for doc in raw.get("data", []):
        attrs = doc.get("attributes", {})
        if attrs.get("withinCommentPeriod") is True:
            found.append(
                (
                    doc.get("id"),
                    attrs.get("commentEndDate"),
                    attrs.get("agencyId"),
                    (attrs.get("title") or "")[:55],
                )
            )
    return found


def run() -> None:
    all_found = []

    for search in SEARCHES:
        label = search["search_term"]
        dtype = search["document_type"] or "any type"
        print(f"Scanning: '{label}' ({dtype})...", end=" ")

        try:
            found = scan(search["search_term"], search["document_type"])
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            print(f"error: {exc}")
            continue

        print(f"{len(found)} open")
        all_found.extend(found)

    # Deduplicate by document ID while preserving order.
    seen = set()
    unique = []
    for item in all_found:
        if item[0] not in seen:
            seen.add(item[0])
            unique.append(item)

    print(f"\n{'=' * 60}")
    if not unique:
        print("No documents with open comment periods found.")
        print("\nOpen windows are transient. Options:")
        print("  - Retry later")
        print("  - Mark S10 as not executed and cite the unit test coverage")
        print("    (test_open_period_reports_days_remaining verifies the")
        print("     OPEN branch with synthetic data)")
        return

    print(f"Found {len(unique)} document(s) with open comment periods:\n")
    for doc_id, end_date, agency, title in unique[:15]:
        print(f"  {doc_id}")
        print(f"    agency:  {agency}")
        print(f"    closes:  {end_date}")
        print(f"    title:   {title}")
        print()

    print("Use one of these IDs to resolve the S10 scenario placeholder.")


if __name__ == "__main__":
    run()
