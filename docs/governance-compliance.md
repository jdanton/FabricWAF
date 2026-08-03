# Governance & Compliance

Reference for the FabricWAF governance/compliance tooling (`scripts/`) and the
production CI/CD pipeline (`.github/workflows/fabric-prod-deploy.yml`).

Fabric has no built-in policy engine, so these Python scripts and the GitHub
Actions gate enforce naming, security, capacity, and tenant-settings standards.
Every script authenticates with `azure.identity.DefaultAzureCredential`, which
transparently picks up the system/user-assigned managed identity on the
`fabric-gh-runner` Azure VM — no secrets, tokens, or OIDC are stored.

## Script index

| Script | Purpose | API(s) | Token scope |
|--------|---------|--------|-------------|
| `audit_fabric.py` | Tenant-wide compliance audit + HTML email | Resource Graph, Fabric Admin, Graph | ARM, Fabric, Graph |
| `assess_tenant_settings.py` | Risk-rated tenant-settings assessment + apply | Fabric Admin | Fabric |
| `compare_tenant_settings.py` | Baseline-vs-live tenant-settings diff → CSV | Fabric Admin | Fabric |
| `validate_fabric.py` | Naming/security validation of prod workspaces | Fabric | Fabric |
| `configure_capacity.py` | Applies capacity best practices | Power BI Admin | Power BI |
| `deploy_fabric.py` | Triggers + polls a Fabric Deployment Pipeline | Fabric | Fabric |
| `list_workspaces.py` | Lists workspaces + assigned capacity | Power BI Admin | Power BI |

Token scope literals passed to `get_token(...)`:

| Label | Scope string |
|-------|--------------|
| ARM | `https://management.azure.com/.default` |
| Fabric | `https://api.fabric.microsoft.com/.default` |
| Graph | `https://graph.microsoft.com/.default` |
| Power BI | `https://analysis.windows.net/powerbi/api/.default` |

---

## `audit_fabric.py` — tenant-wide compliance audit + email

Scans **all** Azure subscriptions for Fabric capacities, then every workspace and
item in the tenant, and emails a compliance report to an admin plus the
Admin-role members of each non-compliant workspace.

### Checks

| Check (`check`) | Object | Severity |
|-----------------|--------|----------|
| `capacity_region` — capacity not in an approved US region | Capacity | high |
| `workspace_naming` — name ≠ `{BU}-{Function}-{Env}` | Workspace | medium |
| `workspace_admin_user` — `Admin` role held by an individual `User` | Workspace | critical |
| `workspace_write_user` — `Member`/`Contributor` held by an individual `User` | Workspace | high |
| `item_naming` — item name ≠ pattern for its type | Item | medium |

Approved US regions: `eastus`, `eastus2`, `westus`, `westus2`, `westus3`,
`centralus`, `northcentralus`, `southcentralus`, `westcentralus`.

Ownership checks only flag principals of type `User`; Entra groups and service
principals are compliant at any role. Unknown item types are skipped rather than
flagged. Naming patterns are the same set used by `validate_fabric.py` (see
[Naming patterns](#naming-patterns-validate_fabricpy--audit_fabricpy)).

### Authentication

`DefaultAzureCredential`, one credential, **three token scopes**:

| Scope | Used for |
|-------|----------|
| ARM (`management.azure.com`) | Azure Resource Graph — `POST .../Microsoft.ResourceGraph/resources` to find all `microsoft.fabric/capacities` |
| Fabric (`api.fabric.microsoft.com`) | Admin API: workspaces, items, role assignments |
| Graph (`graph.microsoft.com`) | Resolve user/group/SP display names + email for the report |

Identity requirements: `Reader` on target subscriptions (Resource Graph), Fabric
tenant admin role (for `/admin/*`), and `User.Read.All` + `Group.Read.All` Graph
application permissions. Admin endpoints fall back to member-scoped endpoints on
`400/401/403/404/429` (e.g. `/admin/workspaces` → `/workspaces`,
`/admin/workspaces/{id}/users` → `/workspaces/{id}/roleAssignments`).
`429` responses honor `Retry-After` (capped 120 s, 3 retries); a global throttle
counter is reported in the summary. Only standard `Workspace`-type workspaces are
audited (Personal/AdminWorkspace skipped).

### CLI / environment

| Flag | Effect |
|------|--------|
| `--dry-run` | Print emails to stdout, do not send (also via `EMAIL_DRY_RUN=true`) |
| `--report-only` | Write JSON report, skip email |

| Env var | Default | Purpose |
|---------|---------|---------|
| `SMTP_HOST` | `smtp.office365.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` / `SMTP_PASSWORD` | — | SMTP login |
| `EMAIL_FROM` | `SMTP_USER` | Sender address |
| `EMAIL_ADMIN` | — | Admin recipient (always gets full report) |
| `EMAIL_DRY_RUN` | `false` | `true` = print instead of send |
| `REPORT_PATH` | `audit-report.json` | JSON output path |

### Output

- **JSON report** at `REPORT_PATH` with a `summary` block (capacities/workspaces/
  items scanned, region/naming/security/total violations) plus per-capacity and
  per-workspace violation detail.
- **Admin email** — full HTML report of every capacity and workspace violation.
- **Owner emails** — each Admin-role member of a *non-compliant* workspace gets a
  scoped HTML report of only that workspace's violations.
- **Exit code**: `0` if `total_violations == 0`, else `1`.

---

## `assess_tenant_settings.py` — tenant-settings assessment + apply

Best-Practice Analyzer for Fabric/Power BI **tenant settings**. Reads live
settings from the Fabric Admin REST API (`GET /admin/tenantsettings`), compares
each catalogued setting against a chosen profile, prints a risk-rated report,
writes JSON, and optionally emits `curl`/`update` calls or applies the diffs.
The in-script `CATALOG` mirrors
[`tenant-settings-best-practices.md`](../tenant-settings-best-practices.md).

### Risk icons (shape + label, color-independent)

| Glyph | Shape | Label | Meaning |
|-------|-------|-------|---------|
| `▲` | Triangle | `HIGH` | Significant security / DLP risk |
| `◆` | Diamond | `MED` | Moderate risk, needs governance controls |
| `●` | Circle | `LOW` | Minimal risk / posture-strengthening |

### Profiles & target vocabulary

`--profile {light,balanced,paranoid}` (default `balanced`).

| Profile | Philosophy |
|---------|-----------|
| **light** | Only strong, broadly-agreeable recommendations. Minimal disruption. |
| **balanced** | Adds governance controls: scope risky features, disable external risks, enable protection/monitoring. |
| **paranoid** | Off for every export/egress path; on for all monitoring and information protection. |

| Target | Meaning | Auto-applied |
|--------|---------|--------------|
| `off` | `enabled = false` org-wide | Yes |
| `on` | `enabled = true` (secure state) | Yes |
| `scope` | `enabled = true` restricted to a security group | Only if `FABRIC_RESTRICT_GROUP` set; else reported |
| `None` (`—`) | Profile takes no position → `informational` | No |

### Evaluation statuses

| Status | Meaning |
|--------|---------|
| `compliant` | Live state matches the profile target |
| `drift` | Actionable — does not match target |
| `informational` | No target in this profile |
| `not_found` | Catalogued `settingName` not returned by the tenant (name drift / unavailable) — never auto-applied |

A `scope` target is compliant only when `enabled` **and**
`canSpecifySecurityGroups` **and** ≥1 `enabledSecurityGroups` entry. Settings the
tenant exposes but the catalog omits are counted as **uncatalogued** (listed with
`--show-uncatalogued`).

### Authentication

`DefaultAzureCredential`, Fabric scope only. Identity must be a Fabric
administrator (or SP with tenant-settings admin access):

| Scope | For |
|-------|-----|
| `Tenant.Read.All` | Report-only and `--emit` |
| `Tenant.ReadWrite.All` | Additionally for `--apply` |

The tenant-settings API is limited to **25 requests/minute**; `--apply` paces at
`_APPLY_PACE_SECONDS = 3` and honors `Retry-After` (cap 120 s, 3 retries).

### CLI / environment

| Flag | Effect |
|------|--------|
| `--profile {light,balanced,paranoid}` | Recommendation profile (default `balanced`) |
| `--emit` | Print copy/paste `curl … /update` calls for each drift |
| `--apply` | POST update bodies for each drift (idempotent); requires `Tenant.ReadWrite.All` |
| `--fail-on {high,medium,low,none}` | CI gate threshold (default `high`) |
| `--show-uncatalogued` | List tenant settings absent from the catalog |

| Env var | Default | Purpose |
|---------|---------|---------|
| `FABRIC_RESTRICT_GROUP` | — | Object ID of the security group for `scope` targets; unset ⇒ scope targets never auto-applied |
| `FABRIC_RESTRICT_GROUP_NAME` | — | Cosmetic display name for that group |
| `REPORT_PATH` | `tenant-settings-report.json` | JSON output path |

### Output & exit codes

JSON report with `summary` (catalogued, compliant, drift + drift_high/medium/low,
informational, not_found, uncatalogued) and full `findings`.

| Code | Meaning |
|------|---------|
| `0` | No drift at/above `--fail-on` (or `--apply` fully succeeded) |
| `1` | Drift at/above threshold, or an apply failed |
| `2` | Configuration / authentication error |

---

## `compare_tenant_settings.py` — baseline-vs-live tenant diff

Compares a **baseline** tenant (local JSON export, e.g. transcribed from
admin-portal screenshots/PDF) against a **live** tenant pulled from the Fabric
Admin API, and writes a CSV. Settings are matched on a normalised title
(case-, punctuation-, and `(preview)`-insensitive), since the admin-portal title
is identical across tenants; `settingName` is filled from the live pull.

### Authentication

`DefaultAzureCredential`, Fabric scope. Requires a Fabric administrator identity
(`Tenant.Read.All`) to read the admin API. Live pull follows
`continuationUri`/`continuationToken` pagination with 429 backoff.

### CLI / environment

| Flag | Default | Effect |
|------|---------|--------|
| `--out` | `tenant_settings_comparison.csv` | Output CSV path |
| `--wide` | off | Side-by-side diff (one row per setting) instead of LONG (one row per tenant×setting) |
| `--baseline-json` | `COMPARE_BASELINE_JSON` or `scripts/baseline_tenant_settings.json` | Baseline JSON export |
| `--label-baseline` | `COMPARE_LABEL_BASELINE` or `Baseline` | Baseline column label |
| `--label-live` | `COMPARE_LABEL_LIVE` or `Live` | Live column label |

Env vars `COMPARE_BASELINE_JSON`, `COMPARE_LABEL_BASELINE`, `COMPARE_LABEL_LIVE`
supply the defaults (no tenant names hard-coded).

### Output

- **LONG CSV** (default): `Tenant, settingName, Title, State, Apply_to,
  Security_groups, Detail`, sorted so both tenants' rows for a setting are
  adjacent.
- **WIDE CSV** (`--wide`): per-setting `*_State` / `*_Apply_to` / `*_groups`
  columns plus `State_match`, `Scope_match` (`yes`/`NO`), and `Present_in`
  (`both`/label). `Apply_to` is derived as `Entire org`, `Except groups`, or
  `Specific groups`.
- **Exit code**: `0` ok, `1` on HTTP failure (401/403 hint: needs Fabric admin).

---

## `validate_fabric.py` — prod workspace naming/security validation

The CI gate script. Validates every item in every **production** workspace
against the naming standard and flags individual users holding write-level
workspace roles. Prod workspaces are those whose `displayName` contains
`PROD_WORKSPACE_PATTERN` (default `-prod`).

### Checks

1. **Workspace naming** — `displayName` must match `{BU}-{Function}-{Env}`.
2. **Item naming** — every item must match the regex for its `type` (below);
   unknown types are skipped.
3. **Workspace security** — any `User`-type principal holding `Admin`, `Member`,
   or `Contributor` is a violation (`WRITE_ROLES`). Only groups and service
   principals may hold write roles in prod.

### Authentication

`DefaultAzureCredential`, Fabric scope, via the `fabric-gh-runner` managed
identity — no secrets. Uses member-scoped endpoints: `/workspaces`,
`/workspaces/{id}/items`, `/workspaces/{id}/roleAssignments` (follows
`continuationUri`).

### CLI / environment

No CLI flags.

| Env var | Default | Purpose |
|---------|---------|---------|
| `PROD_WORKSPACE_PATTERN` | `-prod` | Substring identifying prod workspaces |
| `REPORT_PATH` | `validation-report.json` | JSON output path |
| `GITHUB_OUTPUT` | `os.devnull` | Written with `passed=true|false` for the Actions job |

### Output

JSON report with `summary` (`workspaces_checked`, `items_checked`,
`naming_violations`, `security_violations`, `total_violations`) and a
`violations` array. Writes `passed=` to `GITHUB_OUTPUT`. Exit `0` if no
violations, else `1`.

### Naming patterns (`validate_fabric.py` / `audit_fabric.py`)

Tokens: `BU = (fin|mktg|hr|eng|sales|ops)`, `Env = (dev|tst|stg|prod)`,
`Layer = (raw|bronze|silver|gold)`, `Freq = (daily|hourly|weekly|adhoc)`,
`* = [a-z][a-z0-9_]*`.

Workspace pattern:

```
^(?:fin|mktg|hr|eng|sales|ops)-[a-z][a-z0-9-]*-(?:dev|tst|stg|prod)$    # {BU}-{Function}-{Env}
```

Per-item-type regexes (`ITEM_PATTERNS`):

| Item type | Regex | Gloss |
|-----------|-------|-------|
| Lakehouse | `^lh_{BU}_{Layer}_{Env}$` | `lh_{BU}_{Layer}_{Env}` |
| Warehouse | `^wh_{BU}_*_{Env}$` | `wh_{BU}_{Function}_{Env}` |
| DataPipeline | `^pl_{BU}_*_to_{Layer}_{Freq}$` | `pl_{BU}_{Source}_to_{Layer}_{Freq}` |
| Dataflow | `^df_{BU}_*_*_{Layer}$` | `df_{BU}_{Source}_{Domain}_{Layer}` |
| Notebook | `^nb_{BU}_*_*$` | `nb_{BU}_{Function}_{Domain}` |
| SparkJobDefinition | `^sj_{BU}_*_*_{Freq}$` | `sj_{BU}_{Function}_{Domain}_{Freq}` |
| SemanticModel | `^sm_{BU}_*_{Env}$` | `sm_{BU}_{Domain}_{Env}` |
| Report | `^rpt_{BU}_*_*$` | `rpt_{BU}_{Domain}_{Audience}` |
| PaginatedReport | `^prpt_{BU}_*_*$` | `prpt_{BU}_{Domain}_{Description}` |
| KQLDatabase | `^kql_{BU}_*_{Env}$` | `kql_{BU}_{Domain}_{Env}` |
| KQLQueryset | `^kqs_{BU}_*_*$` | `kqs_{BU}_{Domain}_{Purpose}` |
| Eventstream | `^es_{BU}_*_*$` | `es_{BU}_{Source}_{Domain}` |
| MLExperiment | `^exp_{BU}_*_*$` | `exp_{BU}_{Domain}_{Technique}` |
| MLModel | `^mdl_{BU}_*_v\d+$` | `mdl_{BU}_{Domain}_v{N}` |
| Reflex | `^rx_{BU}_*_*$` | `rx_{BU}_{Domain}_{Trigger}` |
| Environment | `^env_{BU}_*_{Env}$` | `env_{BU}_{Purpose}_{Env}` |
| Shortcut | `^sc_*_*$` | `sc_{SourceLakehouse}_{Domain}` |

`{BU}`/`{Env}`/`{Layer}`/`{Freq}` expand to the alternation groups above; `*`
expands to `[a-z][a-z0-9_]*`. `audit_fabric.py` uses the identical set (non-capturing
`(?:…)` variants).

---

## `configure_capacity.py` — apply capacity best practices

Applies [`capacity-best-practices.md`](../capacity-best-practices.md) to a Fabric
capacity via the **Power BI Admin REST API** (`api.powerbi.com/v1.0/myorg`).
Idempotent — only settings that differ from target are patched.

### What it sets (`[n/4]` steps)

| Step | Setting | Target |
|------|---------|--------|
| 1 | Administration members | Exactly the `Fabric-Capacity-Admins` group (`FABRIC_ADMINS_GROUP`) |
| 2 | Workload memory / timeout | SemanticModel 40% mem + 600 s query timeout; Dataflow 40%; PaginatedReport 20% (each `Enabled`) |
| 3 | Overload notifications | Enabled |
| 4 | Autoscale | Enabled for F64+ SKUs, `maxCapacityUnits` = base CUs × 1.25; skipped below F64 |

Workloads not available on the SKU are silently skipped. SKU→CU map covers
`F2`…`F2048`; autoscale minimum is 64 CUs (F64).

### Authentication

`DefaultAzureCredential`, **Power BI scope**
(`analysis.windows.net/powerbi/api/.default`). Identity needs the **Fabric
Capacity Administrator** role (provisioned by `terraform/policy.tf`) at the
subscription scope.

### CLI / environment

| Flag | Effect |
|------|--------|
| `--dry-run` | Print each `PATCH` URL + body, apply nothing |

| Env var | Required | Purpose |
|---------|----------|---------|
| `FABRIC_CAPACITY_ID` | yes | GUID of the capacity to configure |
| `FABRIC_ADMINS_GROUP` | yes | Object ID of the `Fabric-Capacity-Admins` Entra group |

### Output

Per-step status (`OK`/`Patching…`/`SKIP`/`ERROR`) then a result banner. Exit `0`
if all settings applied (or already correct); `1` if any setting failed or a
required env var is missing.

---

## `deploy_fabric.py` — trigger + poll a Deployment Pipeline

Promotes content between Fabric Deployment Pipeline stages, then polls the
long-running operation to completion. Invoked by the pipeline's `deploy` job.

`POST /deploymentPipelines/{id}/deploy` with `sourceStageOrder` /
`targetStageOrder`; deploy options are hard-set to **not** create new items in
prod (`allowCreateArtifact=false`), allow overwrite of existing items
(`allowOverwriteArtifact=true`), and forbid data purge / target-DB overwrite. A
`202` returns a `Location` header polled every 15 s until `succeeded` /
`failed` / `cancelled`, timing out at 30 minutes.

### Authentication

`DefaultAzureCredential`, Fabric scope, via the runner managed identity.

### Environment

| Env var | Purpose |
|---------|---------|
| `DEPLOYMENT_PIPELINE_ID` | GUID of the Fabric Deployment Pipeline |
| `SOURCE_STAGE_ORDER` | Stage index to promote from (e.g. `1` staging) |
| `TARGET_STAGE_ORDER` | Stage index to promote to (e.g. `2` production) |

Exit `0` on success, `1` on failure or timeout.

---

## `list_workspaces.py` — workspaces + capacity inventory

Lists every non-personal workspace tenant-wide and the capacity each is assigned
to, via the **Power BI Admin API**. Sources: `GET /admin/groups`
(`$filter=type ne 'PersonalGroup'`, paged by `$skip`, `$top=5000`) joined to
`GET /admin/capacities` for capacity display name + SKU. Workspaces off a
dedicated capacity show `(none - shared/Pro)`.

### Authentication

`DefaultAzureCredential`, Power BI scope. Requires a Fabric administrator
identity (`Tenant.Read.All`).

### CLI

| Flag | Effect |
|------|--------|
| `--csv PATH` | Also write results to CSV |
| `--active-only` | Only `Active`-state workspaces |
| `--on-capacity-only` | Only workspaces on a dedicated capacity |

### Output

Console table + optional CSV: `Workspace, Type, State, Capacity, SKU,
Workspace Id, Capacity Id`, sorted by capacity then workspace, with an
on-capacity/off-capacity count header. Exit `0` on success, `1` on request
failure.

---

## Tenant-settings best-practices catalog

Full catalog: [`tenant-settings-best-practices.md`](../tenant-settings-best-practices.md).
It is a curated subset of ~40 of the highest-impact security/DLP/governance
settings (not all ~167 tenant settings) and is mirrored 1:1 as the `CATALOG`
list in `assess_tenant_settings.py`. Uncatalogued settings are reported but never
touched.

Targets per profile (`—` = no position):

| Group | `settingName` | Risk | Light | Balanced | Paranoid |
|-------|---------------|:----:|:-----:|:--------:|:--------:|
| Export & sharing | `PublishToWeb` | ▲ | Off | Off | Off |
| Export & sharing | `ShareLinkToEntireOrg` | ▲ | — | Off | Off |
| Export & sharing | `ExternalSharingV2` | ▲ | — | Scope | Off |
| Export & sharing | `AllowGuestUserToAccessSharedContent` | ▲ | — | Scope | Off |
| Export & sharing | `ElevatedGuestsTenant` | ▲ | — | Off | Off |
| Export & sharing | `ExportReport` | ▲ | — | Scope | Off |
| Export & sharing | `EmailSubscriptionsToExternalUsers` | ▲ | — | Off | Off |
| Export & sharing | `AllowExternalDataSharingSwitch` | ▲ | — | Scope | Off |
| Export & sharing | `AllowExternalDataSharingReceiverSwitch` | ◆ | — | — | Off |
| Export & sharing | `ExternalDatasetSharingTenant` | ◆ | — | — | Off |
| Export & sharing | `EmailSubscriptionsToB2BUsers` | ◆ | — | — | Off |
| Export & sharing | `ExportToExcelSetting` | ◆ | — | — | Off |
| Export & sharing | `ExportToCsv` | ◆ | — | — | Off |
| Export & sharing | `ExportToPowerPoint` | ◆ | — | — | Off |
| Export & sharing | `ExportToImage` | ● | — | — | Off |
| Export & sharing | `ExportToWord` | ● | — | — | Off |
| Export & sharing | `ExportVisualImageTenant` | ● | — | — | Off |
| Export & sharing | `EmailSubscriptionTenant` | ● | — | — | — |
| Export & sharing | `AllowPowerBIASDQOnTenant` | ● | — | — | — |
| Export & sharing | `LiveConnection` | ● | — | — | — |
| Power BI visuals | `CertifiedCustomVisualsTenant` | ▲ | On | On | On |
| Power BI visuals | `CustomVisualsTenant` | ◆ | — | — | — |
| Power BI visuals | `AllowCVToExportDataToFileTenant` | ◆ | — | Off | Off |
| Power BI visuals | `AllowCVLocalStorageV2Tenant` | ● | — | — | Off |
| R & Python visuals | `RScriptVisual` | ◆ | — | Scope | Off |
| Datamart settings | `DatamartTenant` | ◆ | Off | Off | Off |
| Developer settings | `BlockResourceKeyAuthentication` | ▲ | — | On | On |
| Developer settings | `ServicePrincipalAccessGlobalAPIs` | ◆ | — | Scope | Scope |
| Developer settings | `ServicePrincipalAccessPermissionAPIs` | ◆ | — | Scope | Scope |
| Developer settings | `AllowServicePrincipalsCreateAndUseProfiles` | ● | — | Scope | Scope |
| Admin API settings | `AllowServicePrincipalsUseWriteAdminAPIs` | ▲ | — | Scope | Off |
| Admin API settings | `AllowServicePrincipalsUseReadAdminAPIs` | ◆ | — | Scope | Scope |
| Admin API settings | `AdminApisIncludeDetailedMetadata` | ● | — | On | On |
| Admin API settings | `AdminApisIncludeExpressions` | ● | — | On | On |
| Audit & usage | `UsageMetrics` | ● | — | On | On |
| Git integration | `GitIntegrationTenantSwitch` | ◆ | — | Scope | Scope |
| Git integration | `GitIntegrationSensitivityLabelsTenantSwitch` | ◆ | — | — | Off |
| Git integration | `GitIntegrationCrossGeoTenantSwitch` | ◆ | — | — | Off |
| Information protection | `BlockProtectedLabelSharingToEntireOrg` | ▲ | — | On | On |
| Information protection | `EimInformationProtectionEdit` | ▲ | — | On | On |
| Information protection | `EimInformationProtectionDataSourceInheritanceSetting` | ◆ | — | On | On |
| Information protection | `DataSecurityForAIInteractions` | ◆ | — | — | On |

Notes from the catalog: `DatamartTenant` governs a now-retired feature but the
setting persists (keep Off). The `Scope` targets require `FABRIC_RESTRICT_GROUP`
to auto-apply. Verified against the Fabric ecosystem June 2026 — no breaking API
changes; the **Update Tenant Setting** API remains in Preview.

---

## CI/CD — `fabric-prod-deploy.yml`

Workflow **"Fabric — Validate & Deploy to Production"**. Three jobs, all on the
self-hosted runner label `[self-hosted, fabric-gh-runner]`.

```
PR → validate → PR comment (blocks merge on failure)
push main → validate → [production approval] → deploy → post-deploy smoke check → configure-capacity
```

### Triggers

| Event | Jobs that run |
|-------|---------------|
| `pull_request` → `main` | `validate` only (posts/updates a PR comment) |
| `push` → `main` | `validate` → `deploy` → `configure-capacity` |

Workflow `permissions`: `contents: read`, `pull-requests: write` (for the PR
comment). Workflow env: `FABRIC_API`, `PROD_WORKSPACE_PATTERN: -prod`,
`PYTHON_VERSION: 3.12`.

### Job 1 — `validate`

Runs `scripts/validate_fabric.py` against all `*-prod` workspaces, uploads
`validation-report.json`, and — on `pull_request` — posts/updates a
`Fabric Governance Validation` PR comment (via `actions/github-script`)
summarising counts and listing any violations. Exposes job output
`passed = steps.run.outputs.passed` (the `passed=` line the script writes to
`GITHUB_OUTPUT`).

### Job 2 — `deploy`

`needs: validate`. Runs **only** when all three hold:

```yaml
github.event_name == 'push' &&
github.ref == 'refs/heads/main' &&
needs.validate.outputs.passed == 'true'
```

So a failing validation (or any PR event) gates deployment out entirely.
`environment: production` enforces the **manual approval gate** — configure
required reviewers under repo Settings → Environments; the job blocks until a
reviewer approves. Steps: `deploy_fabric.py` (trigger + poll the Deployment
Pipeline) then a **post-deploy smoke check** re-running `validate_fabric.py`
into `post-deploy-validation-report.json`.

### Job 3 — `configure-capacity`

`needs: deploy`, also `environment: production`. Runs
`scripts/configure_capacity.py` to apply capacity best practices via the Power BI
Admin API. Idempotent, so safe to re-run.

### Auth model — managed identity, no secrets

The `fabric-gh-runner` VM has a user-assigned managed identity
(`id-fabric-gh-runner`, provisioned by `terraform/github-runner-identity.tf`).
Every script uses `DefaultAzureCredential`, which picks up that identity
automatically. **No secrets or OIDC tokens are stored in GitHub.**

### Configuration — GitHub Actions *variables* (not secrets)

| Variable | Consumed by | Purpose |
|----------|-------------|---------|
| `FABRIC_DEPLOYMENT_PIPELINE_ID` | `deploy` → `DEPLOYMENT_PIPELINE_ID` | Deployment Pipeline GUID |
| `FABRIC_SOURCE_STAGE_ORDER` | `deploy` → `SOURCE_STAGE_ORDER` | Source stage (e.g. `1`) |
| `FABRIC_TARGET_STAGE_ORDER` | `deploy` → `TARGET_STAGE_ORDER` | Target stage (e.g. `2`) |
| `FABRIC_CAPACITY_ID` | `configure-capacity` → `FABRIC_CAPACITY_ID` | Capacity GUID |
| `FABRIC_ADMINS_GROUP_OID` | `configure-capacity` → `FABRIC_ADMINS_GROUP` | `Fabric-Capacity-Admins` object ID |

Setup also requires: a `production` GitHub Environment with required reviewers;
the `fabric-gh-runner` VM registered as a self-hosted runner with the
`fabric-gh-runner` label; and the runner identity granted `Contributor` on each
prod workspace (as `ServicePrincipal` principal type — a managed identity is an
Entra service principal to Fabric).
