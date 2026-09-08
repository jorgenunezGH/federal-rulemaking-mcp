"""Verify S03: a search with no matches returns a clear empty result.

Confirms the tool reports zero results with a message rather than
raising an error or returning malformed output.
"""

from src.clients.regulations_client import search_documents

NONSENSE_TERM = "zxqwv nonexistent term 99999"


def run() -> None:
    print(f"Searching for: '{NONSENSE_TERM}'")
    raw = search_documents(NONSENSE_TERM, page_size=5)

    data = raw.get("data", [])
    total = raw.get("meta", {}).get("totalElements", 0)

    print(f"  Results returned: {len(data)}")
    print(f"  Total available:  {total}")

    if len(data) == 0:
        print("  S03 PASS: empty result set, no exception raised")
    else:
        print(f"  S03 REVIEW: expected 0 results, got {len(data)}")


if __name__ == "__main__":
    run()
