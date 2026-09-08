"""One-time diagnostic: inspect the real shape of a docket response.

Run: python discover_docket_fields.py
"""

import json

import requests

from src.clients.regulations_client import BASE_URL, _headers

DOCKET_ID = "FDA-2021-N-0270"


def run() -> None:
    resp = requests.get(
        f"{BASE_URL}/dockets/{DOCKET_ID}",
        headers=_headers(),
        timeout=10,
    )
    print(f"Status: {resp.status_code}\n")

    payload = resp.json()
    attrs = payload.get("data", {}).get("attributes", {})

    print(f"--- {len(attrs)} attributes returned ---\n")
    for key in sorted(attrs):
        preview = str(attrs[key])
        if len(preview) > 60:
            preview = preview[:60] + "..."
        print(f"  {key:<30} = {preview}")

    print("\n--- Top-level keys ---")
    print(f"  data keys: {list(payload.get('data', {}).keys())}")

    with open("sample_docket_response.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print("\nFull payload saved to sample_docket_response.json")


if __name__ == "__main__":
    run()
