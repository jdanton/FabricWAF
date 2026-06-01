"""
assess_tenant_settings.py

Security assessment of Microsoft Fabric / Power BI **tenant settings**.

Reads the live tenant settings from the Fabric Admin REST API, compares each
catalogued setting against a chosen recommendation profile (light / balanced /
paranoid), prints a risk-rated compliance report, writes a JSON report, and can
optionally emit copy/paste-ready update calls or apply the differences directly.

This is the tenant-wide counterpart to configure_capacity.py. The catalogue of
settings, their risk ratings, and the per-profile target states mirror
tenant-settings-best-practices.md — that document is the human-readable source
of truth; CATALOG below is the machine-readable mirror.

Risk is shown with a SHAPE as well as a colour, so it is distinguishable without
relying on colour vision (see tenant-settings-best-practices.md):
    A  HIGH   (triangle)   significant security / DLP risk
    D  MED    (diamond)    moderate risk, needs governance controls
    O  LOW    (circle)     minimal risk / posture-strengthening feature
(rendered as the geometric glyphs U+25B2 / U+25C6 / U+25CF at runtime).

Authentication:
  Uses DefaultAzureCredential — picks up the fabric-gh-runner managed identity
  automatically. The identity must be a Fabric administrator (or a service
  principal granted tenant-settings admin access) with:
    - Tenant.Read.All        for report-only / --emit runs
    - Tenant.ReadWrite.All    additionally, for --apply

Environment variables:
  FABRIC_RESTRICT_GROUP       Object (graph) ID of the security group to scope
                              'Scope'-target settings to when applying. If unset,
                              Scope targets are reported but never auto-applied.
  FABRIC_RESTRICT_GROUP_NAME  Optional display name for that group (cosmetic).
  REPORT_PATH                 JSON output path (default: tenant-settings-report.json)

Usage:
  pip install azure-identity requests

  python assess_tenant_settings.py                      # report, balanced profile
  python assess_tenant_settings.py --profile light
  python assess_tenant_settings.py --profile paranoid
  python assess_tenant_settings.py --emit               # print update calls for drift
  python assess_tenant_settings.py --apply              # apply the differences
  python assess_tenant_settings.py --fail-on medium     # CI gate threshold

Exit codes:
  0  No non-compliant settings at or above --fail-on (or --apply fully succeeded)
  1  Non-compliant settings found at/above the threshold, or an apply failed
  2  Configuration / authentication error
"""

import argparse
import json
import os
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FABRIC_API = "https://api.fabric.microsoft.com/v1"
TENANT_SETTINGS_URL = f"{FABRIC_API}/admin/tenantsettings"

REPORT_PATH = os.environ.get("REPORT_PATH", "tenant-settings-report.json")
RESTRICT_GROUP = os.environ.get("FABRIC_RESTRICT_GROUP", "")
RESTRICT_GROUP_NAME = os.environ.get("FABRIC_RESTRICT_GROUP_NAME", "")

# The Fabric Admin tenant-settings API is limited to 25 requests / minute.
# A single update is one request; we pace applies to stay comfortably under it.
_APPLY_PACE_SECONDS = 3
_MAX_THROTTLE_RETRIES = 3
_MAX_RETRY_SLEEP = 120

# ---------------------------------------------------------------------------
# Risk levels — shape + colour + label (shape carries severity for accessibility)
# ---------------------------------------------------------------------------

HIGH, MED, LOW = "high", "medium", "low"
_RISK_ORDER = {HIGH: 3, MED: 2, LOW: 1}

# Geometric glyphs: triangle (high), diamond (medium), circle (low).
_RISK_GLYPH = {HIGH: "▲", MED: "◆", LOW: "●"}
_RISK_LABEL = {HIGH: "HIGH", MED: "MED ", LOW: "LOW "}


def risk_tag(risk: str) -> str:
    """Accessible risk marker: distinct shape + text label, never colour-only."""
    return f"{_RISK_GLYPH[risk]} {_RISK_LABEL[risk]}"


# ---------------------------------------------------------------------------
# Target-state vocabulary
# ---------------------------------------------------------------------------

OFF = "off"        # enabled = false for the whole org
ON = "on"          # enabled = true (secure state for protection/monitoring)
SCOPE = "scope"    # enabled = true but restricted to a security group
# A target of None means the profile takes no position (informational only).


# ---------------------------------------------------------------------------
# Catalogue — mirrors tenant-settings-best-practices.md
# ---------------------------------------------------------------------------

def S(setting_name, title, group, risk, light, balanced, paranoid, why, learn):
    return {
        "settingName": setting_name,
        "title": title,
        "group": group,
        "risk": risk,
        "targets": {"light": light, "balanced": balanced, "paranoid": paranoid},
        "why": why,
        "learn": learn,
    }


_LEARN_EXPORT = "https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-export-sharing"
_LEARN_DEV = "https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer"
_LEARN_ADMINAPI = "https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-admin-api-settings"
_LEARN_INDEX = "https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index"
_LEARN_INFOPROT = "https://learn.microsoft.com/en-us/fabric/governance/information-protection"

CATALOG = [
    # --- Export and sharing ---
    S("PublishToWeb", "Publish to web", "Export and sharing", HIGH,
      OFF, OFF, OFF,
      "Publishes a report to a public, unauthenticated URL indexed by search "
      "engines — the largest accidental-disclosure vector in Power BI.", _LEARN_EXPORT),
    S("ShareLinkToEntireOrg", "Shareable links to everyone in the org",
      "Export and sharing", HIGH, None, OFF, OFF,
      "One click grants every user in the tenant access, bypassing per-item "
      "review and undermining least privilege.", _LEARN_EXPORT),
    S("ExternalSharingV2", "Invite guest users via sharing",
      "Export and sharing", HIGH, None, SCOPE, OFF,
      "Lets users invite external B2B guests through item sharing, creating "
      "external identities with no review process.", _LEARN_EXPORT),
    S("AllowGuestUserToAccessSharedContent", "Guest users can access Fabric",
      "Export and sharing", HIGH, None, SCOPE, OFF,
      "Controls whether external B2B guests can open shared Fabric content at "
      "all. Broad enablement widens the external attack surface.", _LEARN_EXPORT),
    S("ElevatedGuestsTenant", "Guest users can browse and access content",
      "Export and sharing", HIGH, None, OFF, OFF,
      "Lets guests browse tenant content, not just open a shared link. Should "
      "rarely be on org-wide.", _LEARN_EXPORT),
    S("ExportReport", "Download reports (.pbix)", "Export and sharing", HIGH,
      None, SCOPE, OFF,
      "A downloaded .pbix can carry the full data model and embedded "
      "credentials off-platform — a primary exfiltration path.", _LEARN_EXPORT),
    S("EmailSubscriptionsToExternalUsers", "Email subscriptions to external users",
      "Export and sharing", HIGH, None, OFF, OFF,
      "Schedules recurring report snapshots to arbitrary external addresses — "
      "automated, ongoing data egress.", _LEARN_EXPORT),
    S("AllowExternalDataSharingSwitch", "External data sharing",
      "Export and sharing", HIGH, None, SCOPE, OFF,
      "Lets users share live OneLake data outward to other tenants. Powerful "
      "and easy to misuse without governance.", _LEARN_EXPORT),
    S("AllowExternalDataSharingReceiverSwitch", "Accept external data shares",
      "Export and sharing", MED, None, None, OFF,
      "Accepting external shares can pull ungoverned external data into the "
      "tenant.", _LEARN_EXPORT),
    S("ExternalDatasetSharingTenant", "Guests use shared models in their own tenant",
      "Export and sharing", MED, None, None, OFF,
      "Data leaves the boundary of your tenant's monitoring once a guest "
      "consumes the model elsewhere.", _LEARN_EXPORT),
    S("EmailSubscriptionsToB2BUsers", "Email subscriptions to B2B guests",
      "Export and sharing", MED, None, None, OFF,
      "Recurring report snapshots delivered to guest identities.", _LEARN_EXPORT),
    S("ExportToExcelSetting", "Export to Excel", "Export and sharing", MED,
      None, None, OFF,
      "Bulk extraction of underlying data to an uncontrolled file — a DLP "
      "egress path the Paranoid profile closes.", _LEARN_EXPORT),
    S("ExportToCsv", "Export to .csv", "Export and sharing", MED, None, None, OFF,
      "Raw tabular extract leaving the platform.", _LEARN_EXPORT),
    S("ExportToPowerPoint", "Export reports as PowerPoint / PDF",
      "Export and sharing", MED, None, None, OFF,
      "Renders report contents into portable files outside the platform's "
      "controls.", _LEARN_EXPORT),
    S("ExportToImage", "Export reports as images", "Export and sharing", LOW,
      None, None, OFF,
      "Lower-fidelity (image-only) egress; closed by the Paranoid profile for "
      "completeness.", _LEARN_EXPORT),
    S("ExportToWord", "Export reports as Word documents", "Export and sharing", LOW,
      None, None, OFF,
      "Paginated-report export path; low data density but still egress.", _LEARN_EXPORT),
    S("ExportVisualImageTenant", "Copy and paste visuals (as image)",
      "Export and sharing", LOW, None, None, OFF,
      "Visual-as-image egress; minimal data but a path nonetheless.", _LEARN_EXPORT),
    S("EmailSubscriptionTenant", "Users can set up email subscriptions",
      "Export and sharing", LOW, None, None, None,
      "Internal subscriptions are a productivity feature; the external "
      "variants carry the real risk.", _LEARN_EXPORT),
    S("AllowPowerBIASDQOnTenant", "DirectQuery to Power BI semantic models",
      "Export and sharing", LOW, None, None, None,
      "Model reuse / chaining; the concern is lineage, not direct egress.", _LEARN_EXPORT),
    S("LiveConnection", "Work with models in Excel via live connection",
      "Export and sharing", LOW, None, None, None,
      "Analyze-in-Excel; row-level security is still enforced server-side.", _LEARN_EXPORT),

    # --- Power BI visuals ---
    S("CertifiedCustomVisualsTenant", "Add and use certified visuals only",
      "Power BI visuals", HIGH, ON, ON, ON,
      "When on, blocks every uncertified custom visual. Uncertified visuals "
      "can run arbitrary code and call external endpoints with report data. A "
      "strong, broadly-agreeable control.", _LEARN_INDEX),
    S("CustomVisualsTenant", "Allow visuals created using the Power BI SDK",
      "Power BI visuals", MED, None, None, None,
      "Governs custom visuals broadly; CertifiedCustomVisualsTenant already "
      "constrains the uncertified ones. Turning both off can break legitimate "
      "certified visuals.", _LEARN_INDEX),
    S("AllowCVToExportDataToFileTenant", "Allow downloads from custom visuals",
      "Power BI visuals", MED, None, OFF, OFF,
      "A custom visual that can write files becomes an exfiltration channel "
      "bypassing the standard export controls.", _LEARN_INDEX),
    S("AllowCVLocalStorageV2Tenant", "Custom visuals can use browser local storage",
      "Power BI visuals", LOW, None, None, OFF,
      "Persists visual state in the browser; minor data-at-rest concern on "
      "shared machines.", _LEARN_INDEX),

    # --- R and Python visuals ---
    S("RScriptVisual", "Interact with and share R and Python visuals",
      "R and Python visuals", MED, None, SCOPE, OFF,
      "R/Python visuals execute scripted code against report data and can reach "
      "external endpoints. Scope to a trusted group or disable.", _LEARN_INDEX),

    # --- Datamart settings ---
    S("DatamartTenant", "Create Datamarts", "Datamart settings", MED,
      OFF, OFF, OFF,
      "Provisions an autonomous SQL endpoint per datamart — ungoverned data "
      "copies and a new surface to secure. Flagged in the thread as a strong "
      "disable recommendation.", _LEARN_INDEX),

    # --- Developer settings ---
    S("BlockResourceKeyAuthentication", "Block ResourceKey authentication",
      "Developer settings", HIGH, None, ON, ON,
      "When on, blocks streaming-dataset push / resource-key auth — a static, "
      "non-rotating, non-Entra credential. Enabling closes a weak-auth path.", _LEARN_DEV),
    S("ServicePrincipalAccessGlobalAPIs",
      "SPs can create workspaces, connections, pipelines", "Developer settings",
      MED, None, SCOPE, SCOPE,
      "SP access to Fabric APIs should be confined to a named, audited set of "
      "automation identities — never the whole directory.", _LEARN_DEV),
    S("ServicePrincipalAccessPermissionAPIs", "SPs can call Fabric public APIs",
      "Developer settings", MED, None, SCOPE, SCOPE,
      "Restrict the population of service principals that can drive the "
      "platform programmatically.", _LEARN_DEV),
    S("AllowServicePrincipalsCreateAndUseProfiles",
      "Allow SPs to create and use profiles", "Developer settings", LOW,
      None, SCOPE, SCOPE,
      "Profiles are an ISV multi-tenancy feature; scope to the SPs that "
      "legitimately use them.", _LEARN_DEV),

    # --- Admin API settings ---
    S("AllowServicePrincipalsUseWriteAdminAPIs",
      "SPs can access admin APIs used for updates", "Admin API settings", HIGH,
      None, SCOPE, OFF,
      "Write-level admin API access lets an SP change tenant-wide "
      "configuration. Tightly scope or disable.", _LEARN_ADMINAPI),
    S("AllowServicePrincipalsUseReadAdminAPIs",
      "SPs can access read-only admin APIs", "Admin API settings", MED,
      None, SCOPE, SCOPE,
      "Read admin APIs expose tenant-wide metadata; restrict to the governance "
      "/ audit automation that needs it.", _LEARN_ADMINAPI),
    S("AdminApisIncludeDetailedMetadata",
      "Enhance admin API responses with detailed metadata", "Admin API settings",
      LOW, None, ON, ON,
      "Enabling improves governance visibility for the audit scanner. Low "
      "risk, recommended on.", _LEARN_ADMINAPI),
    S("AdminApisIncludeExpressions",
      "Enhance admin API responses with DAX and M expressions",
      "Admin API settings", LOW, None, ON, ON,
      "Surfaces model expressions to governance tooling. Weigh against the fact "
      "that expressions can embed secrets; on for monitoring-first orgs.", _LEARN_ADMINAPI),

    # --- Audit and usage ---
    S("UsageMetrics", "Usage metrics for content creators", "Audit and usage",
      LOW, None, ON, ON,
      "Monitoring feature — enabling strengthens posture by giving owners "
      "visibility into who uses their content.", _LEARN_INDEX),

    # --- Git integration ---
    S("GitIntegrationTenantSwitch", "Synchronize workspace items with Git",
      "Git integration", MED, None, SCOPE, SCOPE,
      "Git sync moves item definitions (which can include sensitive metadata) "
      "to external repos. Scope to teams with a governed repo.", _LEARN_INDEX),
    S("GitIntegrationSensitivityLabelsTenantSwitch",
      "Export items with sensitivity labels to Git", "Git integration", MED,
      None, None, OFF,
      "Pushing labeled (protected) content into a Git repo strips it from the "
      "platform's label enforcement.", _LEARN_INDEX),
    S("GitIntegrationCrossGeoTenantSwitch",
      "Export items to Git repos in other geographies", "Git integration", MED,
      None, None, OFF,
      "Cross-geo export can violate data-residency requirements.", _LEARN_INDEX),

    # --- Information protection ---
    S("BlockProtectedLabelSharingToEntireOrg",
      "Restrict protected-label content from org-wide links",
      "Information protection", HIGH, None, ON, ON,
      "When on, content with a protected sensitivity label cannot be shared via "
      "'everyone in the org' links — couples DLP labels to sharing controls.", _LEARN_INFOPROT),
    S("EimInformationProtectionEdit", "Allow users to apply sensitivity labels",
      "Information protection", HIGH, None, ON, ON,
      "Labeling is the prerequisite for every downstream DLP control. Without "
      "it, no content can be classified or protected.", _LEARN_INFOPROT),
    S("EimInformationProtectionDataSourceInheritanceSetting",
      "Apply labels from data sources", "Information protection", MED,
      None, ON, ON,
      "Propagates source-system classifications into Power BI so protection is "
      "not lost in transit.", _LEARN_INFOPROT),
    S("DataSecurityForAIInteractions",
      "Allow Microsoft Purview to secure AI interactions",
      "Information protection", MED, None, None, ON,
      "Brings Copilot / AI interactions under Purview DLP.", _LEARN_INFOPROT),
]

CATALOG_BY_NAME = {s["settingName"]: s for s in CATALOG}

# ---------------------------------------------------------------------------
# Auth + HTTP
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()


def _headers() -> dict:
    token = _credential.get_token("https://api.fabric.microsoft.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_tenant_settings() -> list[dict]:
    """GET all tenant settings, following the continuationToken pagination."""
    results: list[dict] = []
    url = TENANT_SETTINGS_URL
    params: dict | None = None
    throttle_attempts = 0
    while url:
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code == 429:
            throttle_attempts += 1
            retry_after = min(int(resp.headers.get("Retry-After", 10)), _MAX_RETRY_SLEEP)
            print(f"  [429] Throttled listing settings (attempt {throttle_attempts}/"
                  f"{_MAX_THROTTLE_RETRIES}) — waiting {retry_after}s ...", flush=True)
            if throttle_attempts > _MAX_THROTTLE_RETRIES:
                resp.raise_for_status()
            time.sleep(retry_after)
            continue
        throttle_attempts = 0
        resp.raise_for_status()
        body = resp.json()
        results.extend(body.get("value", []))
        # Pagination: a continuationUri carries its own query string.
        next_uri = body.get("continuationUri")
        token = body.get("continuationToken")
        if next_uri:
            url, params = next_uri, None
        elif token:
            url, params = TENANT_SETTINGS_URL, {"continuationToken": token}
        else:
            url = None
    return results


def update_tenant_setting(setting_name: str, body: dict) -> tuple[bool, str]:
    """POST an update for one setting. Returns (ok, message)."""
    url = f"{TENANT_SETTINGS_URL}/{setting_name}/update"
    for attempt in range(_MAX_THROTTLE_RETRIES + 1):
        resp = requests.post(url, headers=_headers(), json=body, timeout=30)
        if resp.status_code == 429:
            retry_after = min(int(resp.headers.get("Retry-After", 10)), _MAX_RETRY_SLEEP)
            print(f"    [429] Throttled updating {setting_name} — waiting {retry_after}s ...",
                  flush=True)
            time.sleep(retry_after)
            continue
        if resp.ok:
            return True, "applied"
        return False, f"{resp.status_code}: {resp.text[:200]}"
    return False, "gave up after repeated 429s"


# ---------------------------------------------------------------------------
# Compliance evaluation
# ---------------------------------------------------------------------------

def _is_scoped(live: dict) -> bool:
    """True if the setting is enabled but restricted to >=1 security group."""
    return bool(live.get("enabled")) and bool(live.get("canSpecifySecurityGroups")) \
        and len(live.get("enabledSecurityGroups") or []) > 0


def evaluate(setting: dict, live: dict | None, profile: str) -> dict:
    """
    Compare a catalogued setting's live state against its profile target.

    status is one of:
      compliant      meets the target
      drift          does not meet the target (actionable)
      informational  profile has no target for this setting
      not_found      catalogued settingName not present in the tenant (name
                     drift or not available on this tenant — never auto-applied)
    """
    target = setting["targets"].get(profile)
    result = {
        "settingName": setting["settingName"],
        "title": setting["title"],
        "group": setting["group"],
        "risk": setting["risk"],
        "target": target,
        "why": setting["why"],
        "learn": setting["learn"],
        "live_enabled": None if live is None else bool(live.get("enabled")),
        "live_scoped": None if live is None else _is_scoped(live),
    }

    if live is None:
        result["status"] = "not_found"
        result["detail"] = ("settingName not returned by the tenant — verify the "
                            "name against the live API; not applied.")
        return result

    if target is None:
        result["status"] = "informational"
        result["detail"] = "No recommendation in this profile."
        return result

    enabled = bool(live.get("enabled"))
    if target == OFF:
        ok = (enabled is False)
        result["detail"] = "Enabled — should be Off." if not ok else "Off."
    elif target == ON:
        ok = (enabled is True)
        result["detail"] = "Disabled — should be On." if not ok else "On."
    elif target == SCOPE:
        ok = _is_scoped(live)
        if not ok:
            result["detail"] = ("Enabled org-wide — should be restricted to a "
                                "security group." if enabled
                                else "Disabled — should be enabled but scoped to a group.")
        else:
            result["detail"] = "Scoped to a security group."
    else:
        ok = True
        result["detail"] = f"Unknown target '{target}'."

    result["status"] = "compliant" if ok else "drift"
    return result


def build_update_body(setting: dict, target: str) -> dict | None:
    """
    Construct the update request body for a target, or None if it cannot be
    applied automatically (a Scope target with no FABRIC_RESTRICT_GROUP set).
    """
    if target == OFF:
        return {"enabled": False}
    if target == ON:
        return {"enabled": True}
    if target == SCOPE:
        if not RESTRICT_GROUP:
            return None
        return {
            "enabled": True,
            "enabledSecurityGroups": [
                {"graphId": RESTRICT_GROUP, "name": RESTRICT_GROUP_NAME or RESTRICT_GROUP}
            ],
        }
    return None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(findings: list[dict], profile: str, uncatalogued: list[dict]) -> None:
    drift = [f for f in findings if f["status"] == "drift"]
    compliant = [f for f in findings if f["status"] == "compliant"]
    info = [f for f in findings if f["status"] == "informational"]
    not_found = [f for f in findings if f["status"] == "not_found"]

    print(f"\nTenant settings assessment — profile: {profile.upper()}")
    print("Standards: tenant-settings-best-practices.md")
    print("Risk shapes:  ▲ HIGH (triangle)   ◆ MED (diamond)   ● LOW (circle)")
    print("=" * 78)

    if drift:
        print(f"\nNON-COMPLIANT ({len(drift)}) — highest risk first:\n")
        for f in sorted(drift, key=lambda x: (-_RISK_ORDER[x["risk"]], x["group"])):
            tgt = (f["target"] or "").upper()
            print(f"  {risk_tag(f['risk'])}  {f['settingName']}  [{f['group']}]")
            print(f"            {f['title']}")
            print(f"            target: {tgt:<5} | live: enabled={f['live_enabled']}"
                  f"{' (scoped)' if f['live_scoped'] else ''}")
            print(f"            -> {f['detail']}")
            print(f"            why: {f['why']}")
            print(f"            ref: {f['learn']}\n")

    if not_found:
        print(f"\nNOT FOUND IN TENANT ({len(not_found)}) — name drift or not available; "
              "review, not applied:")
        for f in not_found:
            print(f"  {risk_tag(f['risk'])}  {f['settingName']} — {f['detail']}")

    print("\n" + "-" * 78)
    # Risk breakdown of the drift.
    by_risk = {HIGH: 0, MED: 0, LOW: 0}
    for f in drift:
        by_risk[f["risk"]] += 1
    print(f"  Compliant            : {len(compliant)}")
    print(f"  Non-compliant (drift): {len(drift)}   "
          f"[{risk_tag(HIGH)}x{by_risk[HIGH]}  {risk_tag(MED)}x{by_risk[MED]}  "
          f"{risk_tag(LOW)}x{by_risk[LOW]}]")
    print(f"  Informational        : {len(info)} (no target in this profile)")
    print(f"  Catalogued not found : {len(not_found)}")
    print(f"  Uncatalogued in tenant: {len(uncatalogued)} (not assessed)")
    print("-" * 78)


def emit_update_calls(findings: list[dict]) -> None:
    """Print copy/paste-ready update calls for every drift finding."""
    drift = [f for f in findings if f["status"] == "drift"]
    if not drift:
        print("\nNo drift to emit — tenant matches the profile.")
        return
    print("\n# Copy/paste update calls for non-compliant settings")
    print("# POST https://api.fabric.microsoft.com/v1/admin/tenantsettings/{name}/update\n")
    for f in drift:
        body = build_update_body(CATALOG_BY_NAME[f["settingName"]], f["target"])
        if body is None:
            print(f"# {f['settingName']}: Scope target — set FABRIC_RESTRICT_GROUP to a "
                  "security-group object ID, then:")
            print(f"#   body would be: "
                  f'{{"enabled": true, "enabledSecurityGroups": [{{"graphId": "<group-oid>", '
                  f'"name": "<group-name>"}}]}}')
            print(f"curl -X POST '{TENANT_SETTINGS_URL}/{f['settingName']}/update' \\")
            print("  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' \\")
            print("  -d '<body above>'\n")
            continue
        print(f"curl -X POST '{TENANT_SETTINGS_URL}/{f['settingName']}/update' \\")
        print("  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' \\")
        print(f"  -d '{json.dumps(body)}'\n")


def apply_changes(findings: list[dict]) -> bool:
    """Apply every drift finding. Idempotent — only drift is patched. Returns all_ok."""
    drift = [f for f in findings if f["status"] == "drift"]
    if not drift:
        print("\nNothing to apply — tenant already matches the profile.")
        return True

    print(f"\nApplying {len(drift)} change(s)...")
    all_ok = True
    for i, f in enumerate(drift):
        body = build_update_body(CATALOG_BY_NAME[f["settingName"]], f["target"])
        if body is None:
            print(f"  SKIP  {f['settingName']} — Scope target needs FABRIC_RESTRICT_GROUP; "
                  "left unchanged.")
            continue
        ok, msg = update_tenant_setting(f["settingName"], body)
        status = "OK   " if ok else "FAIL "
        print(f"  {status} {f['settingName']} <- {json.dumps(body)}"
              + ("" if ok else f"  ({msg})"))
        all_ok = all_ok and ok
        # Pace to stay under the 25-requests/minute limit.
        if i < len(drift) - 1:
            time.sleep(_APPLY_PACE_SECONDS)
    return all_ok


def write_json_report(findings: list[dict], profile: str, uncatalogued: list[dict]) -> None:
    drift = [f for f in findings if f["status"] == "drift"]
    by_risk = {HIGH: 0, MED: 0, LOW: 0}
    for f in drift:
        by_risk[f["risk"]] += 1
    report = {
        "profile": profile,
        "summary": {
            "catalogued": len(CATALOG),
            "compliant": sum(1 for f in findings if f["status"] == "compliant"),
            "drift": len(drift),
            "drift_high": by_risk[HIGH],
            "drift_medium": by_risk[MED],
            "drift_low": by_risk[LOW],
            "informational": sum(1 for f in findings if f["status"] == "informational"),
            "not_found": sum(1 for f in findings if f["status"] == "not_found"),
            "uncatalogued_in_tenant": len(uncatalogued),
        },
        "findings": findings,
        "uncatalogued": [{"settingName": s.get("settingName"),
                          "title": s.get("title"),
                          "enabled": s.get("enabled")} for s in uncatalogued],
    }
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nJSON report written to {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assess Fabric/Power BI tenant settings against a security profile.")
    parser.add_argument("--profile", choices=["light", "balanced", "paranoid"],
                        default="balanced", help="Recommendation profile (default: balanced)")
    parser.add_argument("--emit", action="store_true",
                        help="Print copy/paste-ready update calls for non-compliant settings")
    parser.add_argument("--apply", action="store_true",
                        help="Apply the differences (idempotent). Requires Tenant.ReadWrite.All")
    parser.add_argument("--fail-on", choices=["high", "medium", "low", "none"],
                        default="high",
                        help="Exit non-zero if drift at/above this risk exists (default: high)")
    args = parser.parse_args()

    print(f"Fetching tenant settings from {TENANT_SETTINGS_URL} ...")
    try:
        live_settings = list_tenant_settings()
    except requests.HTTPError as exc:
        print(f"ERROR: could not list tenant settings: {exc}")
        print("Check that the identity is a Fabric administrator with Tenant.Read.All.")
        return 2
    live_by_name = {s.get("settingName"): s for s in live_settings}
    print(f"  Retrieved {len(live_settings)} settings from the tenant.")

    findings = [evaluate(s, live_by_name.get(s["settingName"]), args.profile) for s in CATALOG]
    uncatalogued = [s for s in live_settings if s.get("settingName") not in CATALOG_BY_NAME]

    print_report(findings, args.profile, uncatalogued)

    if args.emit:
        emit_update_calls(findings)

    apply_ok = True
    if args.apply:
        apply_ok = apply_changes(findings)

    write_json_report(findings, args.profile, uncatalogued)

    # Exit code.
    if args.apply:
        return 0 if apply_ok else 1

    threshold = {"high": 3, "medium": 2, "low": 1, "none": 99}[args.fail_on]
    gating = [f for f in findings
              if f["status"] == "drift" and _RISK_ORDER[f["risk"]] >= threshold]
    if gating:
        print(f"\nGate: {len(gating)} non-compliant setting(s) at or above "
              f"'{args.fail_on}' risk.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
