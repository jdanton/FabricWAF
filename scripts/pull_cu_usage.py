"""
pull_cu_usage.py

Pulls Capacity Unit (CU) consumption **broken down by workspace** for a given
Fabric capacity over a trailing window (default: 7 days).

Why this talks to a semantic model and not a "usage" REST endpoint
------------------------------------------------------------------
Fabric has no first-party REST API that returns CU by workspace:
  - The Azure "Fabric Capacities - List Usages" API returns capacity-level
    CU % over time only (no workspace breakdown).
  - The Admin/monitoring APIs expose daily aggregates, not per-workspace CU.
The authoritative source is the **Microsoft Fabric Capacity Metrics app**'s
semantic model. We query it with DAX through the Power BI *Execute Queries*
REST API. (Retention in that model is ~14 days at the timepoint grain, so a
7-day window is always covered.)

Prerequisites
-------------
  - The "Microsoft Fabric Capacity Metrics" app is installed in the tenant and
    the calling identity has read/Build access to its semantic model.
  - Tenant setting "Semantic Model Execute Queries REST API"
    (DatasetExecuteQueries) is ON.  [confirmed ON in tenant-settings-report.json]
  - If authenticating as a service principal, "Service principals can use
    Fabric APIs" must also allow it, and the SP needs access to the model.

Authentication
--------------
  DefaultAzureCredential against the Power BI scope — same pattern as
  configure_capacity.py (works with `az login` or a managed identity / SP).

Environment variables
----------------------
  FABRIC_CAPACITY_ID     GUID of the capacity to report on (required for the
                         report; not needed for --discover / --schema).
  METRICS_DATASET_ID     Semantic model (dataset) GUID of the Capacity Metrics
                         app. Required unless you use --discover to find it.
  METRICS_GROUP_ID       (optional) Workspace GUID the model lives in. If set,
                         the group-scoped Execute Queries route is used.

Typical flow
------------
  1. Find the metrics model:      python3 pull_cu_usage.py --discover
  2. (optional) inspect schema:   METRICS_DATASET_ID=<id> python3 pull_cu_usage.py --schema
  3. Pull the report:             FABRIC_CAPACITY_ID=<cap> METRICS_DATASET_ID=<id> \
                                    python3 pull_cu_usage.py --days 7 --csv cu_by_workspace.csv

Exit codes
----------
  0  Query succeeded
  1  Configuration missing / query failed
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

# Dynamic M query parameters that gate the DirectQuery facts (from --parameters).
# The Capacity Metrics app leaves these at inert defaults until a report slicer
# binds them; --bind-params sets them via the Update Parameters REST API so a
# headless DAX query returns data. Overridable in case a future app build renames.
CAP_PARAM = os.environ.get("METRICS_CAP_PARAM", "DefaultCapacityID")
START_PARAM = os.environ.get("METRICS_START_PARAM", "StartDate")
END_PARAM = os.environ.get("METRICS_END_PARAM", "EndDate")

# Default CU-by-workspace query. Pinned to the installed Capacity Metrics model
# schema (verified via --schema): daily-grain fact 'Metrics By Item And Day'
# holds Date, Capacity Id, Workspace Id, and CU (s); names come from 'Workspaces'.
# Tokens {{CAPACITY_ID}} and {{DAYS}} are substituted before sending.
#
# GROUPBY + SUMX(CURRENTGROUP()) is deliberate: it sums CU only within each
# capacity/date-filtered group. (A CALCULATE(SUM()) over a table variable would
# drop the filter and total every date/capacity for the workspace.) The capacity
# GUID match is case-insensitive because DAX text equality ignores case.
#
# If a later app version renames these, run `--schema` and adjust:
#   'Metrics By Item And Day'[CU (s)]         -> CU-seconds column
#   'Metrics By Item And Day'[Date]           -> date column
#   'Metrics By Item And Day'[Capacity Id]    -> capacity filter key
#   'Items'[Workspace name] (primary) / 'Workspaces'[Workspace name] (fallback)
# Names resolve from Items first (covers everything that consumed CU), then
# Workspaces; blank Workspace Id => capacity/system-level ops (labelled, not null).
DEFAULT_DAX = """
DEFINE
    VAR __CapacityId = "{{CAPACITY_ID}}"
    VAR __MaxDate = CALCULATE( MAX( 'Metrics By Item And Day'[Date] ), ALL( 'Metrics By Item And Day' ) )
    VAR __StartDate = __MaxDate - {{DAYS}}
    VAR __Filtered =
        FILTER(
            ALL( 'Metrics By Item And Day' ),
            'Metrics By Item And Day'[Capacity Id] = __CapacityId
                && 'Metrics By Item And Day'[Date] > __StartDate
        )
    VAR __ByWorkspace =
        GROUPBY(
            __Filtered,
            'Metrics By Item And Day'[Workspace Id],
            "CU_seconds", SUMX( CURRENTGROUP(), 'Metrics By Item And Day'[CU (s)] )
        )
EVALUATE
    SELECTCOLUMNS(
        ADDCOLUMNS(
            __ByWorkspace,
            "Workspace",
                VAR __id = 'Metrics By Item And Day'[Workspace Id]
                RETURN
                    COALESCE(
                        MAXX( FILTER( ALL( 'Items' ), 'Items'[Workspace Id] = __id ), 'Items'[Workspace name] ),
                        MAXX( FILTER( ALL( 'Workspaces' ), 'Workspaces'[Workspace Id] = __id ), 'Workspaces'[Workspace name] ),
                        IF( ISBLANK( __id ) || __id = "",
                            "(capacity-level / no workspace)",
                            "(unresolved: " & __id & ")" )
                    )
        ),
        "Workspace", [Workspace],
        "Workspace Id", 'Metrics By Item And Day'[Workspace Id],
        "CU (s)", [CU_seconds]
    )
ORDER BY [CU (s)] DESC
""".strip()

# Diagnostic: which capacities actually have item-level CU in the window, with
# their names/SKUs/GUIDs. Use this to find the right FABRIC_CAPACITY_ID when a
# report comes back empty. {{DAYS}} is substituted before sending.
CAPACITIES_DAX = """
DEFINE
    VAR __MaxDate = CALCULATE( MAX( 'Metrics By Item And Day'[Date] ), ALL( 'Metrics By Item And Day' ) )
    VAR __StartDate = __MaxDate - {{DAYS}}
    VAR __Filtered =
        FILTER( ALL( 'Metrics By Item And Day' ), 'Metrics By Item And Day'[Date] > __StartDate )
    VAR __ByCap =
        GROUPBY(
            __Filtered,
            'Metrics By Item And Day'[Capacity Id],
            "CU_seconds", SUMX( CURRENTGROUP(), 'Metrics By Item And Day'[CU (s)] )
        )
EVALUATE
    SELECTCOLUMNS(
        ADDCOLUMNS(
            __ByCap,
            "Capacity name",
                LOOKUPVALUE( 'Capacities'[Capacity name], 'Capacities'[Capacity Id], 'Metrics By Item And Day'[Capacity Id] ),
            "SKU",
                LOOKUPVALUE( 'Capacities'[SKU], 'Capacities'[Capacity Id], 'Metrics By Item And Day'[Capacity Id] )
        ),
        "Capacity name", [Capacity name],
        "SKU", [SKU],
        "Capacity Id", 'Metrics By Item And Day'[Capacity Id],
        "CU (s)", [CU_seconds]
    )
ORDER BY [CU (s)] DESC
""".strip()

# Probe: row counts across key tables in one call. If the fact tables read 0/blank
# but dimensions have rows, the model's facts are parameter/DirectQuery-gated.
PROBE_DAX = (
    'EVALUATE ROW('
    '"Capacities", COUNTROWS( \'Capacities\' ), '
    '"Workspaces", COUNTROWS( \'Workspaces\' ), '
    '"Metrics By Item And Day", COUNTROWS( \'Metrics By Item And Day\' ), '
    '"Metrics By Item", COUNTROWS( \'Metrics By Item\' ) )'
)

# Schema-discovery query (works on any model version via DAX INFO functions).
SCHEMA_DAX = {
    "TABLES": "EVALUATE SELECTCOLUMNS( INFO.VIEW.TABLES(), \"Table\", [Name] )",
    "COLUMNS": "EVALUATE SELECTCOLUMNS( INFO.VIEW.COLUMNS(), \"Table\", [Table], \"Column\", [Name], \"DataType\", [DataType] )",
    "MEASURES": "EVALUATE SELECTCOLUMNS( INFO.VIEW.MEASURES(), \"Table\", [Table], \"Measure\", [Name] )",
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()


def _powerbi_headers() -> dict:
    token = _credential.get_token("https://analysis.windows.net/powerbi/api/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str) -> dict:
    resp = requests.get(url, headers=_powerbi_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _execute_queries(dataset_id: str, group_id: str, dax: str) -> list:
    """Run a single DAX query via Execute Queries and return a list of row dicts."""
    if group_id:
        url = f"{POWERBI_API}/groups/{group_id}/datasets/{dataset_id}/executeQueries"
    else:
        url = f"{POWERBI_API}/datasets/{dataset_id}/executeQueries"

    payload = {
        "queries": [{"query": dax}],
        "serializerSettings": {"includeNulls": True},
    }
    resp = requests.post(url, headers=_powerbi_headers(), json=payload, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"Execute Queries failed ({resp.status_code}): {resp.text}")
    tables = resp.json()["results"][0]["tables"]
    return tables[0]["rows"] if tables else []


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_metrics_model() -> int:
    """List datasets whose name looks like the Capacity Metrics app model."""
    print("Searching workspaces for the Capacity Metrics semantic model...\n")
    groups = _get(f"{POWERBI_API}/groups").get("value", [])
    hits = []
    for g in groups:
        try:
            datasets = _get(f"{POWERBI_API}/groups/{g['id']}/datasets").get("value", [])
        except requests.HTTPError:
            continue
        for d in datasets:
            name = d.get("name", "")
            if "capacity metrics" in name.lower() or "fabric capacity" in name.lower():
                hits.append((g, d))

    if not hits:
        print("No dataset matching 'Capacity Metrics' found. Ensure the")
        print("'Microsoft Fabric Capacity Metrics' app is installed and you have access.")
        return 1

    print(f"Found {len(hits)} candidate model(s):\n")
    for g, d in hits:
        print(f"  Dataset : {d.get('name')}")
        print(f"    METRICS_DATASET_ID={d.get('id')}")
        print(f"    METRICS_GROUP_ID={g.get('id')}   (workspace: {g.get('name')})\n")
    return 0


def list_parameters(dataset_id: str, group_id: str) -> int:
    """List the semantic model's M parameters (name, type, current value)."""
    if group_id:
        url = f"{POWERBI_API}/groups/{group_id}/datasets/{dataset_id}/parameters"
    else:
        url = f"{POWERBI_API}/datasets/{dataset_id}/parameters"
    print("Semantic model parameters:\n")
    params = _get(url).get("value", [])
    if not params:
        print("  (none reported — model exposes no updatable M parameters)")
        return 0
    for p in params:
        print(f"  name={p.get('name')!r}  type={p.get('type')}  "
              f"required={p.get('isRequired')}  current={p.get('currentValue')!r}")
    return 0


def probe_tables(dataset_id: str, group_id: str) -> int:
    """Print row counts for key tables to see whether facts are gated/empty."""
    print("Row counts (blank/None => table returns no data headlessly):\n")
    try:
        rows = _execute_queries(dataset_id, group_id, PROBE_DAX)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    _print_table(rows)
    return 0


def list_capacities(dataset_id: str, group_id: str, days: int) -> int:
    """List capacities that have item-level CU in the window (name, SKU, GUID)."""
    dax = CAPACITIES_DAX.replace("{{DAYS}}", str(days))
    print(f"Capacities with CU activity in the model (trailing {days} days):\n")
    try:
        rows = _execute_queries(dataset_id, group_id, dax)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    _print_table(rows)
    if rows:
        print("\nUse one of the 'Capacity Id' values above as FABRIC_CAPACITY_ID.")
    return 0


def dump_schema(dataset_id: str, group_id: str) -> int:
    """Print the model's tables, columns, and measures (friendly names)."""
    for label, dax in SCHEMA_DAX.items():
        print(f"\n===== {label} =====")
        try:
            rows = _execute_queries(dataset_id, group_id, dax)
        except RuntimeError as exc:
            print(f"  ERROR: {exc}")
            continue
        for row in rows:
            print("  " + " | ".join(f"{k}={v}" for k, v in row.items()))
    return 0


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def set_report_parameters(dataset_id: str, group_id: str, capacity_id: str, days: int) -> bool:
    """
    Bind the Capacity Metrics app's gating M parameters via Update Parameters so
    the DirectQuery facts return data for this capacity/window when queried headlessly.

    Sets the window a day wider than --days on each side to absorb the model's
    UTC_offset; the DAX still trims to exactly `days`. Requires write/owner on the
    dataset. Returns True on success.
    """
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days + 1)).strftime("%Y-%m-%dT00:00:00")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")

    if group_id:
        url = f"{POWERBI_API}/groups/{group_id}/datasets/{dataset_id}/Default.UpdateParameters"
    else:
        url = f"{POWERBI_API}/datasets/{dataset_id}/Default.UpdateParameters"

    payload = {"updateDetails": [
        {"name": CAP_PARAM, "newValue": capacity_id},
        {"name": START_PARAM, "newValue": start},
        {"name": END_PARAM, "newValue": end},
    ]}

    print(f"Binding parameters on model {dataset_id}:")
    print(f"  {CAP_PARAM} = {capacity_id}")
    print(f"  {START_PARAM} = {start}")
    print(f"  {END_PARAM}   = {end}")

    resp = requests.post(url, headers=_powerbi_headers(), json=payload, timeout=60)
    if resp.ok:
        print("  Parameters updated.\n")
        return True
    print(f"  ERROR {resp.status_code}: {resp.text}")
    if resp.status_code in (401, 403):
        print("  -> You need write/owner access on the app dataset to set parameters.")
    elif resp.status_code == 400:
        print("  -> This managed app build may block parameter updates; if so, the")
        print("     read-only DAX-binding path is the alternative.")
    return False


def _render_dax(days: int) -> str:
    return DEFAULT_DAX.replace("{{CAPACITY_ID}}", CAPACITY_ID).replace("{{DAYS}}", str(days))


def _print_table(rows: list) -> None:
    if not rows:
        print("  (no rows returned)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "  " + " | ".join(c.ljust(widths[c]) for c in cols)
    print(header)
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


def run_report(days: int, csv_path: str, dax_file: str, bind_params: bool) -> int:
    if not DATASET_ID:
        print("ERROR: METRICS_DATASET_ID is required. Run with --discover to find it.")
        return 1
    if dax_file:
        with open(dax_file) as fh:
            dax = fh.read()
    else:
        if not CAPACITY_ID:
            print("ERROR: FABRIC_CAPACITY_ID is required for the default query.")
            return 1
        dax = _render_dax(days)

    if bind_params:
        if not CAPACITY_ID:
            print("ERROR: FABRIC_CAPACITY_ID is required to bind parameters.")
            return 1
        if not set_report_parameters(DATASET_ID, GROUP_ID, CAPACITY_ID, days):
            return 1

    print(f"Querying Capacity Metrics model {DATASET_ID}")
    if not dax_file:
        print(f"  Capacity : {CAPACITY_ID}")
        print(f"  Window   : trailing {days} days\n")

    try:
        rows = _execute_queries(DATASET_ID, GROUP_ID, dax)
    except (RuntimeError, KeyError) as exc:
        print(f"ERROR: {exc}")
        print("\nIf this is a missing table/column error, the app's model schema")
        print("differs from the default query. Run `--schema` and adjust the four")
        print("names noted at the top of DEFAULT_DAX (or use --dax with your own query).")
        return 1

    _print_table(rows)
    if not rows and bind_params:
        print("\n  Parameters were set but facts are still empty. If the app's fact")
        print("  tables are Import (not DirectQuery), a dataset refresh may be needed")
        print("  for the new window to load. Otherwise widen --days and retry.")
    if csv_path:
        _write_csv(rows, csv_path)
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull CU usage by workspace for a Fabric capacity via the Capacity Metrics model."
    )
    parser.add_argument("--discover", action="store_true",
                        help="List candidate Capacity Metrics datasets and their IDs, then exit.")
    parser.add_argument("--schema", action="store_true",
                        help="Dump the metrics model's tables/columns/measures, then exit.")
    parser.add_argument("--capacities", action="store_true",
                        help="List capacities that have CU activity (name, SKU, GUID), then exit.")
    parser.add_argument("--parameters", action="store_true",
                        help="List the model's M parameters (e.g. CapacityID), then exit.")
    parser.add_argument("--probe", action="store_true",
                        help="Print row counts for key tables to detect gated/empty facts, then exit.")
    parser.add_argument("--days", type=int, default=7,
                        help="Trailing window in days (default: 7).")
    parser.add_argument("--csv", metavar="PATH", default="",
                        help="Also write results to a CSV file.")
    parser.add_argument("--dax", metavar="FILE", default="",
                        help="Run a custom DAX query from FILE instead of the built-in one.")
    parser.add_argument("--bind-params", action="store_true",
                        help="Set the app's DirectQuery parameters (capacity + date window) "
                             "via Update Parameters before querying. Writes to the app dataset.")
    args = parser.parse_args()

    try:
        if args.discover:
            return discover_metrics_model()
        if args.schema:
            if not DATASET_ID:
                print("ERROR: METRICS_DATASET_ID is required for --schema.")
                return 1
            return dump_schema(DATASET_ID, GROUP_ID)
        if args.parameters:
            if not DATASET_ID:
                print("ERROR: METRICS_DATASET_ID is required for --parameters.")
                return 1
            return list_parameters(DATASET_ID, GROUP_ID)
        if args.probe:
            if not DATASET_ID:
                print("ERROR: METRICS_DATASET_ID is required for --probe.")
                return 1
            return probe_tables(DATASET_ID, GROUP_ID)
        if args.capacities:
            if not DATASET_ID:
                print("ERROR: METRICS_DATASET_ID is required for --capacities.")
                return 1
            return list_capacities(DATASET_ID, GROUP_ID, args.days)
        return run_report(args.days, args.csv, args.dax, args.bind_params)
    except requests.HTTPError as exc:
        print(f"ERROR: HTTP {exc.response.status_code}: {exc.response.text}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
