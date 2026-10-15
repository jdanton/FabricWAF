"""
apply_fabric_policies.py

Deploys a Fabric policy set (preview) from a JSON definition in policies/ and,
optionally, activates it. This is policy-as-code for Fabric policies: neither the
microsoft/fabric Terraform provider, azurerm, nor fabric-cicd support policy sets
(October 2026), so this script calls the Fabric REST API directly.

Steps:
  1. Load the definition and substitute ${ENV_VAR} placeholders (group object IDs).
  2. Validate it locally (scope, policy types, limits, API item-type identifiers).
  3. Find the policy set by displayName in the governance workspace, or create it.
  4. For each policy in the definition, replace all of its rules (replaceByPolicy).
     Policies not in the definition are left untouched.
  5. With --activate, activate the set on its scope (tenant, or --capacity-id).

The default is a dry run that prints the plan. Use --apply to write.

Rules are an ALLOW LIST: once a policy has a rule, any action matching no rule
is blocked. Activation can take up to 15 minutes to propagate. Validate with
test accounts and the Fabric audit log before activating in production.

Authentication:
  DefaultAzureCredential (managed identity on fabric-gh-runner). Needs:
    - Contributor+ on the governance workspace (create / edit rules)
    - Fabric administrator to activate a Tenant-scoped set
    - Capacity admin to activate a Capacity-scoped set
  The "Policies (preview)" tenant setting must be enabled for the identity.

Environment variables:
  FABRIC_POLICY_WORKSPACE_ID  Workspace that stores policy sets (required)
  plus any ${VAR} placeholders used in the definition, e.g.
  FABRIC_PLATFORM_ADMINS_GROUP_ID, FABRIC_PROD_DEPLOYERS_GROUP_ID

Usage:
  python scripts/apply_fabric_policies.py policies/ps_tenant_baseline.json
  python scripts/apply_fabric_policies.py policies/ps_tenant_baseline.json --apply
  python scripts/apply_fabric_policies.py policies/ps_cap_fin_prod_itemcreation.json \
      --apply --activate --capacity-id <capacity-guid>

Exit codes:
  0  Success (or dry run)
  1  Validation or API error
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"

# Policy → scope it can live in (fabric-policies-overview). ExternalDataSharing
# is documented but not yet in the REST API's Policy enum; configure it in the
# Policies center until it is.
POLICY_SCOPES = {
    "ItemCreation": "Capacity",
    "WorkspaceSettingsEditing": "Tenant",
}
MAX_RULES = {"Tenant": 100, "Capacity": 50}
MAX_VALUES_PER_CONDITION = 50
WORKSPACE_SETTING_TYPES = {"Encryption", "OutboundNetworking"}

# API ItemType identifiers (fabric-rest-api-specs common/definitions.json,
# 2026-10-13). Item-creation rules reject display names such as "Pipeline".
ITEM_TYPES = {
    "AnomalyDetector", "ApacheAirflowJob", "AppBackend", "AzureDatabricksStorage",
    "CopyJob", "CosmosDBDatabase", "Dashboard", "DataAgent", "DataBuildToolJob",
    "DataPipeline", "Dataflow", "Datamart", "DigitalTwinBuilder",
    "DigitalTwinBuilderFlow", "Environment", "EventSchemaSet", "Eventhouse",
    "Eventstream", "GraphModel", "GraphQLApi", "GraphQuerySet", "KQLDashboard",
    "KQLDatabase", "KQLQueryset", "Lakehouse", "MLExperiment", "MLModel", "Map",
    "MirroredAzureDatabricksCatalog", "MirroredCatalog", "MirroredDatabase",
    "MirroredWarehouse", "MountedDataFactory", "Notebook", "Ontology",
    "OperationsAgent", "OrgApp", "OrgAppAudience", "PaginatedReport", "Plan",
    "PolicySet", "Reflex", "Report", "SQLDatabase", "SQLEndpoint", "SemanticModel",
    "SnowflakeDatabase", "SparkJobDefinition", "UserDataFunction",
    "VariableLibrary", "Warehouse", "WarehouseSnapshot",
}

# Mirrors naming-standard.md §23.
NAME_PATTERN = re.compile(
    r"^ps_(?:tenant_[a-z][a-z0-9_]*|cap_(?:fin|mktg|hr|eng|sales|ops)_"
    r"(?:dev|tst|stg|prod)_[a-z][a-z0-9_]*)$"
)

_credential = DefaultAzureCredential()


def _headers() -> dict:
    token = _credential.get_token("https://api.fabric.microsoft.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Definition loading + validation
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load_definition(path: str) -> dict:
    with open(path) as f:
        raw = f.read()
    missing = sorted({v for v in _PLACEHOLDER.findall(raw) if not os.environ.get(v)})
    if missing:
        raise ValueError(f"Unset environment variables for placeholders: {', '.join(missing)}")
    return json.loads(_PLACEHOLDER.sub(lambda m: os.environ[m.group(1)], raw))


def validate(defn: dict) -> list[str]:
    errors = []
    name, scope = defn.get("displayName", ""), defn.get("scope")
    if not NAME_PATTERN.match(name):
        errors.append(f"displayName '{name}' does not match naming-standard.md §23")
    if scope not in MAX_RULES:
        errors.append(f"scope must be Tenant or Capacity, got '{scope}'")
        return errors
    if name.startswith("ps_tenant_") != (scope == "Tenant"):
        errors.append(f"displayName prefix doesn't match scope '{scope}'")

    for policy, rules in defn.get("policies", {}).items():
        if policy not in POLICY_SCOPES:
            errors.append(f"{policy}: not a supported policy ({', '.join(POLICY_SCOPES)})")
            continue
        if POLICY_SCOPES[policy] != scope:
            errors.append(f"{policy}: only valid at {POLICY_SCOPES[policy]} scope")
        if not rules:
            errors.append(f"{policy}: no rules. An empty list would clear the policy back "
                          "to its default; remove the key instead")
        if len(rules) > MAX_RULES[scope]:
            errors.append(f"{policy}: {len(rules)} rules exceeds the {scope} limit of {MAX_RULES[scope]}")
        for rule in rules:
            label = f"{policy} / '{rule.get('displayName', '?')}'"
            if not rule.get("conditions"):
                errors.append(f"{label}: a rule with no conditions allows everything")
            for cond in rule.get("conditions", []):
                values = cond.get("predicate", {}).get("values", [])
                prop = cond.get("targetProperty")
                if not 1 <= len(values) <= MAX_VALUES_PER_CONDITION:
                    errors.append(f"{label}: {prop} needs 1–{MAX_VALUES_PER_CONDITION} values")
                if prop == "item.type":
                    bad = sorted(set(values) - ITEM_TYPES)
                    if bad:
                        errors.append(f"{label}: unknown API item types {bad}")
                if prop == "workspaceSettingsEditing.settingType":
                    bad = sorted(set(values) - WORKSPACE_SETTING_TYPES)
                    if bad:
                        errors.append(f"{label}: unsupported setting types {bad}")
                if prop == "workspace.name":
                    errors.append(f"{label}: workspace.name is rejected; use workspace.id")
    return errors


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _raise(resp: requests.Response) -> None:
    if not resp.ok:
        raise RuntimeError(f"{resp.request.method} {resp.url} → {resp.status_code}: {resp.text}")


def _wait_for_lro(resp: requests.Response) -> dict:
    """Poll a 202 long-running operation and return its result body."""
    location = resp.headers.get("Location")
    while True:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.get(location, headers=_headers(), timeout=30)
        _raise(resp)
        status = resp.json().get("status")
        if status == "Succeeded":
            result = requests.get(f"{location}/result", headers=_headers(), timeout=30)
            _raise(result)
            return result.json()
        if status in ("Failed", "Cancelled"):
            raise RuntimeError(f"Operation {status}: {resp.text}")


def find_policy_set(ws_id: str, name: str) -> dict | None:
    url = f"{FABRIC_API}/workspaces/{ws_id}/policySets"
    while url:
        resp = requests.get(url, headers=_headers(), timeout=30)
        _raise(resp)
        body = resp.json()
        for ps in body.get("value", []):
            if ps.get("displayName") == name:
                return ps
        url = body.get("continuationUri")
    return None


def create_policy_set(ws_id: str, defn: dict) -> dict:
    resp = requests.post(
        f"{FABRIC_API}/workspaces/{ws_id}/policySets",
        headers=_headers(),
        json={
            "displayName": defn["displayName"],
            "description": defn.get("description", "")[:256],
            "creationPayload": {"scope": {"type": defn["scope"]}},
        },
        timeout=30,
    )
    _raise(resp)
    return _wait_for_lro(resp) if resp.status_code == 202 else resp.json()


def replace_rules(ws_id: str, ps_id: str, policy: str, rules: list[dict]) -> None:
    resp = requests.post(
        f"{FABRIC_API}/workspaces/{ws_id}/policySets/{ps_id}/policyRules/replaceByPolicy",
        params={"beta": "true"},
        headers=_headers(),
        json={"policy": policy, "policyRules": rules},
        timeout=60,
    )
    _raise(resp)


def activate(ws_id: str, ps_id: str, scope: str, capacity_id: str | None, allow_replace: bool) -> None:
    params = {"allowReplace": str(allow_replace).lower()}
    if scope == "Tenant":
        url = f"{FABRIC_API}/admin/workspaces/{ws_id}/policySets/{ps_id}/activate"
        resp = requests.post(url, params=params, headers=_headers(), timeout=30)
    else:
        url = f"{FABRIC_API}/workspaces/{ws_id}/policySets/{ps_id}/activate"
        resp = requests.post(url, params=params, headers=_headers(), timeout=30,
                             json={"scopeType": "Capacity", "capacityId": capacity_id})
    _raise(resp)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a Fabric policy set from policies/*.json")
    parser.add_argument("definition", help="Path to a policy set definition JSON file")
    parser.add_argument("--apply", action="store_true", help="Create/update the policy set (default: dry run)")
    parser.add_argument("--activate", action="store_true", help="Activate after applying (needs --apply)")
    parser.add_argument("--capacity-id", help="Capacity GUID to activate a Capacity-scoped set on")
    parser.add_argument("--replace-active", action="store_true",
                        help="Replace a different policy set already active on the scope")
    args = parser.parse_args()

    ws_id = os.environ.get("FABRIC_POLICY_WORKSPACE_ID", "")
    if not ws_id:
        print("ERROR: FABRIC_POLICY_WORKSPACE_ID is required.")
        return 1

    try:
        defn = load_definition(args.definition)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    errors = validate(defn)
    if args.activate and not args.apply:
        errors.append("--activate requires --apply")
    if args.activate and defn.get("scope") == "Capacity" and not args.capacity_id:
        errors.append("--activate on a Capacity-scoped set requires --capacity-id")
    if errors:
        print("Validation failed:")
        for e in errors:
            print(f"  - {e}")
        return 1

    name, scope = defn["displayName"], defn["scope"]
    print(f"Policy set : {name}  [{scope} scope]")
    for policy, rules in defn["policies"].items():
        print(f"  {policy}: {len(rules)} rule(s) — replaces all existing {policy} rules")
        for rule in rules:
            print(f"    • {rule['displayName']}")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return 0

    try:
        ps = find_policy_set(ws_id, name)
        if ps is None:
            print(f"\nCreating policy set '{name}'...")
            ps = create_policy_set(ws_id, defn)
        existing_scope = ps.get("properties", {}).get("scope", {}).get("type")
        if existing_scope and existing_scope != scope:
            print(f"ERROR: existing '{name}' has scope {existing_scope}; scope can't be changed. "
                  "Delete it or rename the definition.")
            return 1

        for policy, rules in defn["policies"].items():
            print(f"Replacing {policy} rules...")
            replace_rules(ws_id, ps["id"], policy, rules)

        if args.activate:
            target = "tenant" if scope == "Tenant" else f"capacity {args.capacity_id}"
            print(f"Activating on {target} (takes effect within 15 minutes)...")
            activate(ws_id, ps["id"], scope, args.capacity_id, args.replace_active)
        else:
            status = ps.get("properties", {}).get("status", "Inactive")
            print(f"Rules updated. Policy set status: {status} (not changed; use --activate).")
    except (RuntimeError, requests.RequestException) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
