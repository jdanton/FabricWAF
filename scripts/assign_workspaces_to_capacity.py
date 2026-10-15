"""
assign_workspaces_to_capacity.py

Bulk-assigns workspaces that are in "Pro / Shared" mode (not on any capacity) to a
target Fabric capacity — the remediation for the Free-user "upgrade to Pro" prompts
that check_licensing.py surfaces.

SAFE BY DEFAULT: this is a dry run unless you pass --apply. It only moves standard
(non-personal), active workspaces that are NOT currently on any capacity. Workspaces
already on a capacity (e.g. an F16/F8) are left alone unless --include-on-capacity.

⚠️  CAPACITY SIZING: moving many workspaces onto a single F64 can overload it. Use
    --limit to move in reviewable batches and watch the Fabric Capacity Metrics app
    between batches. Consider spreading load across the F64 and the P2.

Authentication (DefaultAzureCredential, two scopes):
  - Power BI admin  — list workspaces + resolve the target capacity (Tenant.Read.All)
  - Fabric          — assign each workspace (POST /v1/workspaces/{id}/assignToCapacity)
Assignment requires the caller to be an ADMIN of each workspace. Tenant admins are NOT
workspace admins by default (so plain --apply returns 403 everywhere). Pass --elevate:
the script uses the admin API to grant itself workspace-Admin (which bypasses
membership), assigns, and by default REMOVES that grant afterward — the programmatic
equivalent of the portal's Capacity settings page. The capacity assignment persists
after the grant is removed. Without --elevate, non-member workspaces are SKIPPED.

Usage:
  python assign_workspaces_to_capacity.py                              # dry run, auto-target the F64
  python assign_workspaces_to_capacity.py --capacity <name|id>         # choose the target explicitly
  python assign_workspaces_to_capacity.py --elevate --limit 25 --apply # tenant admin: move first 25

Exit codes: 0 ok (or clean dry run), 1 some assignments failed, 2 setup failure.
"""

import argparse
import base64
import csv
import json
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

PBI = "https://api.powerbi.com/v1.0/myorg"
FABRIC = "https://api.fabric.microsoft.com/v1"
PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"

_credential = DefaultAzureCredential()


def _pbi_headers():
    return {"Authorization": f"Bearer {_credential.get_token(PBI_SCOPE).token}",
            "Content-Type": "application/json"}


def _fabric_headers():
    return {"Authorization": f"Bearer {_credential.get_token(FABRIC_SCOPE).token}",
            "Content-Type": "application/json"}


def _get(url, path):
    for _ in range(6):
        r = requests.get(url, headers=_pbi_headers(), timeout=60)
        if r.status_code == 429:
            wait = min(int(r.headers.get("Retry-After", 10)), 60)
            print(f"  [429] throttled — waiting {wait}s ...", flush=True)
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            raise PermissionError(f"HTTP {r.status_code} on {path} -> needs a "
                                  "Fabric/Power BI administrator (Tenant.Read.All).")
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _list(path):
    return _get(f"{PBI}/{path}", path).get("value", [])


def _paged(path, top=5000):
    out, skip = [], 0
    while True:
        batch = _get(f"{PBI}/{path}?$top={top}&$skip={skip}", path).get("value", [])
        out.extend(batch)
        if len(batch) < top:
            return out
        skip += top


def _lc(v):
    return (v or "").lower()


def is_f64_plus(sku):
    s = (sku or "").upper()
    return s.startswith("F") and s[1:].isdigit() and int(s[1:]) >= 64


def resolve_target(capacities, wanted):
    """Return the target capacity dict, or exit with guidance."""
    if wanted:
        w = wanted.lower()
        for c in capacities:
            if _lc(c.get("id")) == w or (c.get("displayName") or "").lower() == w:
                return c
        print(f"ERROR: capacity '{wanted}' not found. Available:")
        for c in capacities:
            print(f"  {c.get('displayName')}  ({c.get('sku')})  {c.get('id')}")
        sys.exit(2)
    # auto-pick the single F64+ (non-trial) capacity
    f64 = [c for c in capacities if is_f64_plus(c.get("sku")) and c.get("state") == "Active"]
    if len(f64) == 1:
        return f64[0]
    print("ERROR: could not auto-pick a target — pass --capacity <name|id>. F64+ found:")
    for c in f64:
        print(f"  {c.get('displayName')}  ({c.get('sku')})  {c.get('id')}")
    sys.exit(2)


def assign(workspace_id, capacity_id):
    """POST assignToCapacity. Returns (ok, note)."""
    url = f"{FABRIC}/workspaces/{workspace_id}/assignToCapacity"
    r = requests.post(url, headers=_fabric_headers(),
                      json={"capacityId": capacity_id}, timeout=60)
    if r.status_code in (200, 202):
        return True, ""
    if r.status_code in (401, 403):
        return False, "not a workspace admin — assign via Admin portal Capacity settings"
    if r.status_code == 404:
        return False, "workspace not found / not assignable via API"
    return False, f"HTTP {r.status_code}: {r.text[:150]}"


def _caller_upn():
    """Read the caller's UPN from the Power BI access token (no extra deps)."""
    seg = _credential.get_token(PBI_SCOPE).token.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    claims = json.loads(base64.urlsafe_b64decode(seg))
    return claims.get("upn") or claims.get("unique_name") or claims.get("preferred_username")


def add_admin(workspace_id, upn):
    """Admin API: grant a user workspace-Admin — bypasses workspace membership."""
    r = requests.post(f"{PBI}/admin/groups/{workspace_id}/users", headers=_pbi_headers(),
                      json={"emailAddress": upn, "groupUserAccessRight": "Admin"}, timeout=60)
    return r.status_code in (200, 201)


def remove_admin(workspace_id, upn):
    """Admin API: remove a user's workspace access (undo the temporary grant)."""
    r = requests.delete(f"{PBI}/admin/groups/{workspace_id}/users/{upn}",
                        headers=_pbi_headers(), timeout=60)
    return r.status_code in (200, 204)


def main():
    p = argparse.ArgumentParser(description="Assign Pro/Shared workspaces to a capacity.")
    p.add_argument("--capacity", help="Target capacity name or id (default: auto-pick the F64).")
    p.add_argument("--apply", action="store_true", help="Actually assign (default: dry run).")
    p.add_argument("--limit", type=int, default=0, help="Only move the first N (0 = all).")
    p.add_argument("--include-on-capacity", action="store_true",
                   help="Also move workspaces already on a (sub-F64) capacity.")
    p.add_argument("--elevate", action="store_true",
                   help="Tenant admins: grant self workspace-Admin via the admin API where "
                        "needed, assign, then remove the grant (bypasses the workspace-admin "
                        "requirement — same effect as the portal).")
    p.add_argument("--keep-elevation", action="store_true",
                   help="With --elevate, leave the temporary admin grants in place "
                        "(default: remove them after assigning).")
    args = p.parse_args()

    print("Reading capacities and workspaces (Admin API)...")
    capacities = _list("admin/capacities")
    workspaces = _paged("admin/groups")
    cap_by_id = {_lc(c.get("id")): c for c in capacities}
    target = resolve_target(capacities, args.capacity)
    tgt_id, tgt_name, tgt_sku = target.get("id"), target.get("displayName"), target.get("sku")

    # candidates: standard, active, not on a capacity (Pro/Shared)
    candidates = []
    for ws in workspaces:
        if (ws.get("type") or "Workspace") != "Workspace":
            continue
        if (ws.get("state") or "Active") != "Active":
            continue
        on_cap = bool(ws.get("isOnDedicatedCapacity")) and ws.get("capacityId")
        if on_cap and not args.include_on_capacity:
            continue
        if _lc(ws.get("capacityId")) == _lc(tgt_id):
            continue  # already on the target
        candidates.append(ws)

    candidates.sort(key=lambda w: (w.get("name") or "").lower())
    if args.limit:
        candidates = candidates[:args.limit]

    print("\n" + "=" * 70)
    print(f"TARGET CAPACITY : {tgt_name}  ({tgt_sku})")
    if not is_f64_plus(tgt_sku) and not (tgt_sku or "").upper().startswith("P"):
        print(f"  ⚠  {tgt_sku} does NOT license Free users — pick an F64+ or P capacity.")
    def _mode(ws):
        cid = _lc(ws.get("capacityId"))
        if bool(ws.get("isOnDedicatedCapacity")) and cid in cap_by_id:
            c = cap_by_id[cid]
            return f"{c.get('displayName')} ({c.get('sku')})"
        return "Pro / Shared"

    print(f"WORKSPACES TO MOVE : {len(candidates)}"
          + (f"  (--limit {args.limit})" if args.limit else ""))
    for ws in candidates:
        print(f"    {ws.get('name'):<44} [{_mode(ws)}]")

    # Full plan to CSV (gitignored — real workspace names) for review before --apply.
    with open("assign_plan.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["workspace", "workspace_id", "current_mode", "target_capacity", "target_sku"])
        for ws in candidates:
            w.writerow([ws.get("name"), ws.get("id"), _mode(ws), tgt_name, tgt_sku])
    print(f"\nFull plan ({len(candidates)} workspaces) written to assign_plan.csv")
    print("⚠  Sizing: verify the target capacity can absorb this load "
          "(watch the Capacity Metrics app); move in batches with --limit.")
    print("=" * 70)

    if not candidates:
        print("Nothing to move.")
        return 0
    if not args.apply:
        print("\nDRY RUN — no changes made. Re-run with --apply to assign "
              "(add --limit N to batch). Tenant admins: add --elevate.")
        return 0

    upn = None
    if args.elevate:
        upn = _caller_upn()
        if not upn:
            print("ERROR: --elevate could not read the caller UPN from the token.")
            return 2
        after = "left in place" if args.keep_elevation else "removed afterward"
        print(f"  --elevate: '{upn}' gets temporary workspace-Admin where needed, {after}.")

    print(f"\nAssigning {len(candidates)} workspace(s) to {tgt_name} ...")
    assigned, skipped, failed, elevated = 0, [], [], []
    for i, ws in enumerate(candidates, 1):
        wid = ws.get("id")
        ok, note = assign(wid, tgt_id)
        if not ok and args.elevate and "workspace admin" in note:
            if add_admin(wid, upn):
                ok, note = assign(wid, tgt_id)
                if ok:
                    elevated.append(wid)
                    note = "ok (elevated)"
            else:
                note = "elevation failed — do in portal"
        print(f"  [{i}/{len(candidates)}] {ws.get('name'):<40} {'ok' if ok else note}")
        if ok:
            assigned += 1
        elif "workspace admin" in note or "elevation" in note:
            skipped.append((ws.get("name"), note))
        else:
            failed.append((ws.get("name"), note))
        time.sleep(0.3)

    # Remove the temporary admin grants we added (the capacity assignment persists).
    if elevated and not args.keep_elevation:
        print(f"\nRemoving temporary admin grant from {len(elevated)} workspace(s)...")
        stuck = sum(0 if remove_admin(wid, upn) else 1 for wid in elevated)
        if stuck:
            print(f"  ⚠  couldn't remove the grant from {stuck} — remove manually.")

    print("\n" + "=" * 70)
    print(f"  assigned : {assigned}" + (f"  ({len(elevated)} via --elevate)" if elevated else ""))
    print(f"  skipped  : {len(skipped)}")
    print(f"  failed   : {len(failed)}")
    for name, note in (skipped + failed)[:50]:
        print(f"    - {name}: {note}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PermissionError as exc:
        print(f"ERROR: {exc}")
        sys.exit(2)
