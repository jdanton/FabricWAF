"""
configure_capacity.py

Applies the configuration best practices defined in capacity-best-practices.md
to a Microsoft Fabric capacity via the Power BI Admin REST API.

Settings applied:
  - Workload memory limits  (SemanticModel 40%, Dataflow 40%, PaginatedReport 20%)
  - SemanticModel query timeout  (600 s)
  - Capacity overload notifications  (enabled)
  - Workspace assignment permissions  (Fabric-Capacity-Admins group only)
  - Autoscale  (enabled for F64+ SKUs, skipped otherwise)

Authentication:
  Uses DefaultAzureCredential — picks up the managed identity on fabric-gh-runner
  automatically. The identity needs the Fabric Capacity Administrator role
  (provisioned by terraform/policy.tf) at the subscription scope.

Environment variables:
  FABRIC_CAPACITY_ID   GUID of the Fabric capacity to configure (required)
  FABRIC_ADMINS_GROUP  Object ID of the Fabric-Capacity-Admins Entra group (required)

Exit codes:
  0  All settings applied (or already correct)
  1  One or more settings could not be applied
"""

import argparse
import os
import sys

import requests
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POWERBI_API = "https://api.powerbi.com/v1.0/myorg"

CAPACITY_ID = os.environ.get("FABRIC_CAPACITY_ID", "")
ADMINS_GROUP_OID = os.environ.get("FABRIC_ADMINS_GROUP", "")

# Best-practice target values — mirrors capacity-best-practices.md
WORKLOAD_TARGETS = {
    "SemanticModel": {
        "state": "Enabled",
        "maxMemoryPercentageSetByUser": 40,
        "queryTimeout": 600,          # seconds; Power BI field name
    },
    "Dataflow": {
        "state": "Enabled",
        "maxMemoryPercentageSetByUser": 40,
    },
    "PaginatedReport": {
        "state": "Enabled",
        "maxMemoryPercentageSetByUser": 20,
    },
}

# SKU CU counts for autoscale eligibility (F64 = 64 CUs minimum)
_AUTOSCALE_MIN_CUS = 64
_SKU_CUS = {
    "F2": 2, "F4": 4, "F8": 8, "F16": 16, "F32": 32,
    "F64": 64, "F128": 128, "F256": 256, "F512": 512,
    "F1024": 1024, "F2048": 2048,
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()


def _powerbi_headers() -> dict:
    token = _credential.get_token("https://analysis.windows.net/powerbi/api/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(url: str) -> dict:
    resp = requests.get(url, headers=_powerbi_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _patch(url: str, payload: dict, dry_run: bool) -> bool:
    """PATCH and return True on success. Prints what would be sent in dry-run mode."""
    if dry_run:
        import json
        print(f"  [dry-run] PATCH {url}")
        print(f"  [dry-run] body: {json.dumps(payload, indent=4)}")
        return True
    resp = requests.patch(url, headers=_powerbi_headers(), json=payload, timeout=30)
    if resp.ok:
        return True
    print(f"  ERROR {resp.status_code}: {resp.text}")
    return False


# ---------------------------------------------------------------------------
# Configuration steps
# ---------------------------------------------------------------------------

def get_capacity(capacity_id: str) -> dict:
    """Fetch the capacity object to read its SKU and current admin list."""
    data = _get(f"{POWERBI_API}/capacities")
    for cap in data.get("value", []):
        if cap.get("id", "").lower() == capacity_id.lower():
            return cap
    raise ValueError(f"Capacity '{capacity_id}' not found. "
                     "Check FABRIC_CAPACITY_ID and that the managed identity "
                     "has the Fabric Capacity Administrator role.")


def configure_admins(capacity: dict, admins_group_oid: str, dry_run: bool) -> bool:
    """
    Ensure administrationMembers contains exactly the Fabric-Capacity-Admins group.
    """
    print("\n[1/4] Administration members")
    current = capacity.get("administrationMembers", [])
    expected = [admins_group_oid]

    if sorted(current) == sorted(expected):
        print("  OK — already set to Fabric-Capacity-Admins group only")
        return True

    print(f"  Current : {current}")
    print(f"  Expected: {expected}")
    ok = _patch(
        f"{POWERBI_API}/capacities/{capacity['id']}",
        {"administrationMembers": expected},
        dry_run,
    )
    if ok and not dry_run:
        print("  Applied.")
    return ok


def configure_workloads(capacity_id: str, dry_run: bool) -> bool:
    """
    Apply memory and timeout settings to each supported workload.
    Workloads not supported by the capacity SKU are silently skipped.
    """
    print("\n[2/4] Workload memory and timeout settings")
    data = _get(f"{POWERBI_API}/capacities/{capacity_id}/workloads")
    available = {w["name"]: w for w in data.get("value", [])}
    all_ok = True

    for workload_name, targets in WORKLOAD_TARGETS.items():
        if workload_name not in available:
            print(f"  SKIP {workload_name} — not available on this SKU")
            continue

        current = available[workload_name]
        patch_body = {}

        if current.get("state") != targets["state"]:
            patch_body["state"] = targets["state"]

        mem_key = "maxMemoryPercentageSetByUser"
        if current.get(mem_key) != targets.get(mem_key):
            patch_body[mem_key] = targets[mem_key]

        if workload_name == "SemanticModel":
            qt_key = "queryTimeout"
            if current.get(qt_key) != targets.get(qt_key):
                patch_body[qt_key] = targets[qt_key]

        if not patch_body:
            print(f"  OK {workload_name} — already at target values")
            continue

        print(f"  Patching {workload_name}: {patch_body}")
        ok = _patch(
            f"{POWERBI_API}/capacities/{capacity_id}/workloads/{workload_name}",
            patch_body,
            dry_run,
        )
        if ok and not dry_run:
            print(f"  Applied {workload_name}.")
        all_ok = all_ok and ok

    return all_ok


def configure_notifications(capacity_id: str, dry_run: bool) -> bool:
    """Enable overload notifications on the capacity."""
    print("\n[3/4] Overload notifications")
    cap = _get(f"{POWERBI_API}/capacities/{capacity_id}")
    notif = cap.get("notificationSettings", {})

    if notif.get("overloadNotificationsEnabled") is True:
        print("  OK — overload notifications already enabled")
        return True

    print("  Enabling overload notifications...")
    ok = _patch(
        f"{POWERBI_API}/capacities/{capacity_id}",
        {"notificationSettings": {"overloadNotificationsEnabled": True}},
        dry_run,
    )
    if ok and not dry_run:
        print("  Applied.")
    return ok


def configure_autoscale(capacity: dict, dry_run: bool) -> bool:
    """
    Enable autoscale on F64+ capacities.
    Sets max autoscale to 25% above the base SKU CU count.
    Silently skips ineligible SKUs.
    """
    print("\n[4/4] Autoscale")
    sku = capacity.get("sku", {}).get("name", "")
    base_cus = _SKU_CUS.get(sku, 0)

    if base_cus < _AUTOSCALE_MIN_CUS:
        print(f"  SKIP — autoscale requires F64+, this capacity is {sku} ({base_cus} CUs)")
        return True

    max_cus = int(base_cus * 1.25)
    autoscale = capacity.get("capacityUserSettings", {}).get("autoscaleSettings", {})

    if (
        autoscale.get("enabled") is True
        and autoscale.get("maxCapacityUnits") == max_cus
    ):
        print(f"  OK — autoscale already enabled, max {max_cus} CUs")
        return True

    print(f"  Enabling autoscale: max {max_cus} CUs ({sku} base {base_cus} + 25%)")
    ok = _patch(
        f"{POWERBI_API}/capacities/{capacity['id']}",
        {"capacityUserSettings": {
            "autoscaleSettings": {
                "enabled": True,
                "maxCapacityUnits": max_cus,
            }
        }},
        dry_run,
    )
    if ok and not dry_run:
        print("  Applied.")
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(dry_run: bool = False) -> int:
    if not CAPACITY_ID:
        print("ERROR: FABRIC_CAPACITY_ID environment variable is required.")
        return 1
    if not ADMINS_GROUP_OID:
        print("ERROR: FABRIC_ADMINS_GROUP environment variable is required.")
        return 1

    mode = " (DRY RUN)" if dry_run else ""
    print(f"Configuring Fabric capacity '{CAPACITY_ID}'{mode}")
    print("Standards: capacity-best-practices.md\n")

    try:
        capacity = get_capacity(CAPACITY_ID)
    except (ValueError, requests.HTTPError) as exc:
        print(f"ERROR: {exc}")
        return 1

    sku = capacity.get("sku", {}).get("name", "unknown")
    print(f"  Capacity : {capacity.get('displayName', CAPACITY_ID)}")
    print(f"  SKU      : {sku}")

    results = [
        configure_admins(capacity, ADMINS_GROUP_OID, dry_run),
        configure_workloads(CAPACITY_ID, dry_run),
        configure_notifications(CAPACITY_ID, dry_run),
        configure_autoscale(capacity, dry_run),
    ]

    all_ok = all(results)
    print(f"\n{'='*50}")
    print(f"  Result: {'ALL SETTINGS APPLIED' if all_ok else 'ONE OR MORE SETTINGS FAILED'}")
    print(f"{'='*50}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply Fabric capacity best practices")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be changed without applying anything",
    )
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run))
