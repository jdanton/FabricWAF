"""
Create 100 randomly (badly) named workspaces in Microsoft Fabric
to demonstrate what happens without a naming standard.

Prerequisites:
  pip install azure-identity requests

Usage:
  1. Set your CAPACITY_ID below (or pass via env var)
  2. Run:  python create_bad_workspaces.py
  3. Authenticate when prompted (uses interactive browser login)

Cleanup:
  python create_bad_workspaces.py --cleanup
"""

import random
import re
import string
import time
import argparse
import requests

# ---------------------------------------------------------------------------
# CONFIG — set these before running
# ---------------------------------------------------------------------------
CAPACITY_ID = "1B56BD13-3656-4370-9093-FA22CE8E4D9A"  # or set env var FABRIC_CAPACITY_ID
FABRIC_API = "https://api.fabric.microsoft.com/v1"
WORKSPACE_COUNT = 100
TAG = "[DEMO-CLEANUP]"  # put in description so we can find & delete later
# ---------------------------------------------------------------------------


def sanitize_display_name(name):
    """
    Strip characters the Fabric API rejects in displayName.
    Fabric blocks: \ / * ? " < > | # . ' " ! & ^ ( ) { } [ ] @ = +
    and leading/trailing whitespace.
    Returns the sanitized name, or None if nothing usable remains.
    """
    # Remove disallowed characters; keep letters, digits, spaces, hyphens, underscores
    cleaned = re.sub(r"[\\/*?\"<>|#.\'\"!&^(){}\[\]@=+★📊—]", "", name).strip()
    # Collapse multiple spaces
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned if cleaned else None


def get_token():
    """Get a Fabric access token via interactive browser login."""
    from azure.identity import InteractiveBrowserCredential
    credential = InteractiveBrowserCredential()
    token = credential.get_token("https://api.fabric.microsoft.com/.default")
    return token.token


def generate_bad_names(count=100):
    """
    Generate realistic but inconsistent workspace names — the kind
    that accumulate when 20 different people create workspaces with
    no naming convention over 18 months.
    """
    bad_names = []

    # ---- Category 1: Inconsistent casing & separators ----
    bad_names += [
        "Finance_DataWarehouse_PROD",
        "finance-dw-prod",
        "FIN DW Prod",
        "Finance Data Warehouse - Production",
        "fin.dw.prod",
        "FINANCE-DW-PROD",
        "FinanceDW_Production",
        "finance datawarehouse prod",
        "Fin-DW (Production)",
        "Finance_DW prod",
    ]

    # ---- Category 2: Person-named or team-named ----
    bad_names += [
        "John's Workspace",
        "Sarah Test",
        "Mike Analytics",
        "Data Team Shared",
        "Bob's Pipeline Stuff",
        "Jennifers Reports",
        "Carlos ML experiments",
        "intern-project-2024",
        "New Hire Sandbox",
        "Kevins Copy of Finance",
    ]

    # ---- Category 3: Vague or meaningless ----
    bad_names += [
        "Test",
        "Test2",
        "Test3 - Copy",
        "New Workspace",
        "New Workspace (1)",
        "Untitled",
        "Workspace",
        "delete me",
        "temp",
        "DO NOT DELETE",
        "asdf",
        "zzz_archive",
    ]

    # ---- Category 4: Date-stamped chaos ----
    bad_names += [
        "Sales Report 2024-03",
        "Sales Report March 2024",
        "Sales_Report_Q1_2024",
        "SalesReport_03_2024",
        "sales-report-2024-q1",
        "2024 Sales",
        "Sales (old)",
        "Sales (new)",
        "Sales - FINAL",
        "Sales - FINAL v2",
    ]

    # ---- Category 5: Env confusion ----
    bad_names += [
        "HR Analytics",
        "HR Analytics DEV",
        "HR Analytics - Dev",
        "hr_analytics_development",
        "HR-Analytics-QA",
        "HR Analytics STAGING",
        "HR Analytics Production",
        "HR Analytics PROD",
        "hr-analytics-prod-backup",
        "HR Analytics OLD PROD",
    ]

    # ---- Category 6: Abbreviation soup ----
    bad_names += [
        "MFG_RPT_DLY_V2",
        "FIN_GL_ETL_PROC_WS",
        "MKTG-CMP-ANL",
        "SLS_FCST_ML_DEV",
        "OPS_MON_RT",
        "ACCT_REC_RPT",
        "CUST_360_DW",
        "ENG_CI_CD_PIPE",
        "BizDev Analytics Space",
        "SupplyChain_WH",
    ]

    # ---- Category 7: Mixed languages / emojis / special chars ----
    bad_names += [
        "Finanzas - Producción",
        "データ分析",
        "Reporting 📊",
        "Marketing ★ Campaigns",
        "Sales & Revenue Tracking",
        "R&D Experiments (v3)",
        "Customer Success — Dashboard",
        "P&L Board Review",
    ]

    # ---- Category 8: Random junk to fill to 100 ----
    departments = ["HR", "Finance", "Sales", "Mktg", "Eng", "Ops", "Legal", "Procurement"]
    actions = ["test", "workspace", "project", "sandbox", "demo", "poc", "backup", "archive"]
    suffixes = ["", " v2", " FINAL", " (copy)", " OLD", " new", " 2024", " WIP"]

    while len(bad_names) < count:
        dept = random.choice(departments)
        action = random.choice(actions)
        suffix = random.choice(suffixes)
        sep = random.choice([" ", "-", "_", " - ", ""])
        name = f"{dept}{sep}{action}{suffix}"
        if name not in bad_names:
            bad_names.append(name)

    random.shuffle(bad_names)
    return bad_names[:count]


def create_workspaces(token, names, capacity_id):
    """Create workspaces via Fabric REST API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    created = []
    failed = []

    for i, name in enumerate(names, 1):
        display_name = sanitize_display_name(name)
        if display_name is None:
            print(f"  [{i:3d}/{len(names)}] ⚠ Skipped (no valid chars): {name}")
            failed.append((name, "skipped", "name contained only disallowed characters"))
            continue

        if display_name != name:
            print(f"  [{i:3d}/{len(names)}] ⚠ Sanitized: '{name}' → '{display_name}'")

        payload = {
            "displayName": display_name,
            "description": f"{TAG} Auto-generated bad name for governance demo",
        }

        # Optionally assign to capacity
        if capacity_id and capacity_id != "YOUR_CAPACITY_ID_HERE":
            payload["capacityId"] = capacity_id

        resp = requests.post(f"{FABRIC_API}/workspaces", json=payload, headers=headers)

        if resp.status_code in (200, 201):
            ws = resp.json()
            created.append(ws)
            print(f"  [{i:3d}/{len(names)}] ✓ Created: {display_name}")
        elif resp.status_code == 429:
            # Throttled — wait and retry
            retry_after = int(resp.headers.get("Retry-After", 30))
            print(f"  [{i:3d}/{len(names)}] ⏳ Throttled, waiting {retry_after}s...")
            time.sleep(retry_after)
            resp = requests.post(f"{FABRIC_API}/workspaces", json=payload, headers=headers)
            if resp.status_code in (200, 201):
                created.append(resp.json())
                print(f"  [{i:3d}/{len(names)}] ✓ Created (retry): {display_name}")
            else:
                error = resp.json().get("message", resp.text) if resp.content else resp.text
                failed.append((name, resp.status_code, error))
                print(f"  [{i:3d}/{len(names)}] ✗ Failed: {display_name} ({resp.status_code}): {error}")
        elif resp.status_code == 409:
            error = resp.json().get("message", resp.text) if resp.content else resp.text
            failed.append((name, resp.status_code, error))
            print(f"  [{i:3d}/{len(names)}] ✗ Conflict (already exists?): {display_name} — {error}")
        else:
            error = resp.json().get("message", resp.text) if resp.content else resp.text
            failed.append((name, resp.status_code, error))
            print(f"  [{i:3d}/{len(names)}] ✗ Failed: {display_name} ({resp.status_code}): {error}")

        # Small delay to avoid hammering the API
        time.sleep(0.5)

    return created, failed


def cleanup_demo_workspaces(token):
    """Find and delete all workspaces with the demo tag in their description."""
    headers = {"Authorization": f"Bearer {token}"}

    print("Fetching all workspaces...")
    resp = requests.get(f"{FABRIC_API}/workspaces", headers=headers)
    workspaces = resp.json().get("value", [])

    demo_workspaces = []
    for ws in workspaces:
        # Get details to check description
        detail = requests.get(
            f"{FABRIC_API}/workspaces/{ws['id']}", headers=headers
        ).json()
        if TAG in detail.get("description", ""):
            demo_workspaces.append(detail)

    if not demo_workspaces:
        print("No demo workspaces found. Nothing to clean up.")
        return

    print(f"\nFound {len(demo_workspaces)} demo workspaces to delete:\n")
    for ws in demo_workspaces:
        print(f"  - {ws['displayName']} ({ws['id']})")

    confirm = input(f"\nDelete all {len(demo_workspaces)} workspaces? (yes/no): ")
    if confirm.lower() != "yes":
        print("Cancelled.")
        return

    for i, ws in enumerate(demo_workspaces, 1):
        resp = requests.delete(f"{FABRIC_API}/workspaces/{ws['id']}", headers=headers)
        if resp.status_code in (200, 204):
            print(f"  [{i:3d}/{len(demo_workspaces)}] ✓ Deleted: {ws['displayName']}")
        else:
            print(f"  [{i:3d}/{len(demo_workspaces)}] ✗ Failed: {ws['displayName']} ({resp.status_code})")
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Fabric bad-naming demo")
    parser.add_argument("--cleanup", action="store_true", help="Delete demo workspaces")
    parser.add_argument("--capacity", default=CAPACITY_ID, help="Capacity ID to assign")
    parser.add_argument("--count", type=int, default=WORKSPACE_COUNT, help="Number of workspaces")
    parser.add_argument("--dry-run", action="store_true", help="Print names without creating")
    args = parser.parse_args()

    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"  DRY RUN — {args.count} bad workspace names")
        print(f"{'='*60}\n")
        names = generate_bad_names(args.count)
        for i, name in enumerate(names, 1):
            print(f"  {i:3d}. {name}")
        print(f"\n  Run without --dry-run to actually create these.\n")
        return

    print("\nAuthenticating with Azure...")
    token = get_token()

    if args.cleanup:
        cleanup_demo_workspaces(token)
        return

    names = generate_bad_names(args.count)

    print(f"\n{'='*60}")
    print(f"  Creating {len(names)} badly-named workspaces")
    print(f"  Capacity: {args.capacity}")
    print(f"  Cleanup tag: {TAG}")
    print(f"{'='*60}\n")

    created, failed = create_workspaces(token, names, args.capacity)

    print(f"\n{'='*60}")
    print(f"  Done! Created: {len(created)} | Failed: {len(failed)}")
    print(f"  To clean up later: python {__file__} --cleanup")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()