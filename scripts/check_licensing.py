"""
check_licensing.py

Diagnoses "you need a Power BI Pro license" / unlicensed prompts after a capacity
reorganization (e.g. splitting one P-SKU into several F-SKUs), by checking two
tenant-wide conditions:

  1. Workspace-to-capacity assignment
     Only Premium (P1+) and Fabric F64-or-larger capacities let *Free* users view
     content — and only for content that lives ON that capacity. This benefit is
     PER-CAPACITY, not tenant-wide: having an F64 AND a P2 does nothing for a
     workspace that landed on the F8/F16/F32, or that fell back to "Pro" license
     mode (the default for a workspace not assigned to any capacity). Viewers of
     those workspaces are prompted to upgrade to Pro.

  2. Split semantic models  (the sneaky one)
     A report can live on a qualifying capacity while its semantic model sits in a
     workspace that does NOT qualify. Free users are still blocked, because opening
     the report queries the model on the Pro-only capacity. Common after a re-org
     when a report on the F64 keeps pointing at a shared model left behind on the
     dev F16.

Qualifying SKUs for Free-user viewing: P1-P5, F64/F128/F256/... (F >= 64), and
Fabric trial (FT). NOT qualifying: F2-F32, A/EM (embedded), and "Pro/Shared" mode.

Authentication:
  DefaultAzureCredential against the Power BI API scope. Requires a Fabric / Power BI
  administrator identity (Tenant.Read.All) to read the admin APIs.

Usage:
  pip install azure-identity requests
  python check_licensing.py                 # prints report, writes CSVs + JSON
  python check_licensing.py --report-only   # skip CSVs, JSON only

Exit codes: 0 = no licensing gaps found, 1 = gaps found, 2 = failure.
Outputs (gitignored — real tenant data): license_workspaces.csv,
license_split_models.csv, license-report.json
"""

import argparse
import csv
import json
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

PBI = "https://api.powerbi.com/v1.0/myorg"
SCOPE = "https://analysis.windows.net/powerbi/api/.default"

_credential = DefaultAzureCredential()


def _headers() -> dict:
    return {"Authorization": f"Bearer {_credential.get_token(SCOPE).token}",
            "Content-Type": "application/json"}


def _get(url: str, path: str) -> dict:
    """GET with 429 backoff; PermissionError on 401/403."""
    for _ in range(6):
        r = requests.get(url, headers=_headers(), timeout=60)
        if r.status_code == 429:
            wait = min(int(r.headers.get("Retry-After", 10)), 60)
            print(f"  [429] throttled — waiting {wait}s ...", flush=True)
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            raise PermissionError(
                f"HTTP {r.status_code} on {path} -> needs a Fabric/Power BI "
                "administrator identity (Tenant.Read.All).")
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _list(path: str) -> list:
    """Plain admin GET (no paging) — e.g. admin/capacities rejects $top/$skip."""
    return _get(f"{PBI}/{path}", path).get("value", [])


def _paged(path: str, top: int = 5000) -> list:
    """GET an admin list, following $top/$skip paging with 429 backoff."""
    out, skip = [], 0
    while True:
        batch = _get(f"{PBI}/{path}?$top={top}&$skip={skip}", path).get("value", [])
        out.extend(batch)
        if len(batch) < top:
            return out
        skip += top


# ---------------------------------------------------------------------------
# SKU classification
# ---------------------------------------------------------------------------

def qualifies(sku: str) -> bool:
    """True if the capacity SKU lets *Free* users view content (P1+, F64+, trial)."""
    s = (sku or "").upper()
    if s.startswith("FT"):                       # Fabric trial (~F64)
        return True
    if s.startswith("P") and s[1:].isdigit():    # P1-P5 Premium
        return True
    if s.startswith("F") and s[1:].isdigit():    # Fabric — F64 and larger only
        return int(s[1:]) >= 64
    return False                                  # A/EM embedded, unknown -> no


def _lc(v):
    return (v or "").lower()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(report_only: bool = False) -> int:
    print("Reading capacities, workspaces, datasets and reports (Admin API)...")
    capacities = _list("admin/capacities")
    workspaces = _paged("admin/groups")
    datasets = _paged("admin/datasets")
    reports = _paged("admin/reports")
    print(f"  capacities={len(capacities)}  workspaces={len(workspaces)}  "
          f"datasets={len(datasets)}  reports={len(reports)}")

    # capacity id -> {name, sku, qualifies}
    cap_by_id = {}
    for c in capacities:
        cap_by_id[_lc(c.get("id"))] = {
            "name": c.get("displayName", ""),
            "sku": c.get("sku", ""),
            "qualifies": qualifies(c.get("sku")),
            "state": c.get("state", ""),
        }

    def ws_license(ws: dict) -> dict:
        """Resolve a workspace's capacity + whether Free users can view it."""
        cid = _lc(ws.get("capacityId"))
        on_cap = bool(ws.get("isOnDedicatedCapacity")) and cid in cap_by_id
        if not on_cap:
            return {"capacity_name": "", "sku": "", "mode": "Pro / Shared",
                    "qualifies": False}
        cap = cap_by_id[cid]
        return {"capacity_name": cap["name"], "sku": cap["sku"],
                "mode": "Dedicated capacity", "qualifies": cap["qualifies"]}

    # workspace id -> license + name (standard, active workspaces only)
    ws_by_id, ws_rows = {}, []
    reports_per_ws = {}
    for r in reports:
        reports_per_ws[_lc(r.get("workspaceId"))] = reports_per_ws.get(_lc(r.get("workspaceId")), 0) + 1

    for ws in workspaces:
        if (ws.get("type") or "Workspace") != "Workspace":
            continue
        if (ws.get("state") or "Active") != "Active":
            continue
        lic = ws_license(ws)
        wid = _lc(ws.get("id"))
        ws_by_id[wid] = {"name": ws.get("name", ""), **lic}
        ws_rows.append({
            "workspace": ws.get("name", ""),
            "workspace_id": ws.get("id", ""),
            "capacity": lic["capacity_name"],
            "sku": lic["sku"],
            "mode": lic["mode"],
            "free_users_can_view": "yes" if lic["qualifies"] else "NO",
            "reports_in_workspace": reports_per_ws.get(wid, 0),
        })

    # dataset id -> workspace id
    ds_ws = {_lc(d.get("id")): _lc(d.get("workspaceId")) for d in datasets}

    # ---- Check 1: workspaces where Free users cannot view --------------------
    blocked_ws = [r for r in ws_rows if r["free_users_can_view"] == "NO"]
    blocked_ws.sort(key=lambda r: (-r["reports_in_workspace"], r["workspace"]))

    # ---- Check 2: split semantic models -------------------------------------
    split_rows = []
    for r in reports:
        rep_wid = _lc(r.get("workspaceId"))
        rep_ws = ws_by_id.get(rep_wid)
        if not rep_ws or not rep_ws["qualifies"]:
            continue  # report itself not on a qualifying capacity -> Check 1 covers it
        did = _lc(r.get("datasetId"))
        if not did:
            continue
        model_wid = ds_ws.get(did)
        if model_wid is None:
            continue  # dataset not in admin list (push/streaming/deleted) — skip
        model_ws = ws_by_id.get(model_wid)
        if model_ws is None or model_ws["qualifies"]:
            continue  # model is fine
        # report qualifies, model does NOT -> Free users blocked
        split_rows.append({
            "report": r.get("name", ""),
            "report_workspace": rep_ws["name"],
            "report_capacity": f'{rep_ws["capacity_name"]} ({rep_ws["sku"]})',
            "model_workspace": model_ws["name"],
            "model_capacity": (f'{model_ws["capacity_name"]} ({model_ws["sku"]})'
                               if model_ws["sku"] else model_ws["mode"]),
        })
    split_rows.sort(key=lambda r: (r["model_workspace"], r["report_workspace"], r["report"]))

    # ---- Report -------------------------------------------------------------
    print("\n" + "=" * 72)
    print("CAPACITIES")
    for cid, c in sorted(cap_by_id.items(), key=lambda kv: kv[1]["sku"]):
        tag = "Free users OK" if c["qualifies"] else "Pro required"
        print(f"  {c['name']:<28} {c['sku']:<6} {c['state']:<9} -> {tag}")

    print("\nCHECK 1 — workspaces where Free users are prompted to upgrade")
    if blocked_ws:
        print(f"  {len(blocked_ws)} workspace(s):")
        for r in blocked_ws[:40]:
            where = f'{r["capacity"]} ({r["sku"]})' if r["sku"] else r["mode"]
            print(f"    {r['workspace']:<34} {where:<24} reports={r['reports_in_workspace']}")
        if len(blocked_ws) > 40:
            print(f"    ... and {len(blocked_ws) - 40} more (see CSV)")
    else:
        print("  none ✅")

    print("\nCHECK 2 — split models (report on qualifying capacity, model is not)")
    if split_rows:
        print(f"  {len(split_rows)} report(s):")
        for r in split_rows[:40]:
            print(f"    '{r['report']}'  [{r['report_capacity']}]  ->  model in "
                  f"'{r['model_workspace']}' [{r['model_capacity']}]")
        if len(split_rows) > 40:
            print(f"    ... and {len(split_rows) - 40} more (see CSV)")
    else:
        print("  none ✅")
    print("=" * 72)

    report = {
        "capacities": [{"name": c["name"], "sku": c["sku"], "qualifies": c["qualifies"]}
                       for c in cap_by_id.values()],
        "summary": {
            "workspaces_scanned": len(ws_rows),
            "workspaces_free_blocked": len(blocked_ws),
            "split_model_reports": len(split_rows),
        },
        "workspaces_free_blocked": blocked_ws,
        "split_model_reports": split_rows,
    }
    with open("license-report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nWrote license-report.json")

    if not report_only:
        with open("license_workspaces.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ws_rows[0].keys()) if ws_rows else
                               ["workspace", "workspace_id", "capacity", "sku", "mode",
                                "free_users_can_view", "reports_in_workspace"])
            w.writeheader(); w.writerows(ws_rows)
        with open("license_split_models.csv", "w", newline="") as f:
            cols = ["report", "report_workspace", "report_capacity",
                    "model_workspace", "model_capacity"]
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader(); w.writerows(split_rows)
        print("Wrote license_workspaces.csv, license_split_models.csv")

    return 1 if (blocked_ws or split_rows) else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Diagnose Fabric/Power BI Free-user licensing gaps.")
    p.add_argument("--report-only", action="store_true", help="JSON only, skip CSVs")
    args = p.parse_args()
    try:
        sys.exit(main(report_only=args.report_only))
    except PermissionError as exc:
        print(f"ERROR: {exc}")
        sys.exit(2)
