"""
report_heavy_interactive_ops.py

Reports interactive operations that consumed more than a threshold share
(default 5%) of a Fabric capacity, by inspecting the busiest 30-second
timepoints in a trailing window (default 7 days).

Why it works this way
---------------------
"% of capacity" is a *per-timepoint* metric in the Fabric Capacity Metrics app:
it lives in the 'Timepoint Interactive Detail' table, which is driven by the
'TimePoint' dynamic M parameter. There is no windowed "% of capacity" column,
so this script:
  1. Binds DefaultCapacityID + StartDate/EndDate (the window) via Update Parameters.
  2. Queries 'CU Detail' for the top-N highest-CU timepoints in the window.
  3. For each, sets 'TimePoint' and reads 'Timepoint Interactive Detail' where
     [% of capacity] exceeds the threshold.
This mirrors how you'd hunt overload drivers on the report's timepoint page.

Companion to pull_cu_usage.py (same model, auth, and parameter mechanism).

Prerequisites
-------------
  - Microsoft Fabric Capacity Metrics app installed; caller has read on the model
    and WRITE/owner on it (required to set the M parameters).
  - Tenant setting "Semantic Model Execute Queries REST API" (DatasetExecuteQueries) ON.

Environment variables
----------------------
  FABRIC_CAPACITY_ID   GUID of the capacity to report on (required).
  METRICS_DATASET_ID   Capacity Metrics semantic model (dataset) GUID (required;
                       find via `pull_cu_usage.py --discover`).
  METRICS_GROUP_ID     (optional) Workspace GUID hosting the model.

Exit codes
----------
  0  Report produced (even if zero operations crossed the threshold)
  1  Configuration missing / a query or parameter update failed
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POWERBI_API = "https://api.powerbi.com/v1.0/myorg"

CAPACITY_ID = os.environ.get("FABRIC_CAPACITY_ID", "")
DATASET_ID = os.environ.get("METRICS_DATASET_ID", "")
GROUP_ID = os.environ.get("METRICS_GROUP_ID", "")

# Dynamic M parameters that gate the DirectQuery facts (see pull_cu_usage.py).
CAP_PARAM = os.environ.get("METRICS_CAP_PARAM", "DefaultCapacityID")
START_PARAM = os.environ.get("METRICS_START_PARAM", "StartDate")
END_PARAM = os.environ.get("METRICS_END_PARAM", "EndDate")
TIMEPOINT_PARAM = os.environ.get("METRICS_TIMEPOINT_PARAM", "TimePoint")

# Top-N busiest timepoints to inspect. {{TOPN}} substituted before sending.
BUSIEST_TIMEPOINTS_DAX = """
EVALUATE
    TOPN(
        {{TOPN}},
        SELECTCOLUMNS(
            'CU Detail',
            "Window start time", 'CU Detail'[Window start time],
            "CU (s)", 'CU Detail'[CU (s)],
            "CU limit", 'CU Detail'[CU limit]
        ),
        [CU (s)], DESC
    )
ORDER BY [CU (s)] DESC
""".strip()

# Interactive ops above the threshold at the currently-bound TimePoint.
# 'Timepoint Interactive Detail' carries only [Item] (an id) and [Workspace Id],
# so item/workspace names are resolved from the 'Items' dimension (Item Id ->
# Item name, Workspace Id -> Workspace name), with 'Workspaces' as a fallback and
# the raw id kept for traceability. {{THRESHOLD}} substituted (a fraction, 0.05).
HEAVY_OPS_DAX = """
EVALUATE
    SELECTCOLUMNS(
        FILTER(
            'Timepoint Interactive Detail',
            'Timepoint Interactive Detail'[% of capacity] > {{THRESHOLD}}
        ),
        "Item name",
            VAR __iid = 'Timepoint Interactive Detail'[Item]
            RETURN
                COALESCE(
                    MAXX( FILTER( ALL( 'Items' ), 'Items'[Item Id] = __iid ), 'Items'[Item name] ),
                    __iid
                ),
        "Workspace",
            VAR __wid = 'Timepoint Interactive Detail'[Workspace Id]
            RETURN
                COALESCE(
                    MAXX( FILTER( ALL( 'Items' ), 'Items'[Workspace Id] = __wid ), 'Items'[Workspace name] ),
                    MAXX( FILTER( ALL( 'Workspaces' ), 'Workspaces'[Workspace Id] = __wid ), 'Workspaces'[Workspace name] ),
                    IF( ISBLANK( __wid ) || __wid = "",
                        "(capacity-level / no workspace)",
                        "(unresolved: " & __wid & ")" )
                ),
        "Operation", 'Timepoint Interactive Detail'[Operation],
        "User", 'Timepoint Interactive Detail'[User],
        "Status", 'Timepoint Interactive Detail'[Status],
        "% of capacity", 'Timepoint Interactive Detail'[% of capacity],
        "Total CU (s)", 'Timepoint Interactive Detail'[Total CU (s)],
        "Duration (s)", 'Timepoint Interactive Detail'[Duration (s)],
        "Throttling (s)", 'Timepoint Interactive Detail'[Throttling (s)],
        "Item Id", 'Timepoint Interactive Detail'[Item],
        "Workspace Id", 'Timepoint Interactive Detail'[Workspace Id]
    )
ORDER BY [% of capacity] DESC
""".strip()

# ---------------------------------------------------------------------------
# Auth / HTTP
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()


def _powerbi_headers() -> dict:
    token = _credential.get_token("https://analysis.windows.net/powerbi/api/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _dataset_route(dataset_id: str, group_id: str, suffix: str) -> str:
    if group_id:
        return f"{POWERBI_API}/groups/{group_id}/datasets/{dataset_id}/{suffix}"
    return f"{POWERBI_API}/datasets/{dataset_id}/{suffix}"


def _execute_queries(dataset_id: str, group_id: str, dax: str) -> list:
    url = _dataset_route(dataset_id, group_id, "executeQueries")
    payload = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
    resp = requests.post(url, headers=_powerbi_headers(), json=payload, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"Execute Queries failed ({resp.status_code}): {resp.text}")
    tables = resp.json()["results"][0]["tables"]
    return tables[0]["rows"] if tables else []


def _update_params(dataset_id: str, group_id: str, updates: dict) -> bool:
    """POST Update Parameters for {name: value}. Returns True on success."""
    url = _dataset_route(dataset_id, group_id, "Default.UpdateParameters")
    payload = {"updateDetails": [{"name": k, "newValue": v} for k, v in updates.items()]}
    resp = requests.post(url, headers=_powerbi_headers(), json=payload, timeout=60)
    if resp.ok:
        return True
    print(f"  ERROR updating parameters ({resp.status_code}): {resp.text}")
    if resp.status_code in (401, 403):
        print("  -> Needs write/owner on the app dataset to set parameters.")
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_seconds(value) -> str:
    """Normalise an Execute-Queries datetime to 'YYYY-MM-DDTHH:MM:SS'."""
    s = str(value).replace("Z", "")
    if "." in s:
        s = s.split(".")[0]
    return s[:19]


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


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run(days: int, top: int, threshold: float, csv_path: str, bind_window: bool) -> int:
    if not (CAPACITY_ID and DATASET_ID):
        print("ERROR: FABRIC_CAPACITY_ID and METRICS_DATASET_ID are required.")
        return 1

    # 1. Bind the capacity + window so CU Detail returns this capacity's timeline.
    if bind_window:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=days + 1)).strftime("%Y-%m-%dT00:00:00")
        end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        print(f"Binding window: {CAP_PARAM}={CAPACITY_ID}, {START_PARAM}={start}, {END_PARAM}={end}")
        if not _update_params(DATASET_ID, GROUP_ID, {
            CAP_PARAM: CAPACITY_ID, START_PARAM: start, END_PARAM: end,
        }):
            return 1

    # 2. Find the busiest timepoints in the window.
    print(f"\nFinding the {top} busiest timepoints (trailing {days} days)...")
    try:
        busiest = _execute_queries(
            DATASET_ID, GROUP_ID, BUSIEST_TIMEPOINTS_DAX.replace("{{TOPN}}", str(top)))
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    if not busiest:
        print("  No CU Detail rows — check the window / that the capacity had activity.")
        return 1
    _print_table(busiest)

    # 3. For each busiest timepoint, set TimePoint and pull heavy interactive ops.
    dax = HEAVY_OPS_DAX.replace("{{THRESHOLD}}", repr(float(threshold)))
    collected = []
    print(f"\nInspecting each timepoint for interactive ops > {threshold:.0%} of capacity...")
    for row in busiest:
        tp = _iso_seconds(row.get("[Window start time]"))
        cap_cu = row.get("[CU (s)]")
        if not _update_params(DATASET_ID, GROUP_ID, {TIMEPOINT_PARAM: tp}):
            return 1
        try:
            ops = _execute_queries(DATASET_ID, GROUP_ID, dax)
        except RuntimeError as exc:
            print(f"  {tp}: ERROR {exc}")
            continue
        print(f"  {tp}  (capacity CU {cap_cu}): {len(ops)} op(s) over threshold")
        for op in ops:
            collected.append({
                "Timepoint": tp,
                "Timepoint capacity CU (s)": cap_cu,
                "Workspace": op.get("[Workspace]"),
                "Item name": op.get("[Item name]"),
                "Operation": op.get("[Operation]"),
                "User": op.get("[User]"),
                "Status": op.get("[Status]"),
                "% of capacity": op.get("[% of capacity]"),
                "Total CU (s)": op.get("[Total CU (s)]"),
                "Duration (s)": op.get("[Duration (s)]"),
                "Throttling (s)": op.get("[Throttling (s)]"),
                "Item Id": op.get("[Item Id]"),
                "Workspace Id": op.get("[Workspace Id]"),
            })

    # 4. Report.
    collected.sort(key=lambda r: (r["% of capacity"] or 0), reverse=True)
    print(f"\n{'='*70}")
    print(f"Interactive operations over {threshold:.0%} of capacity "
          f"across the {top} busiest timepoints: {len(collected)}")
    print(f"{'='*70}")
    _print_table(collected)

    if not collected:
        print(f"\n  Nothing crossed {threshold:.0%}. If your app build stores "
              f"'% of capacity' as 0-100 rather than 0-1,")
        print(f"  re-run with --threshold {threshold*100:g} .")
    if csv_path:
        _write_csv(collected, csv_path)
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report interactive ops exceeding a share of capacity, "
                    "across the busiest timepoints in a window.")
    parser.add_argument("--days", type=int, default=7,
                        help="Trailing window in days (default: 7).")
    parser.add_argument("--top", type=int, default=10,
                        help="How many of the busiest timepoints to inspect (default: 10).")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="Share-of-capacity cutoff as a fraction (default: 0.05 = 5%%).")
    parser.add_argument("--csv", metavar="PATH", default="",
                        help="Also write results to a CSV file.")
    parser.add_argument("--no-bind", action="store_true",
                        help="Skip binding capacity/window params (use if already set this session).")
    args = parser.parse_args()

    try:
        return run(args.days, args.top, args.threshold, args.csv, not args.no_bind)
    except requests.HTTPError as exc:
        print(f"ERROR: HTTP {exc.response.status_code}: {exc.response.text}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
