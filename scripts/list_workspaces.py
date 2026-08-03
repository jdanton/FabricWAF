"""
list_workspaces.py

Lists every non-personal Fabric / Power BI workspace tenant-wide and the
capacity each is assigned to, via the Power BI Admin REST API.

Sources
-------
  - GET /admin/groups      -> workspaces (name, type, state, capacityId), paged
  - GET /admin/capacities  -> capacity id -> display name + SKU

Personal workspaces (type 'PersonalGroup' / 'Personal') are excluded. Workspaces
not on a dedicated capacity are reported with Capacity = "(none - shared/Pro)".

Authentication
--------------
  DefaultAzureCredential against the Power BI scope (same as the other scripts).
  Requires a Fabric administrator identity (Tenant.Read.All) to read admin APIs.

Environment variables: none required.

Exit codes
----------
  0  Listing produced
  1  A request failed
"""

import argparse
import csv
import sys

import requests
from azure.identity import DefaultAzureCredential

POWERBI_API = "https://api.powerbi.com/v1.0/myorg"
PERSONAL_TYPES = {"PersonalGroup", "Personal"}
PAGE = 5000  # admin/groups max $top

_credential = DefaultAzureCredential()


def _headers() -> dict:
    token = _credential.get_token("https://analysis.windows.net/powerbi/api/.default").token
    return {"Authorization": f"Bearer {token}"}


def _get(url: str, params: dict = None) -> dict:
    resp = requests.get(url, headers=_headers(), params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_capacity_map() -> dict:
    """capacityId (lowercased) -> {name, sku}."""
    data = _get(f"{POWERBI_API}/admin/capacities")
    out = {}
    for cap in data.get("value", []):
        cid = (cap.get("id") or "").lower()
        out[cid] = {"name": cap.get("displayName") or "", "sku": cap.get("sku") or ""}
    return out


def get_workspaces() -> list:
    """All non-personal workspaces, paged via $skip."""
    workspaces = []
    skip = 0
    while True:
        data = _get(f"{POWERBI_API}/admin/groups", {
            "$top": PAGE,
            "$skip": skip,
            "$filter": "type ne 'PersonalGroup'",
        })
        batch = data.get("value", [])
        workspaces.extend(batch)
        if len(batch) < PAGE:
            break
        skip += PAGE
    return workspaces


def _print_table(rows: list) -> None:
    if not rows:
        print("  (none)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  " + " | ".join(c.ljust(widths[c]) for c in cols))
    print("  " + "-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  " + " | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def _write_csv(rows: list, path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  Wrote {len(rows)} rows -> {path}")


def main(csv_path: str, active_only: bool, on_capacity_only: bool) -> int:
    try:
        caps = get_capacity_map()
        workspaces = get_workspaces()
    except requests.HTTPError as exc:
        print(f"ERROR: HTTP {exc.response.status_code}: {exc.response.text}")
        if exc.response.status_code in (401, 403):
            print("  -> Needs a Fabric administrator identity (Tenant.Read.All).")
        return 1

    rows = []
    for w in workspaces:
        wtype = w.get("type", "")
        if wtype in PERSONAL_TYPES:          # guard in case the $filter is ignored
            continue
        state = w.get("state", "")
        if active_only and state != "Active":
            continue
        cid = w.get("capacityId") or ""
        if on_capacity_only and not cid:
            continue
        cap = caps.get(cid.lower())
        if cap:
            cap_name, sku = cap["name"], cap["sku"]
        elif cid:
            cap_name, sku = "(unknown capacity)", ""
        else:
            cap_name, sku = "(none - shared/Pro)", ""
        rows.append({
            "Workspace": w.get("name", ""),
            "Type": wtype,
            "State": state,
            "Capacity": cap_name,
            "SKU": sku,
            "Workspace Id": w.get("id", ""),
            "Capacity Id": cid,
        })

    rows.sort(key=lambda r: (r["Capacity"].lower(), r["Workspace"].lower()))

    on_cap = sum(1 for r in rows if r["Capacity Id"])
    print(f"Non-personal workspaces: {len(rows)} "
          f"({on_cap} on a capacity, {len(rows) - on_cap} not)\n")
    _print_table(rows)
    if csv_path:
        _write_csv(rows, csv_path)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="List non-personal workspaces and their assigned capacity.")
    parser.add_argument("--csv", metavar="PATH", default="",
                        help="Also write results to a CSV file.")
    parser.add_argument("--active-only", action="store_true",
                        help="Only workspaces in the Active state.")
    parser.add_argument("--on-capacity-only", action="store_true",
                        help="Only workspaces assigned to a dedicated capacity.")
    args = parser.parse_args()
    sys.exit(main(args.csv, args.active_only, args.on_capacity_only))
