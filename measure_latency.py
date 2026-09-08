"""Measure per-call latency for the read tools.

Populates the latency_sec column in evaluations/results.csv.
Run: python measure_latency.py
"""

import time

from src.clients.regulations_client import (
    DocumentNotFoundError,
    RegulationsClientError,
    get_docket,
    get_document,
    search_documents,
)


def timed(label: str, fn) -> None:
    """Run fn, print elapsed seconds, and report the outcome type."""
    start = time.perf_counter()
    outcome = "success"
    try:
        fn()
    except DocumentNotFoundError:
        outcome = "not_found (expected)"
    except RegulationsClientError as e:
        outcome = f"client_error: {e}"
    elapsed = time.perf_counter() - start
    print(f"  {label:<42} {elapsed:6.2f}s   {outcome}")


if __name__ == "__main__":
    print("Latency measurements (seconds)\n")

    timed(
        "S01 search: federal buildings energy",
        lambda: search_documents("federal buildings energy efficiency", page_size=10),
    )
    timed(
        "S02 search with agency filter (EERE)",
        lambda: search_documents("energy", agency_id="EERE", page_size=10),
    )
    timed(
        "S03 search with no expected results",
        lambda: search_documents("zxqwv nonexistent term 99999", page_size=5),
    )
    timed(
        "S05 get_document: FDA-2021-N-0270-0001",
        lambda: get_document("FDA-2021-N-0270-0001"),
    )
    timed(
        "S06 get_document: nonexistent ID",
        lambda: get_document("FAKE-9999-X-0000-0001"),
    )
    timed(
        "S08 get_docket: FDA-2021-N-0270",
        lambda: get_docket("FDA-2021-N-0270"),
    )
    timed(
        "S09 get_docket: nonexistent ID",
        lambda: get_docket("FAKE-0000-X-0000"),
    )
    timed(
        "S10 comment reqs: EERE-2022-BT-OT-0004-0001 (open)",
        lambda: get_document("EERE-2022-BT-OT-0004-0001"),
    )
    timed(
        "S11 comment reqs: FDA-2021-N-0270-0001",
        lambda: get_document("FDA-2021-N-0270-0001"),
    )
    timed(
        "S11b comment reqs: EERE-2022-BT-TP-0028-0021",
        lambda: get_document("EERE-2022-BT-TP-0028-0021"),
    )

    print("\nRecord these values in evaluations/results.csv")
