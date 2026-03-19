"""
validate_fabric.py

Validates all items inside every production Fabric workspace against the
naming standards defined in naming-standard.md and checks that no individual
user accounts hold write-level workspace roles (Contributor / Member / Admin).

Authentication:
    Runs on the fabric-gh-runner Azure VM and uses its system-assigned managed
    identity via DefaultAzureCredential — no secrets or tokens are required.

Environment variables:
    PROD_WORKSPACE_PATTERN  Substring that identifies prod workspaces (default: -prod)
    REPORT_PATH             Where to write the JSON report (default: validation-report.json)

Exit codes:
    0  All checks passed
    1  One or more violations found
"""

import json
import os
import re
import sys

import requests
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FABRIC_API = "https://api.fabric.microsoft.com/v1"
PROD_PATTERN = os.environ.get("PROD_WORKSPACE_PATTERN", "-prod")
REPORT_PATH = os.environ.get("REPORT_PATH", "validation-report.json")

# ---------------------------------------------------------------------------
# Naming patterns
# Derived directly from naming-standard.md
#
# Tokens used in patterns:
#   BU     : fin | mktg | hr | eng | sales | ops
#   Env    : dev | tst | stg | prod
#   Layer  : raw | bronze | silver | gold
#   Freq   : daily | hourly | weekly | adhoc
#   *      : [a-z][a-z0-9_]* — lowercase alphanum, underscore allowed within
# ---------------------------------------------------------------------------

_BU = r"(fin|mktg|hr|eng|sales|ops)"
_ENV = r"(dev|tst|stg|prod)"
_LAYER = r"(raw|bronze|silver|gold)"
_FREQ = r"(daily|hourly|weekly|adhoc)"
_TOKEN = r"[a-z][a-z0-9_]*"

# Maps Fabric item type (as returned by the API) → compiled regex
ITEM_PATTERNS: dict[str, re.Pattern] = {
    # lh_{BU}_{Layer}_{Env}
    "Lakehouse": re.compile(
        rf"^lh_{_BU}_{_LAYER}_{_ENV}$"
    ),
    # wh_{BU}_{Function}_{Env}
    "Warehouse": re.compile(
        rf"^wh_{_BU}_{_TOKEN}_{_ENV}$"
    ),
    # pl_{BU}_{Source}_to_{Layer}_{Freq}
    "DataPipeline": re.compile(
        rf"^pl_{_BU}_{_TOKEN}_to_{_LAYER}_{_FREQ}$"
    ),
    # df_{BU}_{Source}_{Domain}_{Layer}
    "Dataflow": re.compile(
        rf"^df_{_BU}_{_TOKEN}_{_TOKEN}_{_LAYER}$"
    ),
    # nb_{BU}_{Function}_{Domain}
    "Notebook": re.compile(
        rf"^nb_{_BU}_{_TOKEN}_{_TOKEN}$"
    ),
    # sj_{BU}_{Function}_{Domain}_{Freq}
    "SparkJobDefinition": re.compile(
        rf"^sj_{_BU}_{_TOKEN}_{_TOKEN}_{_FREQ}$"
    ),
    # sm_{BU}_{Domain}_{Env}
    "SemanticModel": re.compile(
        rf"^sm_{_BU}_{_TOKEN}_{_ENV}$"
    ),
    # rpt_{BU}_{Domain}_{Audience}
    "Report": re.compile(
        rf"^rpt_{_BU}_{_TOKEN}_{_TOKEN}$"
    ),
    # prpt_{BU}_{Domain}_{Description}
    "PaginatedReport": re.compile(
        rf"^prpt_{_BU}_{_TOKEN}_{_TOKEN}$"
    ),
    # kql_{BU}_{Domain}_{Env}
    "KQLDatabase": re.compile(
        rf"^kql_{_BU}_{_TOKEN}_{_ENV}$"
    ),
    # kqs_{BU}_{Domain}_{Purpose}
    "KQLQueryset": re.compile(
        rf"^kqs_{_BU}_{_TOKEN}_{_TOKEN}$"
    ),
    # es_{BU}_{Source}_{Domain}
    "Eventstream": re.compile(
        rf"^es_{_BU}_{_TOKEN}_{_TOKEN}$"
    ),
    # exp_{BU}_{Domain}_{Technique}
    "MLExperiment": re.compile(
        rf"^exp_{_BU}_{_TOKEN}_{_TOKEN}$"
    ),
    # mdl_{BU}_{Domain}_{Version}   e.g. mdl_fin_fraud_v1
    "MLModel": re.compile(
        rf"^mdl_{_BU}_{_TOKEN}_v\d+$"
    ),
    # rx_{BU}_{Domain}_{Trigger}
    "Reflex": re.compile(
        rf"^rx_{_BU}_{_TOKEN}_{_TOKEN}$"
    ),
    # env_{BU}_{Purpose}_{Env}
    "Environment": re.compile(
        rf"^env_{_BU}_{_TOKEN}_{_ENV}$"
    ),
    # sc_{SourceLakehouse}_{Domain}
    "Shortcut": re.compile(
        rf"^sc_{_TOKEN}_{_TOKEN}$"
    ),
}

# Workspace name pattern: {BU}-{Function}-{Env}  e.g. fin-dw-prod
WORKSPACE_PATTERN = re.compile(
    rf"^{_BU.replace('(', '(?:')}-[a-z][a-z0-9-]*-{_ENV.replace('(', '(?:')}$"
)

# Roles that should not be held by individual User accounts in prod
WRITE_ROLES = {"Admin", "Member", "Contributor"}


# ---------------------------------------------------------------------------
# Fabric API helpers
# ---------------------------------------------------------------------------

def get_headers() -> dict[str, str]:
    """Acquire a Fabric API token via the VM's managed identity."""
    credential = DefaultAzureCredential()
    token = credential.get_token("https://api.fabric.microsoft.com/.default")
    return {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
    }


def paginate(url: str, headers: dict) -> list[dict]:
    """Follow Fabric API continuation tokens and return all items."""
    results = []
    while url:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        results.extend(body.get("value", []))
        url = body.get("continuationUri")
    return results


def list_prod_workspaces(headers: dict) -> list[dict]:
    workspaces = paginate(f"{FABRIC_API}/workspaces", headers)
    return [
        ws for ws in workspaces
        if PROD_PATTERN in ws["displayName"]
    ]


def list_items(workspace_id: str, headers: dict) -> list[dict]:
    return paginate(f"{FABRIC_API}/workspaces/{workspace_id}/items", headers)


def list_role_assignments(workspace_id: str, headers: dict) -> list[dict]:
    return paginate(
        f"{FABRIC_API}/workspaces/{workspace_id}/roleAssignments", headers
    )


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------

def check_workspace_name(ws: dict) -> list[dict]:
    name = ws["displayName"]
    if not WORKSPACE_PATTERN.match(name):
        return [{
            "workspace": name,
            "item": None,
            "type": "naming",
            "message": (
                f"Workspace name '{name}' does not match pattern "
                "{{BU}}-{{Function}}-{{Env}} (e.g. fin-dw-prod)"
            ),
        }]
    return []


def check_item_names(ws_name: str, items: list[dict]) -> list[dict]:
    violations = []
    for item in items:
        item_name = item.get("displayName", "")
        item_type = item.get("type", "Unknown")
        pattern = ITEM_PATTERNS.get(item_type)

        if pattern is None:
            # Unknown type — skip rather than fail; new Fabric types may not be listed yet
            continue

        if not pattern.match(item_name):
            violations.append({
                "workspace": ws_name,
                "item": item_name,
                "type": "naming",
                "message": (
                    f"'{item_name}' ({item_type}) does not match the required "
                    f"naming pattern. See naming-standard.md for details."
                ),
            })
    return violations


def check_workspace_security(ws_name: str, assignments: list[dict]) -> list[dict]:
    """
    Flag individual User accounts that hold a write-level role (Admin /
    Member / Contributor). In production, only Groups and Service Principals
    should hold these roles — not individual users.
    """
    violations = []
    for assignment in assignments:
        role = assignment.get("role", "")
        principal = assignment.get("principal", {})
        principal_type = principal.get("type", "")
        principal_id = principal.get("id", "unknown")

        if role in WRITE_ROLES and principal_type == "User":
            violations.append({
                "workspace": ws_name,
                "item": None,
                "type": "security",
                "message": (
                    f"Individual user '{principal_id}' has role '{role}'. "
                    "Production workspaces must use Entra groups or service "
                    "principals — not individual accounts."
                ),
            })
    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("Acquiring Fabric API token via managed identity...")
    headers = get_headers()

    print(f"Finding workspaces matching pattern '{PROD_PATTERN}'...")
    workspaces = list_prod_workspaces(headers)

    if not workspaces:
        print(f"No workspaces found matching '{PROD_PATTERN}'. Nothing to validate.")
        report = {
            "summary": {
                "workspaces_checked": 0,
                "items_checked": 0,
                "naming_violations": 0,
                "security_violations": 0,
                "total_violations": 0,
            },
            "violations": [],
        }
        with open(REPORT_PATH, "w") as f:
            json.dump(report, f, indent=2)
        return 0

    all_violations: list[dict] = []
    total_items = 0

    for ws in workspaces:
        ws_id = ws["id"]
        ws_name = ws["displayName"]
        print(f"\n  Workspace: {ws_name}")

        # 1. Workspace name
        all_violations.extend(check_workspace_name(ws))

        # 2. Item names
        items = list_items(ws_id, headers)
        total_items += len(items)
        print(f"    Items: {len(items)}")
        all_violations.extend(check_item_names(ws_name, items))

        # 3. Workspace role assignments
        assignments = list_role_assignments(ws_id, headers)
        all_violations.extend(check_workspace_security(ws_name, assignments))

    naming_violations = sum(1 for v in all_violations if v["type"] == "naming")
    security_violations = sum(1 for v in all_violations if v["type"] == "security")

    report = {
        "summary": {
            "workspaces_checked": len(workspaces),
            "items_checked": total_items,
            "naming_violations": naming_violations,
            "security_violations": security_violations,
            "total_violations": len(all_violations),
        },
        "violations": all_violations,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print(f"\n{'='*60}")
    print(f"  Workspaces checked : {len(workspaces)}")
    print(f"  Items checked      : {total_items}")
    print(f"  Naming violations  : {naming_violations}")
    print(f"  Security violations: {security_violations}")
    print(f"{'='*60}")

    if all_violations:
        print("\nViolations:\n")
        for v in all_violations:
            loc = f"[{v['workspace']}] {v['item'] or '(workspace)'}"
            print(f"  ✗ {loc}: {v['message']}")
        print()

    # Set GitHub Actions output
    passed = len(all_violations) == 0
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as gh_out:
        gh_out.write(f"passed={'true' if passed else 'false'}\n")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
