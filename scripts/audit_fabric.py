"""
audit_fabric.py

Scans all Azure subscriptions for Microsoft Fabric capacities, validates each
capacity's region and every workspace and item inside it against governance
standards, then emails a compliance report to an admin and to the Admin-role
members of each non-compliant workspace.

Checks performed:
  1. Capacity region — must be a US Azure region
  2. Workspace naming — must match {BU}-{Function}-{Env} pattern
  3. Workspace ownership — write-level roles (Admin/Member/Contributor) must be
     held by Entra groups or service principals, not individual user accounts
  4. Item naming — every item must match the pattern for its type (naming-standard.md)

Authentication:
  Uses DefaultAzureCredential, which picks up the managed identity of the
  fabric-gh-runner VM automatically. Three separate token scopes are used:
    - Azure Resource Manager  (scan capacities via Resource Graph)
    - Microsoft Fabric API    (list workspaces and items)
    - Microsoft Graph API     (resolve user/group emails for the report)

Configuration (environment variables):
  SMTP_HOST          SMTP server hostname (e.g. smtp.office365.com)
  SMTP_PORT          SMTP port — default 587
  SMTP_USER          SMTP login username
  SMTP_PASSWORD      SMTP login password
  EMAIL_FROM         Sender address shown in the report emails
  EMAIL_ADMIN        Admin recipient — always receives the full report
  EMAIL_DRY_RUN      Set to 'true' to print emails to stdout instead of sending
  REPORT_PATH        Output path for the JSON report (default: audit-report.json)

Usage:
  pip install azure-identity requests
  python audit_fabric.py
  python audit_fabric.py --dry-run        # print emails, do not send
  python audit_fabric.py --report-only    # skip email, write JSON report only
"""

import argparse
import json
import os
import re
import smtplib
import sys
import textwrap
import time
from datetime import UTC, date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FABRIC_API   = "https://api.fabric.microsoft.com/v1"
GRAPH_API    = "https://graph.microsoft.com/v1.0"
ARM_API      = "https://management.azure.com"
RG_API       = f"{ARM_API}/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"

COMPLIANT_REGIONS = {
    "eastus", "eastus2", "westus", "westus2", "westus3",
    "centralus", "northcentralus", "southcentralus", "westcentralus",
}

SMTP_HOST    = os.environ.get("SMTP_HOST", "smtp.office365.com")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM   = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_ADMIN  = os.environ.get("EMAIL_ADMIN", "")
DRY_RUN      = os.environ.get("EMAIL_DRY_RUN", "false").lower() == "true"
REPORT_PATH  = os.environ.get("REPORT_PATH", "audit-report.json")

WRITE_ROLES  = {"Admin", "Member", "Contributor"}

# ---------------------------------------------------------------------------
# Naming patterns — derived from naming-standard.md
# ---------------------------------------------------------------------------

_BU    = r"(?:fin|mktg|hr|eng|sales|ops)"
_ENV   = r"(?:dev|tst|stg|prod)"
_LAYER = r"(?:raw|bronze|silver|gold)"
_FREQ  = r"(?:daily|hourly|weekly|adhoc)"
_TOK   = r"[a-z][a-z0-9_]*"

WORKSPACE_PATTERN = re.compile(rf"^{_BU}-[a-z][a-z0-9-]*-{_ENV}$")

ITEM_PATTERNS: dict[str, re.Pattern] = {
    "Lakehouse":          re.compile(rf"^lh_{_BU}_{_LAYER}_{_ENV}$"),
    "Warehouse":          re.compile(rf"^wh_{_BU}_{_TOK}_{_ENV}$"),
    "DataPipeline":       re.compile(rf"^pl_{_BU}_{_TOK}_to_{_LAYER}_{_FREQ}$"),
    "Dataflow":           re.compile(rf"^df_{_BU}_{_TOK}_{_TOK}_{_LAYER}$"),
    "Notebook":           re.compile(rf"^nb_{_BU}_{_TOK}_{_TOK}$"),
    "SparkJobDefinition": re.compile(rf"^sj_{_BU}_{_TOK}_{_TOK}_{_FREQ}$"),
    "SemanticModel":      re.compile(rf"^sm_{_BU}_{_TOK}_{_ENV}$"),
    "Report":             re.compile(rf"^rpt_{_BU}_{_TOK}_{_TOK}$"),
    "PaginatedReport":    re.compile(rf"^prpt_{_BU}_{_TOK}_{_TOK}$"),
    "KQLDatabase":        re.compile(rf"^kql_{_BU}_{_TOK}_{_ENV}$"),
    "KQLQueryset":        re.compile(rf"^kqs_{_BU}_{_TOK}_{_TOK}$"),
    "Eventstream":        re.compile(rf"^es_{_BU}_{_TOK}_{_TOK}$"),
    "MLExperiment":       re.compile(rf"^exp_{_BU}_{_TOK}_{_TOK}$"),
    "MLModel":            re.compile(rf"^mdl_{_BU}_{_TOK}_v\d+$"),
    "Reflex":             re.compile(rf"^rx_{_BU}_{_TOK}_{_TOK}$"),
    "Environment":        re.compile(rf"^env_{_BU}_{_TOK}_{_ENV}$"),
    "Shortcut":           re.compile(rf"^sc_{_TOK}_{_TOK}$"),
}

# ---------------------------------------------------------------------------
# Auth — one credential, three token scopes
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()

def _token(scope: str) -> str:
    return _credential.get_token(scope).token

def fabric_headers() -> dict:
    return {"Authorization": f"Bearer {_token('https://api.fabric.microsoft.com/.default')}",
            "Content-Type": "application/json"}

def arm_headers() -> dict:
    return {"Authorization": f"Bearer {_token('https://management.azure.com/.default')}",
            "Content-Type": "application/json"}

def graph_headers() -> dict:
    return {"Authorization": f"Bearer {_token('https://graph.microsoft.com/.default')}"}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

# Global 429 counter — reported in the summary so the operator knows if the
# scan was throttled and any results may be incomplete.
_throttle_count = 0
_MAX_THROTTLE_RETRIES = 3
_MAX_RETRY_SLEEP = 120  # respect Fabric's Retry-After (docs show ~55s); cap at 2 min


def get_all(url: str, headers: dict, params: dict | None = None) -> list[dict]:
    """
    GET with Fabric-style continuationUri / OData nextLink pagination.

    The Fabric REST API uses different collection keys depending on the endpoint:
      /v1/workspaces, /v1/workspaces/{id}/items  → "value"
      /v1/admin/workspaces                        → "workspaces"
      /v1/admin/workspaces/{id}/items             → "items"
      /v1/admin/workspaces/{id}/users             → "users"

    We try each known key in order and use the first one that is present.
    429 responses are retried up to _MAX_THROTTLE_RETRIES times, then raised
    so the caller can fall back or skip gracefully.
    """
    global _throttle_count
    _COLLECTION_KEYS = ("value", "workspaces", "items", "users")

    results, next_url = [], url
    throttle_attempts = 0
    while next_url:
        resp = requests.get(next_url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            _throttle_count += 1
            throttle_attempts += 1
            retry_after = min(int(resp.headers.get("Retry-After", 10)), _MAX_RETRY_SLEEP)
            print(
                f"\n  [429] Throttled by Fabric API (attempt {throttle_attempts}/"
                f"{_MAX_THROTTLE_RETRIES}) — waiting {retry_after}s ... "
                f"(total throttles so far: {_throttle_count})",
                flush=True,
            )
            if throttle_attempts > _MAX_THROTTLE_RETRIES:
                print(f"  [429] Giving up on {next_url.split('?')[0]} after {_MAX_THROTTLE_RETRIES} retries")
                resp.raise_for_status()
            time.sleep(retry_after)
            continue  # retry the same URL
        throttle_attempts = 0  # reset on a non-429 response
        resp.raise_for_status()
        body = resp.json()
        for key in _COLLECTION_KEYS:
            if key in body:
                results.extend(body[key])
                break
        next_url = body.get("continuationUri") or body.get("@odata.nextLink")
        params = None  # params are embedded in the continuation URL
    return results


def post_json(url: str, headers: dict, payload: dict) -> dict:
    global _throttle_count
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 429:
        _throttle_count += 1
        retry_after = min(int(resp.headers.get("Retry-After", 10)), _MAX_RETRY_SLEEP)
        print(f"\n  [429] Throttled on POST — waiting {retry_after}s ...", flush=True)
        time.sleep(retry_after)
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

# ---------------------------------------------------------------------------
# 1. Scan Azure Resource Graph for all Fabric capacities
# ---------------------------------------------------------------------------

def scan_capacities() -> list[dict]:
    """
    Uses Azure Resource Graph to find all Microsoft.Fabric/capacities across
    every subscription the managed identity can read.
    """
    print("Scanning Azure Resource Graph for Fabric capacities...")
    query = textwrap.dedent("""
        Resources
        | where type == 'microsoft.fabric/capacities'
        | project
            id,
            name,
            location,
            resourceGroup,
            subscriptionId,
            skuName  = sku.name,
            skuTier  = sku.tier,
            state    = properties.state
    """).strip()

    body = {"query": query}
    data = post_json(RG_API, arm_headers(), body)

    # Resource Graph returns data as a plain object array by default.
    return data.get("data", [])

# ---------------------------------------------------------------------------
# 2. Fabric Admin API — workspaces, items, role assignments
# ---------------------------------------------------------------------------

def scan_workspaces() -> list[dict]:
    """
    List workspaces visible to the caller.

    Tries the Fabric Admin API first (/admin/workspaces), which returns all
    workspaces in the tenant but requires the Fabric Administrator role.
    If the admin endpoint returns 0 results (caller lacks the role), falls back
    to /workspaces, which returns workspaces the caller is a member of.
    """
    h = fabric_headers()
    print("Listing all Fabric workspaces (Admin API)...")
    workspaces = get_all(f"{FABRIC_API}/admin/workspaces", h)

    if not workspaces:
        print(
            "  Admin API returned 0 workspaces — caller may not have the Fabric "
            "Administrator role. Falling back to /workspaces (member-scoped)..."
        )
        workspaces = get_all(f"{FABRIC_API}/workspaces", h)
        print(f"  Found {len(workspaces)} workspaces (member-scoped)")
    else:
        print(f"  Found {len(workspaces)} workspaces (tenant-wide)")

    # Only audit standard Workspace type. Personal, AdminWorkspace, and other
    # system-managed types are not governance targets and will fail item API calls.
    before = len(workspaces)
    workspaces = [ws for ws in workspaces if ws.get("type", "Workspace") == "Workspace"]
    skipped = before - len(workspaces)
    if skipped:
        print(f"  Skipped {skipped} non-standard workspace(s) (Personal, AdminWorkspace, etc.)")

    return workspaces

_SKIP_CODES = (400, 401, 403, 404, 429)

def scan_items(workspace_id: str) -> list[dict]:
    h = fabric_headers()
    # Admin endpoint first; some workspace types return 404 for it.
    try:
        return get_all(f"{FABRIC_API}/admin/workspaces/{workspace_id}/items", h)
    except requests.HTTPError as e:
        if e.response.status_code not in _SKIP_CODES:
            raise
    # Member-scoped fallback — may 401 if the caller is not a member.
    try:
        return get_all(f"{FABRIC_API}/workspaces/{workspace_id}/items", h)
    except requests.HTTPError as e:
        if e.response.status_code not in _SKIP_CODES:
            raise
        return []

def scan_role_assignments(workspace_id: str) -> list[dict]:
    """
    Returns role assignments for a workspace.

    Tries the member-scoped /roleAssignments endpoint first — it has a much
    more generous throttle limit than the admin /users endpoint, and contains
    the same principal type and role data needed for ownership checks.

    Falls back to the admin /users endpoint only if the member call is denied
    (e.g. the runner identity is not a workspace member).
    """
    h = fabric_headers()
    try:
        return get_all(f"{FABRIC_API}/workspaces/{workspace_id}/roleAssignments", h)
    except requests.HTTPError as e:
        if e.response.status_code not in _SKIP_CODES:
            raise
    try:
        return get_all(f"{FABRIC_API}/admin/workspaces/{workspace_id}/users", h)
    except requests.HTTPError as e:
        if e.response.status_code not in _SKIP_CODES:
            raise
        return []

# ---------------------------------------------------------------------------
# 3. Microsoft Graph — resolve display names and email addresses
# ---------------------------------------------------------------------------

_graph_cache: dict[str, dict] = {}

def resolve_principal(object_id: str, principal_type: str) -> dict:
    """
    Return {"displayName": ..., "email": ...} for a user, group, or service principal.
    Results are cached to avoid redundant Graph calls.
    """
    if object_id in _graph_cache:
        return _graph_cache[object_id]

    result = {"displayName": object_id, "email": None}
    try:
        h = graph_headers()
        if principal_type == "User":
            resp = requests.get(
                f"{GRAPH_API}/users/{object_id}",
                headers=h,
                params={"$select": "displayName,mail,userPrincipalName"},
                timeout=15,
            )
            if resp.ok:
                u = resp.json()
                result = {
                    "displayName": u.get("displayName", object_id),
                    "email": u.get("mail") or u.get("userPrincipalName"),
                }

        elif principal_type == "Group":
            resp = requests.get(
                f"{GRAPH_API}/groups/{object_id}",
                headers=h,
                params={"$select": "displayName,mail"},
                timeout=15,
            )
            if resp.ok:
                g = resp.json()
                result = {
                    "displayName": g.get("displayName", object_id),
                    "email": g.get("mail"),
                }
            # Fall back to group owners for a contactable email
            if not result["email"]:
                owners_resp = requests.get(
                    f"{GRAPH_API}/groups/{object_id}/owners",
                    headers=h,
                    params={"$select": "mail,userPrincipalName"},
                    timeout=15,
                )
                if owners_resp.ok:
                    owners = owners_resp.json().get("value", [])
                    if owners:
                        result["email"] = (
                            owners[0].get("mail") or owners[0].get("userPrincipalName")
                        )

        elif principal_type in ("ServicePrincipal", "ServicePrincipalProfile"):
            resp = requests.get(
                f"{GRAPH_API}/servicePrincipals/{object_id}",
                headers=h,
                params={"$select": "displayName"},
                timeout=15,
            )
            if resp.ok:
                result = {"displayName": resp.json().get("displayName", object_id), "email": None}

    except Exception as exc:
        print(f"  Warning: Graph lookup failed for {object_id}: {exc}")

    _graph_cache[object_id] = result
    return result

# ---------------------------------------------------------------------------
# 4. Validation logic
# ---------------------------------------------------------------------------

def check_capacity_region(cap: dict) -> list[dict]:
    region = cap["location"].lower().replace(" ", "")
    if region not in COMPLIANT_REGIONS:
        return [{
            "check": "capacity_region",
            "severity": "high",
            "object": cap["name"],
            "object_type": "Capacity",
            "message": (
                f"Capacity '{cap['name']}' is deployed in '{cap['location']}', "
                f"which is not an approved US region. Approved regions: "
                + ", ".join(sorted(COMPLIANT_REGIONS))
            ),
        }]
    return []

def check_workspace_name(ws: dict) -> list[dict]:
    name = ws.get("displayName", "")
    if not WORKSPACE_PATTERN.match(name):
        return [{
            "check": "workspace_naming",
            "severity": "medium",
            "object": name,
            "object_type": "Workspace",
            "message": (
                f"Workspace '{name}' does not match pattern {{BU}}-{{Function}}-{{Env}} "
                "(e.g. fin-dw-prod). See naming-standard.md."
            ),
        }]
    return []

_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

def check_workspace_ownership(ws_name: str, assignments: list[dict]) -> list[dict]:
    """
    Two distinct checks derived from the Fabric workspace identity standard
    (https://learn.microsoft.com/en-us/fabric/security/workspace-identity):

    1. workspace_admin_user (critical)
       The Admin role is held by an individual user account (user@domain.com).
       Admins should be Entra groups or service principals so that ownership
       is tied to the workspace identity, not a person.

    2. workspace_write_user (high)
       A Member or Contributor role is held by an individual user account.
       Write-level access must go through groups or service principals to
       remain auditable and revocable without per-person management.

    The Fabric Admin API returns these fields per assignment:
      workspaceRole  — Admin | Member | Contributor | Viewer
      principalType  — User | Group | ServicePrincipal | ServicePrincipalProfile
      emailAddress   — user@domain.com (for User principals)
      identifier     — object ID (used when email is absent)
    """
    violations = []
    for a in assignments:
        role  = a.get("workspaceRole", a.get("role", ""))
        ptype = a.get("principalType", a.get("type", ""))
        email = a.get("emailAddress") or a.get("userPrincipalName", "")
        ident = a.get("identifier") or a.get("id") or a.get("principalId", "unknown")

        # Only flag individual user accounts identified by an email address.
        # Groups and service principals are compliant regardless of role.
        if ptype != "User":
            continue

        # Confirm it looks like a real user account (user@domain.com).
        display = email if _EMAIL_RE.match(email or "") else ident

        if role == "Admin":
            violations.append({
                "check": "workspace_admin_user",
                "severity": "critical",
                "object": ws_name,
                "object_type": "Workspace",
                "message": (
                    f"'{display}' is an Admin (owner) of workspace '{ws_name}'. "
                    "Per the Fabric workspace identity standard, workspace ownership "
                    "must be held by an Entra group or service principal — "
                    "not an individual user account."
                ),
                "_principal_email": email or None,
            })
        elif role in ("Member", "Contributor"):
            violations.append({
                "check": "workspace_write_user",
                "severity": "high",
                "object": ws_name,
                "object_type": "Workspace",
                "message": (
                    f"'{display}' holds the '{role}' role in workspace '{ws_name}'. "
                    "Write-level roles must be assigned to Entra groups or service "
                    "principals, not individual user accounts."
                ),
                "_principal_email": email or None,
            })
    return violations

def check_item_names(ws_name: str, items: list[dict]) -> list[dict]:
    violations = []
    for item in items:
        item_name = item.get("displayName", "")
        item_type = item.get("type", "Unknown")
        pattern   = ITEM_PATTERNS.get(item_type)
        if pattern is None:
            continue  # unknown/new item type — skip rather than false-positive
        if not pattern.match(item_name):
            violations.append({
                "check": "item_naming",
                "severity": "medium",
                "object": item_name,
                "object_type": item_type,
                "message": (
                    f"'{item_name}' ({item_type}) in workspace '{ws_name}' "
                    "does not match the required naming pattern. "
                    "See naming-standard.md."
                ),
            })
    return violations

# ---------------------------------------------------------------------------
# 5. Email report generation
# ---------------------------------------------------------------------------

_SEVERITY_COLOR = {"critical": "#7b0000", "high": "#c0392b", "medium": "#e67e22", "low": "#7f8c8d"}
_SEVERITY_BG    = {"critical": "#f9e6e6", "high": "#fdf0ee", "medium": "#fef9f0", "low": "#f8f9fa"}

def _html_table(rows: list[dict], columns: list[str]) -> str:
    th = "".join(
        f'<th style="text-align:left;padding:8px 12px;background:#2c3e50;color:#fff;'
        f'font-size:12px;white-space:nowrap">{c}</th>'
        for c in columns
    )
    trs = ""
    for i, row in enumerate(rows):
        bg = "#fff" if i % 2 == 0 else "#f8f9fa"
        td = "".join(
            f'<td style="padding:7px 12px;font-size:12px;color:#333;border-bottom:1px solid #eee">'
            f'{row.get(c, "")}</td>'
            for c in columns
        )
        trs += f'<tr style="background:{bg}">{td}</tr>'
    return (
        '<table style="border-collapse:collapse;width:100%;margin-bottom:24px">'
        f'<thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'
    )

def _severity_badge(sev: str) -> str:
    color = _SEVERITY_COLOR.get(sev, "#7f8c8d")
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;'
        f'border-radius:10px;font-size:11px;font-weight:600">{sev.upper()}</span>'
    )

def build_admin_html(report: dict) -> str:
    s  = report["summary"]
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    rows_cap = [
        {
            "Capacity": v["capacity_name"],
            "Region": v["location"],
            "SKU": v["sku"],
            "Severity": _severity_badge("high"),
            "Issue": v["message"],
        }
        for v in report["capacity_violations"]
    ]

    ws_violation_rows = []
    for ws in report["workspaces"]:
        for v in ws["violations"]:
            ws_violation_rows.append({
                "Workspace": ws["name"],
                "Object": v["object"],
                "Type": v["object_type"],
                "Severity": _severity_badge(v["severity"]),
                "Issue": v["message"],
            })

    cap_table = _html_table(rows_cap, ["Capacity", "Region", "SKU", "Severity", "Issue"]) if rows_cap else "<p>None ✅</p>"
    ws_table  = _html_table(ws_violation_rows, ["Workspace", "Object", "Type", "Severity", "Issue"]) if ws_violation_rows else "<p>None ✅</p>"

    total_violations = len(report["capacity_violations"]) + sum(
        len(ws["violations"]) for ws in report["workspaces"]
    )
    status_color = "#27ae60" if total_violations == 0 else "#c0392b"
    status_label = "COMPLIANT" if total_violations == 0 else f"{total_violations} VIOLATIONS"

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f8;margin:0;padding:0">
<div style="max-width:900px;margin:32px auto;background:#fff;border-radius:8px;
            box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden">

  <!-- Header -->
  <div style="background:#0078d4;padding:28px 32px">
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700">
      Microsoft Fabric — Governance Compliance Report
    </h1>
    <p style="margin:6px 0 0;color:#cce0f5;font-size:13px">Generated {ts}</p>
  </div>

  <div style="padding:28px 32px">

    <!-- Summary banner -->
    <div style="background:{status_color};color:#fff;padding:14px 20px;border-radius:6px;
                margin-bottom:28px;font-weight:700;font-size:15px">
      {status_label}
    </div>

    <!-- Stats -->
    <table style="width:100%;border-collapse:collapse;margin-bottom:28px">
      <tr>
        {''.join(
            f'<td style="text-align:center;padding:16px;background:#f8f9fa;border-radius:6px;margin:4px">'
            f'<div style="font-size:28px;font-weight:700;color:#0078d4">{v}</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:4px">{k}</div></td>'
            for k, v in [
                ("Capacities scanned", s["capacities_scanned"]),
                ("Region violations", s["capacity_region_violations"]),
                ("Workspaces scanned", s["workspaces_scanned"]),
                ("Naming violations", s["naming_violations"]),
                ("Security violations", s["security_violations"]),
                ("Items scanned", s["items_scanned"]),
            ]
        )}
      </tr>
    </table>

    <h2 style="font-size:16px;color:#1e293b;border-bottom:2px solid #e2e8f0;
               padding-bottom:8px;margin-top:0">Capacity Region Violations</h2>
    {cap_table}

    <h2 style="font-size:16px;color:#1e293b;border-bottom:2px solid #e2e8f0;
               padding-bottom:8px">Workspace & Item Violations</h2>
    {ws_table}

  </div>

  <div style="padding:16px 32px;background:#f8f9fa;font-size:11px;color:#94a3b8;
              border-top:1px solid #e2e8f0">
    Fabric Governance Audit · fabric-gh-runner · {ts}
  </div>
</div>
</body>
</html>
"""

def build_owner_html(ws_name: str, violations: list[dict], owner_email: str) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rows = [
        {
            "Object": v["object"],
            "Type": v["object_type"],
            "Check": v["check"].replace("_", " ").title(),
            "Severity": _severity_badge(v["severity"]),
            "Issue": v["message"],
        }
        for v in violations
    ]
    table = _html_table(rows, ["Object", "Type", "Check", "Severity", "Issue"])
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f8;margin:0;padding:0">
<div style="max-width:800px;margin:32px auto;background:#fff;border-radius:8px;
            box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden">
  <div style="background:#c0392b;padding:24px 32px">
    <h1 style="margin:0;color:#fff;font-size:20px;font-weight:700">
      Action Required — Fabric Governance Violations
    </h1>
    <p style="margin:6px 0 0;color:#fde8e8;font-size:13px">Workspace: {ws_name}</p>
  </div>
  <div style="padding:28px 32px">
    <p style="color:#374151;margin-top:0">
      The following governance violations were detected in workspace
      <strong>{ws_name}</strong>. Please resolve them to remain compliant with
      the Fabric governance standards.
    </p>
    {table}
    <p style="color:#64748b;font-size:12px">
      See <a href="https://github.com/your-org/FabricWAF/blob/main/naming-standard.md"
      style="color:#0078d4">naming-standard.md</a> for the full naming convention.
      For security issues, reassign roles to Entra groups instead of individual users.
    </p>
  </div>
  <div style="padding:14px 32px;background:#f8f9fa;font-size:11px;color:#94a3b8;
              border-top:1px solid #e2e8f0">
    Fabric Governance Audit · {ts}
  </div>
</div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# 6. Email sending
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, html_body: str, dry_run: bool = False) -> None:
    if not to:
        print(f"  Skipping email — no recipient address for: {subject}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = to
    msg.attach(MIMEText(html_body, "html"))

    if dry_run:
        print(f"\n{'='*70}")
        print(f"  DRY RUN — To: {to}")
        print(f"  Subject: {subject}")
        print(f"  Body: {len(html_body)} chars HTML")
        print(f"{'='*70}")
        return

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, [to], msg.as_string())
    print(f"  Sent → {to}")

# ---------------------------------------------------------------------------
# 7. Main
# ---------------------------------------------------------------------------

def main(dry_run: bool = False, report_only: bool = False) -> int:
    run_dry = dry_run or DRY_RUN

    # ---- Capacities --------------------------------------------------------
    capacities = scan_capacities()
    print(f"  Found {len(capacities)} Fabric capacities")

    capacity_violations = []
    # Build a map of Fabric capacity name → ARM details for workspace join
    capacity_map: dict[str, dict] = {}
    for cap in capacities:
        cap_lower = cap["name"].lower()
        capacity_map[cap_lower] = cap
        capacity_violations.extend(check_capacity_region(cap))

    # ---- Workspaces --------------------------------------------------------
    all_workspaces = scan_workspaces()
    print(f"  Found {len(all_workspaces)} workspaces")

    workspace_results = []
    total_items = 0
    naming_violations = 0
    security_violations = 0

    total_ws = len(all_workspaces)
    for idx, ws in enumerate(all_workspaces, 1):
        pct = int(idx / total_ws * 100)
        bar = ("█" * (pct // 5)).ljust(20)
        print(f"\r  [{bar}] {pct:3d}%  ({idx}/{total_ws})", end="", flush=True)

        # Pace API calls — Fabric Admin API throttles per-user per-endpoint.
        # Docs show Retry-After values around 55s, so we slow down proactively
        # to avoid burning retries across 100+ workspaces.
        time.sleep(0.5)

        ws_id   = ws.get("id", "")
        ws_name = ws.get("displayName") or ws.get("name") or ws_id
        ws_violations: list[dict] = []

        # Workspace naming
        ws_violations.extend(check_workspace_name(ws))

        # Workspace role assignments + ownership check
        assignments = scan_role_assignments(ws_id)

        # Collect Admin-role members for the owner email
        owner_emails: list[str] = []
        for a in assignments:
            role  = a.get("workspaceRole", a.get("role", ""))
            ptype = a.get("principalType", a.get("type", ""))
            pid   = a.get("id", a.get("principalId", ""))
            email = a.get("emailAddress") or a.get("userPrincipalName")

            if role == "Admin":
                if not email and ptype in ("User", "Group"):
                    email = resolve_principal(pid, ptype).get("email")
                if email:
                    owner_emails.append(email)

        ws_violations.extend(check_workspace_ownership(ws_name, assignments))

        # Item names
        items = scan_items(ws_id)
        total_items += len(items)
        ws_violations.extend(check_item_names(ws_name, items))

        # Tally
        naming_violations   += sum(1 for v in ws_violations if "naming" in v["check"])
        security_violations += sum(
            1 for v in ws_violations
            if v["check"] in ("workspace_admin_user", "workspace_write_user")
        )

        workspace_results.append({
            "id":           ws_id,
            "name":         ws_name,
            "capacity_id":  ws.get("capacityId"),
            "owner_emails": list(set(owner_emails)),
            "violations":   ws_violations,
        })

    print()  # newline after progress bar

    # ---- Build report ------------------------------------------------------
    report = {
        "generated_at": datetime.now(UTC).isoformat() + "Z",
        "summary": {
            "capacities_scanned":          len(capacities),
            "capacity_region_violations":  len(capacity_violations),
            "workspaces_scanned":          len(all_workspaces),
            "items_scanned":               total_items,
            "naming_violations":           naming_violations,
            "security_violations":         security_violations,
            "total_violations":            (
                len(capacity_violations) + naming_violations + security_violations
            ),
        },
        "capacity_violations": [
            {
                "capacity_name": cap["name"],
                "location":      cap["location"],
                "sku":           cap.get("skuName", ""),
                "message":       v["message"],
            }
            for cap in capacities
            for v in check_capacity_region(cap)
        ],
        "workspaces": workspace_results,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport written to {REPORT_PATH}")

    # ---- Print summary -----------------------------------------------------
    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"  Capacities scanned         : {s['capacities_scanned']}")
    print(f"  Capacity region violations : {s['capacity_region_violations']}")
    print(f"  Workspaces scanned         : {s['workspaces_scanned']}")
    print(f"  Items scanned              : {s['items_scanned']}")
    print(f"  Naming violations          : {s['naming_violations']}")
    print(f"  Security violations        : {s['security_violations']}")
    print(f"  Total violations           : {s['total_violations']}")
    if _throttle_count:
        print(f"  ⚠  API 429 throttles hit   : {_throttle_count}  (some workspaces may use member-scoped fallback data)")
    print(f"{'='*60}\n")

    if report_only:
        return 0 if s["total_violations"] == 0 else 1

    # ---- Email — admin (full report) ----------------------------------------
    today  = date.today().isoformat()
    status = "COMPLIANT" if s["total_violations"] == 0 else f"{s['total_violations']} VIOLATIONS FOUND"
    print("Sending admin report email...")
    send_email(
        to=EMAIL_ADMIN,
        subject=f"[Fabric Governance] {status} — {today}",
        html_body=build_admin_html(report),
        dry_run=run_dry,
    )

    # ---- Email — workspace owners (scoped to their workspace) ---------------
    for ws in workspace_results:
        if not ws["violations"]:
            continue
        for owner_email in ws["owner_emails"]:
            print(f"Sending workspace report to {owner_email} for '{ws['name']}'...")
            send_email(
                to=owner_email,
                subject=f"[Fabric Governance] Action Required — {ws['name']} — {today}",
                html_body=build_owner_html(ws["name"], ws["violations"], owner_email),
                dry_run=run_dry,
            )

    return 0 if s["total_violations"] == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fabric governance audit")
    parser.add_argument("--dry-run",     action="store_true", help="Print emails, do not send")
    parser.add_argument("--report-only", action="store_true", help="Write JSON report, skip email")
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run, report_only=args.report_only))
