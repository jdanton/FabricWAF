# Fabric / Power BI Tenant Settings — Security Best Practices

This document defines a security-rated catalog of Microsoft Fabric (and Power BI)
**tenant settings** and the recommended target state for each, across three
profiles. The `scripts/assess_tenant_settings.py` script reads this catalog (it
is mirrored as a data structure in the script), compares it against the live
tenant via the Fabric Admin REST API, prints a risk-rated compliance report, and
can optionally apply the differences.

It is the tenant-settings counterpart to [capacity-best-practices.md](capacity-best-practices.md):
that document governs a single capacity; this one governs the whole tenant.

> **Scope.** This is a curated subset of ~40 of the highest-impact
> security / DLP / governance settings — not all ~167 tenant settings. The
> catalog structure is extensible: add a row here and a matching entry in the
> script's `CATALOG` list to cover more. Settings not in the catalog are
> reported as **uncatalogued** and left untouched.

---

## Risk legend — designed for color-blind accessibility

The risk of each setting is shown with an icon whose **shape** carries the
severity independently of its color, and is always paired with a text label.
A reader who cannot distinguish red from green can still read the shape and the
word. (The original proposal used three circles distinguished only by color
— 🔴🟡🟢 — which is exactly the pattern that fails for ~8% of men. These shapes fix that.)

| Icon | Shape | Label | Meaning |
|------|-------|-------|---------|
| 🔺 | **Triangle** (point-up, "warning") | **HIGH** | Significant security or DLP risk if not addressed — public sharing, external data egress, authentication gaps, broad admin-write access. |
| 🔶 | **Diamond** | **MEDIUM** | Moderate risk requiring governance controls — should be scoped to security groups, monitored, or restricted rather than left open to the whole org. |
| 🟢 | **Circle** | **LOW** | Minimal risk — mostly productivity or visibility features. Included for completeness or because they *strengthen* posture when enabled. |

> The script prints the same three shapes in plain ASCII so they survive in any
> terminal or log file: `▲ HIGH`, `◆ MED`, `● LOW` — triangle, diamond, circle.

---

## Recommendation profiles

Each setting has a target state in up to three profiles. A dash (**—**) means the
profile takes no position on that setting (it is left as-is and reported as
*informational* only).

| Profile | Philosophy |
|---------|-----------|
| **Light** | Only the strong, broadly-agreeable recommendations that almost any organization should accept. Minimal business disruption. Mirrors the email thread's "Publish to web off, Datamarts off, Certified-visuals-only on" tier. |
| **Balanced** | Adds governance controls: scope risky features to security groups, disable clearly external-facing risks, and turn on data-protection / monitoring features. The recommended default for most regulated organizations. |
| **Paranoid** | Turns **off** everything DLP-related that can be turned off (every export/egress path) and turns **on** everything related to monitoring, logging, and information protection. Working title — expect to relax specific settings against business need. |

**Target-state vocabulary**

| Target | Meaning | Auto-applied? |
|--------|---------|---------------|
| **Off** | `enabled = false` for the whole org | Yes |
| **On** | `enabled = true` (this is the *secure* state for protection/monitoring settings) | Yes |
| **Scope** | `enabled = true` **but restricted to a named security group** (`canSpecifySecurityGroups = true` with at least one `enabledSecurityGroups` entry) | **Reported, not auto-applied** unless `FABRIC_RESTRICT_GROUP` is set — see [Scoping](#a-note-on-scope-targets) |
| **—** | No recommendation in this profile | No |

---

## Catalog

### Export and sharing

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔺 HIGH | Publish to web | `PublishToWeb` | Off | Off | Off | Publishes a report to a public, **unauthenticated** URL indexed by search engines. The single largest accidental-data-disclosure vector in Power BI. |
| 🔺 HIGH | Allow shareable links to everyone in the org | `ShareLinkToEntireOrg` | — | Off | Off | One click grants every user in the tenant access to the item, bypassing per-item review. Undermines least-privilege sharing. |
| 🔺 HIGH | Invite guest users via sharing | `ExternalSharingV2` | — | Scope | Off | Lets users invite external Entra B2B guests directly through item sharing, creating external identities outside any review process. |
| 🔺 HIGH | Guest users can access Fabric | `AllowGuestUserToAccessSharedContent` | — | Scope | Off | Controls whether external B2B guests can open shared Fabric content at all. Broad enablement widens the external attack surface. |
| 🔺 HIGH | Guest users can browse and access content | `ElevatedGuestsTenant` | — | Off | Off | Goes beyond opening a shared link — lets guests *browse* tenant content. Should rarely be on org-wide. |
| 🔺 HIGH | Download reports (.pbix) | `ExportReport` | — | Scope | Off | A downloaded `.pbix` can carry the full data model **and** embedded credentials/connection strings off-platform. Primary exfiltration path. |
| 🔺 HIGH | Email subscriptions to external users | `EmailSubscriptionsToExternalUsers` | — | Off | Off | Schedules report snapshots to arbitrary external email addresses on a recurring basis — automated, ongoing data egress. |
| 🔺 HIGH | External data sharing | `AllowExternalDataSharingSwitch` | — | Scope | Off | Lets users share live OneLake data outward to other tenants. Powerful and easy to misuse without governance. |
| 🔶 MED | Accept external data shares | `AllowExternalDataSharingReceiverSwitch` | — | — | Off | Inbound counterpart — accepting external shares can pull ungoverned external data into the tenant. |
| 🔶 MED | Guests work with shared models in their own tenant | `ExternalDatasetSharingTenant` | — | — | Off | Data leaves the boundary of your tenant's monitoring once a guest consumes the model elsewhere. |
| 🔶 MED | Email subscriptions to B2B guests | `EmailSubscriptionsToB2BUsers` | — | — | Off | Recurring snapshots delivered to guest identities. |
| 🔶 MED | Export to Excel | `ExportToExcelSetting` | — | — | Off | Bulk extraction of underlying data to an uncontrolled file. A DLP egress path the Paranoid profile closes. |
| 🔶 MED | Export to .csv | `ExportToCsv` | — | — | Off | As above — raw tabular extract. |
| 🔶 MED | Export reports as PowerPoint / PDF | `ExportToPowerPoint` | — | — | Off | Renders report contents into portable files that leave the platform's controls. |
| 🟢 LOW | Export reports as images | `ExportToImage` | — | — | Off | Lower-fidelity egress (image only), included so the Paranoid profile closes every export path. |
| 🟢 LOW | Export reports as Word documents | `ExportToWord` | — | — | Off | Paginated-report export path; low data density but still egress. |
| 🟢 LOW | Copy and paste visuals (as image) | `ExportVisualImageTenant` | — | — | Off | Visual-as-image egress; minimal data but a path nonetheless. |
| 🟢 LOW | Users can set up email subscriptions | `EmailSubscriptionTenant` | — | — | — | Internal subscriptions are a productivity feature; the *external* variants above carry the real risk. |
| 🟢 LOW | DirectQuery to Power BI semantic models | `AllowPowerBIASDQOnTenant` | — | — | — | Reuse / chaining of models; governance concern is data lineage, not direct egress. |
| 🟢 LOW | Work with models in Excel via live connection | `LiveConnection` | — | — | — | Analyze-in-Excel; productivity feature with row-level security still enforced server-side. |

### Power BI visuals

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔺 HIGH | Add and use **certified** visuals only | `CertifiedCustomVisualsTenant` | On | On | On | When **on**, blocks every uncertified custom visual. Uncertified visuals can run arbitrary code and call external endpoints with the report's data. Enabling this is a strong, broadly-agreeable control. |
| 🔶 MED | Allow visuals created using the Power BI SDK | `CustomVisualsTenant` | — | — | — | Governs custom visuals broadly. Left to the org because `CertifiedCustomVisualsTenant` already constrains the uncertified ones; turning both off may break legitimate certified visuals. |
| 🔶 MED | Allow downloads from custom visuals | `AllowCVToExportDataToFileTenant` | — | Off | Off | A custom visual that can write files becomes an exfiltration channel that bypasses the standard export controls. |
| 🟢 LOW | Custom visuals can use browser local storage | `AllowCVLocalStorageV2Tenant` | — | — | Off | Persists visual state in the browser; minor data-at-rest concern on shared machines. |

### R and Python visuals

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔶 MED | Interact with and share R and Python visuals | `RScriptVisual` | — | Scope | Off | R/Python visuals execute scripted code against the report's data and can reach external endpoints. Scope to a trusted group or disable. |

### Datamart settings

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔶 MED | Create Datamarts | `DatamartTenant` | Off | Off | Off | Spins up an autonomously-provisioned SQL endpoint per datamart — ungoverned data copies and a new surface to secure. The email thread flags disabling this as a strong recommendation. |

### Developer settings

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔺 HIGH | Block ResourceKey authentication | `BlockResourceKeyAuthentication` | — | On | On | When **on**, blocks streaming-dataset push/resource-key auth — a static, non-rotating, non-Entra credential. Enabling this closes a weak-auth path. |
| 🔶 MED | Service principals can create workspaces, connections, pipelines | `ServicePrincipalAccessGlobalAPIs` | — | Scope | Scope | SP access to Fabric APIs should be confined to a named, audited set of automation identities — never the whole directory. |
| 🔶 MED | Service principals can call Fabric public APIs | `ServicePrincipalAccessPermissionAPIs` | — | Scope | Scope | As above — restrict the population of SPs that can drive the platform programmatically. |
| 🟢 LOW | Allow service principals to create and use profiles | `AllowServicePrincipalsCreateAndUseProfiles` | — | Scope | Scope | Profiles are an ISV multi-tenancy feature; scope to the SPs that legitimately use them. |

### Admin API settings

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔺 HIGH | Service principals can access admin APIs used for updates | `AllowServicePrincipalsUseWriteAdminAPIs` | — | Scope | Off | Write-level admin API access lets an SP change tenant-wide configuration. Tightly scope or disable. |
| 🔶 MED | Service principals can access read-only admin APIs | `AllowServicePrincipalsUseReadAdminAPIs` | — | Scope | Scope | Read admin APIs expose tenant-wide metadata; restrict to the governance/audit automation that needs it (e.g. this repo's scanner identity). |
| 🟢 LOW | Enhance admin API responses with detailed metadata | `AdminApisIncludeDetailedMetadata` | — | On | On | Enabling **improves** governance visibility (the audit scanner sees more). Low risk, recommended on. |
| 🟢 LOW | Enhance admin API responses with DAX and M expressions | `AdminApisIncludeExpressions` | — | On | On | Surfaces model expressions to governance tooling. Weigh against the fact that expressions can contain embedded secrets; on for monitoring-first orgs. |

### Audit and usage

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🟢 LOW | Usage metrics for content creators | `UsageMetrics` | — | On | On | Monitoring feature — enabling **strengthens** posture by giving owners visibility into who uses their content. |

### Git integration

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔶 MED | Synchronize workspace items with Git | `GitIntegrationTenantSwitch` | — | Scope | Scope | Git sync moves item definitions (which can include sensitive metadata) out to external repos. Scope to teams with a governed repo. |
| 🔶 MED | Export items with sensitivity labels to Git | `GitIntegrationSensitivityLabelsTenantSwitch` | — | — | Off | Pushing labeled (protected) content into a Git repo strips it from the platform's label enforcement. |
| 🔶 MED | Export items to Git repos in other geographies | `GitIntegrationCrossGeoTenantSwitch` | — | — | Off | Cross-geo export can violate data-residency requirements. |

### Information protection

| Risk | Setting | `settingName` | Light | Balanced | Paranoid | Why it is a risk |
|------|---------|---------------|:-----:|:--------:|:--------:|------------------|
| 🔺 HIGH | Restrict protected-label content from org-wide links | `BlockProtectedLabelSharingToEntireOrg` | — | On | On | When **on**, content carrying a protected sensitivity label cannot be shared via "everyone in the org" links. Directly couples DLP labels to sharing controls. |
| 🔺 HIGH | Allow users to apply sensitivity labels | `EimInformationProtectionEdit` | — | On | On | Enabling labeling is the prerequisite for every downstream DLP control. Without it, no content can be classified or protected. |
| 🔶 MED | Apply labels from data sources | `EimInformationProtectionDataSourceInheritanceSetting` | — | On | On | Propagates source-system classifications into Power BI automatically, so protection is not lost in transit. |
| 🔶 MED | Allow Microsoft Purview to secure AI interactions | `DataSecurityForAIInteractions` | — | — | On | Brings Copilot / AI interactions under Purview DLP. The Paranoid profile turns on all available protection. |

---

## A note on `Scope` targets

A **Scope** target means "this feature is useful but must not be open to the
entire organization — restrict it to a named security group." The assessment
checks that the setting is enabled with `canSpecifySecurityGroups = true` and at
least one `enabledSecurityGroups` entry, and flags it as non-compliant if it is
enabled org-wide.

`Scope` targets are **not auto-applied by default**, because blindly writing a
security-group restriction can lock legitimate users (or automation) out of a
feature. To apply them, set `FABRIC_RESTRICT_GROUP` to the **object ID** of the
security group that should be allowed, and the script will scope the setting to
that group. Without it, the script prints the manual `update` call and moves on.

---

## Applying the configuration

```bash
pip install azure-identity requests

# Report only — assess the live tenant against the Balanced profile (default)
python scripts/assess_tenant_settings.py

# Pick a profile
python scripts/assess_tenant_settings.py --profile light
python scripts/assess_tenant_settings.py --profile paranoid

# Show the copy/paste-ready update calls for every non-compliant setting
python scripts/assess_tenant_settings.py --emit

# Apply the differences (idempotent — only settings that differ are patched)
python scripts/assess_tenant_settings.py --profile balanced --apply

# Apply, including Scope targets, restricting them to a security group
FABRIC_RESTRICT_GROUP=<group-object-id> \
  python scripts/assess_tenant_settings.py --profile balanced --apply
```

Authentication uses `DefaultAzureCredential` (the `fabric-gh-runner` managed
identity in the pipeline). The identity must be a **Fabric administrator** or a
service principal granted the tenant settings admin scope, and needs the
`Tenant.ReadWrite.All` delegated scope to apply changes (`Tenant.Read.All` is
enough for report-only runs). The Fabric Admin tenant-settings API is rate
limited to **25 requests per minute** — the script paces itself accordingly.

---

## Deviating from these standards

Any deviation from the target values above should be made by a pull request that:

1. Updates this document with the new target value and a documented reason.
2. Updates the corresponding entry in the `CATALOG` list in
   `scripts/assess_tenant_settings.py`.
3. Is approved by a tenant administrator.

Ad-hoc changes made through the Fabric admin portal will be reported as drift on
the next assessment run, and overwritten if the assessment is run with `--apply`.

---

## References

- [List Tenant Settings — Fabric Admin REST API](https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-tenant-settings)
- [Update Tenant Setting — Fabric Admin REST API](https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/update-tenant-setting)
- [Tenant settings index](https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index)
- [Export and sharing tenant settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-export-sharing)
- [Developer tenant settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer)
- [Admin API tenant settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-admin-api-settings)
- [Information protection in Fabric](https://learn.microsoft.com/en-us/fabric/governance/information-protection)
