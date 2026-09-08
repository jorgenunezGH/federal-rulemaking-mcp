"""Live check of the get_docket_details logic path.

Exercises the client and models directly, bypassing the MCP transport
layer. Run: python verify_docket_tool.py
"""

from pydantic import ValidationError

from src.clients.regulations_client import DocumentNotFoundError, get_docket
from src.models import DocketDetailInput, DocketDetailOutput

REG_GOV_DOCKET_BASE = "https://www.regulations.gov/docket/"


def fetch(docket_id: str) -> None:
    """Fetch and print one docket or a handled failure."""
    print(f"\n--- {docket_id} ---")

    try:
        params = DocketDetailInput(docket_id=docket_id)
    except ValidationError:
        print("  Rejected at validation: ValidationError")
        return

    try:
        raw = get_docket(params.docket_id)
    except DocumentNotFoundError as e:
        print(f"  Not found (handled gracefully): {e}")
        return

    data = raw.get("data", {})
    attrs = data.get("attributes", {})
    docket_type = attrs.get("docketType")

    out = DocketDetailOutput(
        docket_id=data.get("id", ""),
        title=attrs.get("title", ""),
        short_title=attrs.get("shortTitle"),
        agency_id=attrs.get("agencyId"),
        program=attrs.get("program"),
        docket_type=docket_type,
        is_rulemaking=(
            docket_type.strip().lower() == "rulemaking" if docket_type else None
        ),
        rin=attrs.get("rin"),
        keywords=attrs.get("keywords") or [],
        effective_date=attrs.get("effectiveDate"),
        modify_date=attrs.get("modifyDate"),
        source_url=f"{REG_GOV_DOCKET_BASE}{data.get('id', '')}",
    )

    print(f"  Title:         {out.title[:60]}")
    print(f"  Agency:        {out.agency_id}")
    print(f"  Program:       {out.program}")
    print(f"  Docket type:   {out.docket_type}")
    print(f"  Is rulemaking: {out.is_rulemaking}")
    print(f"  RIN:           {out.rin}")
    print(f"  Keywords:      {out.keywords[:4]}")
    print(f"  Modified:      {out.modify_date}")
    print(f"  Source:        {out.source_url}")


if __name__ == "__main__":
    print("Live verification: get_docket_details")
    fetch("FDA-2021-N-0270")
    fetch("EERE-2022-BT-TP-0028")
    fetch("FAKE-0000-X-0000")
    fetch("not a docket !!!")
    print("\nVerification complete.")
