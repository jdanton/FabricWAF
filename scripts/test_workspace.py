"""
Quick diagnostic — dumps raw role assignments for one workspace
and runs the ownership check against them. No throttle risk.

Usage:
    python3 scripts/test_workspace.py
"""

import json

import requests
from azure.identity import DefaultAzureCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"
WS_ID = "78131a43-c10c-44db-8ab6-41acc36014d6"

credential = DefaultAzureCredential()


def get_headers():
    tok = credential.get_token(
        "https://api.fabric.microsoft.com/.default"
    ).token
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ── 1. Fetch role assignments (member-scoped) ────────────────────────────────
print(f"\n=== /workspaces/{WS_ID}/roleAssignments ===")
resp = requests.get(
    f"{FABRIC_API}/workspaces/{WS_ID}/roleAssignments",
    headers=get_headers(),
    timeout=30,
)
print(f"Status: {resp.status_code}")
if resp.ok:
    member_data = resp.json()
    print(json.dumps(member_data, indent=2))
else:
    print(resp.text)
    member_data = {"value": []}


# ── 2. Fetch role assignments (admin-scoped) ─────────────────────────────────
print(f"\n=== /admin/workspaces/{WS_ID}/users ===")
resp2 = requests.get(
    f"{FABRIC_API}/admin/workspaces/{WS_ID}/users",
    headers=get_headers(),
    timeout=30,
)
print(f"Status: {resp2.status_code}")
if resp2.ok:
    admin_data = resp2.json()
    print(json.dumps(admin_data, indent=2))
else:
    print(resp2.text)
    admin_data = {"accessDetails": []}


# ── 3. Show what check_workspace_ownership now sees ──────────────────────────
print("\n=== Field extraction (fixed code behaviour) ===")
for label, assignments in [
    ("member /roleAssignments", member_data.get("value", [])),
    ("admin /users", admin_data.get("accessDetails", [])),
]:
    print(f"\n  [{label}]")
    for a in assignments:
        principal = a.get("principal", {})
        ptype = principal.get("type", a.get("principalType", ""))
        ident = principal.get("id", a.get("identifier", a.get("id", "?")))
        email = (
            principal.get("userDetails", {}).get("userPrincipalName")
            or principal.get("userPrincipalName")
            or a.get("emailAddress")
            or ""
        )
        role = (
            a.get("role")
            or a.get("workspaceRole")
            or a.get("workspaceAccessDetails", {}).get("workspaceRole", "")
        )
        print(
            f"    role={role!r:12} ptype={ptype!r:20} "
            f"email={email!r:30} ident={ident!r}"
        )
        if ptype == "User" and role in ("Admin", "Member", "Contributor"):
            print(
                f"    *** VIOLATION: individual user "
                f"'{email or ident}' holds '{role}' ***"
            )
