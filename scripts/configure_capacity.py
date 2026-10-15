"""
configure_capacity.py

Applies the configuration best practices defined in capacity-best-practices.md
to a Microsoft Fabric (F SKU) capacity.

Settings applied:
  - Administration members       (Fabric-Capacity-Admins group only)       Power BI API
  - Capacity-level surge protection  (enabled; 75% reject / 50% recover)  Fabric REST API
  - Capacity overage threshold   (enabled, <= 1/3 of daily CU-hours)       Fabric REST API
  - Capacity overload notifications  (enabled)                            Power BI API

Removed in October 2026 (no effect on F SKUs):
  - Per-workload memory % / query timeout via the Premium Gen1 workloads API
  - "Autoscale for F64+" -- a Premium P-SKU feature. Burst headroom on F SKUs
    now comes from capacity overage (above) and on-demand billing for Spark
    (an opt-in, per-capacity decision -- see capacity-best-practices.md).

Existing surge-protection and overage settings that are already within policy
are left alone, so per-capacity tuning done in the Capacity Metrics app is
not overwritten.

Authentication:
  Uses DefaultAzureCredential, which picks up the managed identity on
  fabric-gh-runner automatically. The surge-protection and overage APIs need
  the identity to be a **capacity admin** (a member of Fabric-Capacity-Admins).
  Azure RBAC alone isn't enough.

Environment variables:
  FABRIC_CAPACITY_ID   GUID of the Fabric capacity to configure (required)
  FABRIC_ADMINS_GROUP  Object ID of the Fabric-Capacity-Admins Entra group (required)

Exit codes:
  0  All settings applied (or already correct)
  1  One or more settings could not be applied
"""

import argparse
import json
import os
import sys

import requests
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POWERBI_API = "https://api.powerbi.com/v1.0/myorg"
FABRIC_API = "https://api.fabric.microsoft.com/v1"

CAPACITY_ID = os.environ.get("FABRIC_CAPACITY_ID", "")
ADMINS_GROUP_OID = os.environ.get("FABRIC_ADMINS_GROUP", "")

# Best-practice target values -- mirrors capacity-best-practices.md

# Capacity-level surge protection (24-hour background %). Starting points only;
# tune per capacity from the Metrics app "Background rejection" chart. Any
# enabled configuration is accepted as compliant so tuning isn't overwritten.
SURGE_REJECTION_THRESHOLD = 75   # valid 15-100
SURGE_RECOVERY_THRESHOLD = 50    # must be > 5 and < rejection threshold

# Capacity overage is billed at 3x PAYG. Microsoft's guidance is to keep the
# rolling 24-hour threshold below 1/3 of the SKU's daily CU-hours (the
# break-even with scaling up). We target 10% and flag anything above 1/3.
OVERAGE_TARGET_FRACTION = 0.10
OVERAGE_MAX_FRACTION = 1 / 3

_SKU_CUS = {
    "F2": 2, "F4": 4, "F8": 8, "F16": 16, "F32": 32,
    "F64": 64, "F128": 128, "F256": 256, "F512": 512,
    "F1024": 1024, "F2048": 2048, "F4096": 4096, "F8192": 8192,
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()


def _powerbi_headers() -> dict:
    token = _credential.get_token("https://analysis.windows.net/powerbi/api/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _fabric_headers() -> dict:
    token = _credential.get_token("https://api.fabric.microsoft.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers_for(url: str) -> dict:
    return _fabric_headers() if url.startswith(FABRIC_API) else _powerbi_headers()


def _get(url: str) -> dict:
    resp = requests.get(url, headers=_headers_for(url), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _patch(url: str, payload: dict, dry_run: bool) -> bool:
    """PATCH and return True on success. Prints what would be sent in dry-run mode."""
    if dry_run:
        print(f"  [dry-run] PATCH {url}")
        print(f"  [dry-run] body: {json.dumps(payload, indent=4)}")
        return True
    resp = requests.patch(url, headers=_headers_for(url), json=payload, timeout=30)
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


def _sku_name(capacity: dict) -> str:
    """The Power BI API returns sku as a string ("F64"); tolerate the {name: ...} shape too."""
    sku = capacity.get("sku", "")
    return sku.get("name", "") if isinstance(sku, dict) else sku


def configure_surge_protection(capacity_id: str, dry_run: bool) -> bool:
    """
    Enable capacity-level surge protection if it is disabled.
    An already-enabled configuration is treated as tuned and left alone.
    """
    print("\n[2/4] Capacity-level surge protection")
    url = f"{FABRIC_API}/capacities/{capacity_id}/surgeProtection"
    try:
        current = _get(url)
    except requests.HTTPError as exc:
        print(f"  ERROR reading surge protection: {exc}")
        return False

    if current.get("state") == "Enabled":
        print(f"  OK — enabled (reject {current.get('rejectionThreshold')}%, "
              f"recover {current.get('recoveryThreshold')}%)")
        return True

    body = {
        "state": "Enabled",
        "rejectionThreshold": SURGE_REJECTION_THRESHOLD,
        "recoveryThreshold": SURGE_RECOVERY_THRESHOLD,
    }
    print(f"  Enabling: reject {SURGE_REJECTION_THRESHOLD}%, recover {SURGE_RECOVERY_THRESHOLD}%")
    ok = _patch(url, body, dry_run)
    if ok and not dry_run:
        print("  Applied.")
    return ok


def configure_overage(capacity_id: str, sku: str, dry_run: bool) -> bool:
    """
    Make sure capacity overage is enabled with an explicit threshold below the
    1/3-of-daily-CU-hours break-even. Overage is on by default (25%) for new
    capacities, so leaving it untouched is itself a cost decision.
    """
    print("\n[3/4] Capacity overage")
    base_cus = _SKU_CUS.get(sku)
    if base_cus is None:
        print(f"  SKIP — unknown SKU '{sku}'; set the overage threshold manually")
        return True

    daily_cu_hours = base_cus * 24
    target = max(1, int(daily_cu_hours * OVERAGE_TARGET_FRACTION))
    ceiling = int(daily_cu_hours * OVERAGE_MAX_FRACTION)

    url = f"{FABRIC_API}/capacities/{capacity_id}/overageConfiguration"
    try:
        current = _get(url)
    except requests.HTTPError as exc:
        print(f"  ERROR reading overage configuration: {exc}")
        return False

    state = current.get("state")
    threshold = current.get("thresholdCapacityUnitHours", 0)
    if state == "Enabled" and 0 < threshold <= ceiling:
        print(f"  OK — enabled, threshold {threshold} CU-h/24h (ceiling {ceiling})")
        return True

    reason = ("disabled" if state != "Enabled"
              else f"threshold {threshold} CU-h exceeds 1/3 of daily CU-hours ({ceiling})")
    print(f"  {reason} — setting threshold to {target} CU-h/24h "
          f"({int(OVERAGE_TARGET_FRACTION * 100)}% of {daily_cu_hours} daily CU-h for {sku})")
    ok = _patch(url, {"state": "Enabled", "thresholdCapacityUnitHours": target}, dry_run)
    if ok and not dry_run:
        print("  Applied.")
    return ok


def configure_notifications(capacity_id: str, dry_run: bool) -> bool:
    """Enable overload notifications on the capacity."""
    print("\n[4/4] Overload notifications")
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

    sku = _sku_name(capacity) or "unknown"
    print(f"  Capacity : {capacity.get('displayName', CAPACITY_ID)}")
    print(f"  SKU      : {sku}")

    results = [
        configure_admins(capacity, ADMINS_GROUP_OID, dry_run),
        configure_surge_protection(CAPACITY_ID, dry_run),
        configure_overage(CAPACITY_ID, sku, dry_run),
        configure_notifications(CAPACITY_ID, dry_run),
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
