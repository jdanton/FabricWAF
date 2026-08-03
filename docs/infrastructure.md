# Infrastructure (Terraform)

Reference for the Terraform configuration under [`terraform/`](../terraform). It provisions a single Microsoft Fabric capacity, three Azure Policy definitions bundled into a governance initiative, a custom RBAC role, and a managed identity for the self-hosted GitHub Actions runner.

## Providers

Defined in [`providers.tf`](../terraform/providers.tf).

| Requirement | Version |
|-------------|---------|
| Terraform core | `>= 1.6.0` |
| `hashicorp/azurerm` | `>= 4.0.0` |
| `hashicorp/azuread` | `>= 2.47.0` |

```hcl
provider "azurerm" {
  features {}
}

provider "azuread" {}
```

The `azuread` provider resolves the admin group by display name; `azurerm` provisions the capacity, policies, and RBAC.

---

## 1. Fabric Capacity

Defined in [`main.tf`](../terraform/main.tf) as `azurerm_fabric_capacity.this`.

```hcl
resource "azurerm_fabric_capacity" "this" {
  name                = var.capacity_name
  resource_group_name = var.resource_group_name
  location            = var.location

  sku {
    name = var.sku_name
    tier = "Fabric"
  }

  administration_members = [data.azuread_group.fabric_capacity_admins.object_id]

  tags = local.tags
}
```

### SKU handling

- The `sku` block always sets `tier = "Fabric"`. Only `sku.name` is variable, driven by `var.sku_name` (default `F2`).
- Valid values are enforced by variable validation: `F2` `F4` `F8` `F16` `F32` `F64` `F128` `F256` `F512` `F1024` `F2048`.

### Required tags

`local.required_tags` is merged with `var.additional_tags` (required tags win on key collision):

```hcl
locals {
  required_tags = {
    costCenter  = var.cost_center
    createdDate = var.created_date
    createdBy   = var.created_by
  }

  tags = merge(local.required_tags, var.additional_tags)
}
```

| Tag | Source variable | Description |
|-----|-----------------|-------------|
| `costCenter` | `cost_center` | Cost center code for billing attribution |
| `createdDate` | `created_date` | ISO 8601 date the capacity was provisioned (`YYYY-MM-DD`) |
| `createdBy` | `created_by` | UPN or service principal that deployed the capacity |

### Admin-group locking

The `Fabric-Capacity-Admins` Entra security group is resolved at plan time via a data source:

```hcl
data "azuread_group" "fabric_capacity_admins" {
  display_name     = var.fabric_admins_group_name
  security_enabled = true
}
```

Its `object_id` is the **only** entry in `administration_members`. This is enforced twice — once here in the resource, and again by Policy 3 (below) so drift is denied at the control plane.

---

## 2. Azure Policies

Defined in [`policy.tf`](../terraform/policy.tf). Three custom `azurerm_policy_definition` resources (`policy_type = "Custom"`, `mode = "All"`, metadata `category = "Microsoft Fabric"`, `version = "1.0.0"`), each paired with an `azurerm_policy_assignment` at `var.policy_scope` with `enforce = true`. All three use the `Deny` effect.

### Policy 1 — US regions only

`fabric-capacity-us-regions-only`. Denies `Microsoft.Fabric/capacities` whose `location` is **not in** the US region allow-list.

```json
"if": {
  "allOf": [
    { "field": "type", "equals": "Microsoft.Fabric/capacities" },
    { "field": "location", "notIn": [ /* US regions */ ] }
  ]
},
"then": { "effect": "Deny" }
```

Allowed regions: `eastus`, `eastus2`, `westus`, `westus2`, `westus3`, `centralus`, `northcentralus`, `southcentralus`, `westcentralus`.

### Policy 2 — Naming standard

`fabric-capacity-naming-standard`. Denies any Fabric capacity whose `name` does **not** match the naming regex. The regex is passed as a policy parameter (`namingRegex`, type `String`) so it stays in sync with the same `local.fabric_naming_regex` used by the `capacity_name` variable validation.

```hcl
fabric_naming_regex = "^(fin|mktg|hr|eng|sales|ops)-(dw|analytics|ingest|ml|report)-(dev|tst|stg|prod)-(eus|eus2|wus|wus2|wus3|cus|ncus|scus|wcus)$"
```

```json
"if": {
  "allOf": [
    { "field": "type", "equals": "Microsoft.Fabric/capacities" },
    { "not": { "field": "name", "match": "[parameters('namingRegex')]" } }
  ]
},
"then": { "effect": "Deny" }
```

> Azure Policy uses RE2 syntax (no lookahead/lookbehind), as noted in the code.

### Policy 3 — Admin-group enforcement

`fabric-capacity-admin-group-only`. Takes an `allowedAdminObjectId` parameter (the `Fabric-Capacity-Admins` object ID, resolved via `local.fabric_admins_object_id`). It denies a capacity if **either** condition is true — combined with `anyOf`:

- **(a)** any entry in `administration.members[*]` is **not** the approved group (`count ... where notEquals ... greater 0`), or
- **(b)** the approved group is **absent** from `administration.members[*]` entirely (`count ... where equals ... equals 0`).

```json
"anyOf": [
  { "count": { "field": "Microsoft.Fabric/capacities/administration.members[*]",
               "where": { "field": "...administration.members[*]",
                          "notEquals": "[parameters('allowedAdminObjectId')]" } },
    "greater": 0 },
  { "count": { "field": "Microsoft.Fabric/capacities/administration.members[*]",
               "where": { "field": "...administration.members[*]",
                          "equals": "[parameters('allowedAdminObjectId')]" } },
    "equals": 0 }
]
```

`Microsoft.Fabric/capacities/administration.members[*]` is the ARM alias for the capacity's `administrationMembers` array.

### Governance initiative

`azurerm_policy_set_definition.fabric_governance` (`fabric-capacity-governance`, metadata `version = "2.0.0"`) bundles all three definitions via `policy_definition_reference` blocks:

| Reference ID | Definition | Parameters passed |
|--------------|------------|-------------------|
| `fabric-us-regions-only` | Policy 1 | none |
| `fabric-naming-standard` | Policy 2 | `namingRegex` → initiative `namingRegex` param |
| `fabric-admin-group-only` | Policy 3 | `allowedAdminObjectId` → initiative `allowedAdminObjectId` param |

The initiative exposes two parameters: `allowedAdminObjectId` (String, required) and `namingRegex` (String, default `local.fabric_naming_regex`). The individual assignments in `policy.tf` enforce the same three definitions directly; the initiative packages them for assignment as a single unit.

---

## 3. Custom RBAC role — `Fabric Capacity Administrator`

`azurerm_role_definition.fabric_capacity_admin`, scoped to `var.policy_scope` with `assignable_scopes = [var.policy_scope]`.

```hcl
actions = [
  "Microsoft.Fabric/capacities/read",
  "Microsoft.Fabric/capacities/write",
  "Microsoft.Fabric/capacities/delete",
  "Microsoft.Fabric/capacities/resume/action",
  "Microsoft.Fabric/capacities/suspend/action",
  "Microsoft.Resources/subscriptions/resourceGroups/read",
]
not_actions = []
```

Assigned to the `Fabric-Capacity-Admins` group via `azurerm_role_assignment.fabric_capacity_admins_group` (`principal_id = local.fabric_admins_object_id`, `scope = var.policy_scope`).

> The role grants full capacity lifecycle (create/read/update/delete, suspend, resume). Note in code: broad built-in roles (Owner, Contributor) at the subscription also carry `Microsoft.Fabric/capacities/write` — review and scope those down separately.

---

## 4. GitHub runner managed identity

Defined in [`github-runner-identity.tf`](../terraform/github-runner-identity.tf) for the `fabric-gh-runner` self-hosted GitHub Actions runner VM.

**Creates / manages:**

| Resource | Purpose |
|----------|---------|
| `data.azurerm_virtual_machine.gh_runner` | Looks up the existing `fabric-gh-runner` VM in `var.resource_group_name` |
| `azurerm_user_assigned_identity.gh_runner` | User-assigned identity `id-fabric-gh-runner` (tagged with `local.tags`) |
| `azurerm_virtual_machine_extension.gh_runner_identity` | `ManagedIdentityExtensionForLinux` extension on the runner VM |
| `azurerm_linux_virtual_machine_identity.gh_runner` | Attaches the user-assigned identity to the VM (`type = "UserAssigned"`) |
| `azurerm_role_assignment.gh_runner_fabric_capacity` | Grants the identity the **Fabric Capacity Administrator** custom role at `var.policy_scope` |

**Azure RBAC granted:** the same custom `Fabric Capacity Administrator` role (section 3), so the runner can read/update capacities at the policy scope.

**Fabric workspace membership is out-of-band.** Contributor access on production Fabric workspaces is **not** manageable through Azure RBAC or Terraform. It must be granted separately via the Fabric Admin API or portal, using the identity's principal ID (exposed as the `gh_runner_identity_principal_id` output):

```http
PATCH https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/roleAssignments
{
  "role": "Contributor",
  "principal": { "id": "<gh_runner_identity_principal_id>", "type": "ServicePrincipal" }
}
```

---

## 5. Inputs and outputs

### Input variables ([`variables.tf`](../terraform/variables.tf))

| Variable | Type | Purpose | Default | Validation |
|----------|------|---------|---------|------------|
| `resource_group_name` | `string` | Resource group for the Fabric capacity | — (required) | none |
| `location` | `string` | Azure region for the capacity (must be US) | `eastus` | must be one of the 9 US regions |
| `capacity_name` | `string` | Capacity name (see naming standard) | — (required) | regex `^(fin\|mktg\|hr\|eng\|sales\|ops)-(dw\|analytics\|ingest\|ml\|report)-(dev\|tst\|stg\|prod)-(eus\|eus2\|wus\|wus2\|wus3\|cus\|ncus\|scus\|wcus)$` |
| `sku_name` | `string` | Fabric capacity SKU name | `F2` | regex `^F(2\|4\|8\|16\|32\|64\|128\|256\|512\|1024\|2048)$` |
| `fabric_admins_group_name` | `string` | Display name of the admin Entra security group | `Fabric-Capacity-Admins` | none |
| `cost_center` | `string` | Cost center code for billing attribution | — (required) | none |
| `created_by` | `string` | Identity (UPN or SP) that provisioned the capacity | — (required) | none |
| `created_date` | `string` | ISO 8601 creation date | `2026-03-18` | regex `^\d{4}-\d{2}-\d{2}$` |
| `additional_tags` | `map(string)` | Extra tags merged with the required tags | `{}` | none |
| `policy_scope` | `string` | ARM resource ID (subscription or mgmt group) at which to assign policies and RBAC | — (required) | none |

### Outputs ([`outputs.tf`](../terraform/outputs.tf), [`github-runner-identity.tf`](../terraform/github-runner-identity.tf))

| Output | Description | Source |
|--------|-------------|--------|
| `fabric_capacity_id` | Resource ID of the Fabric capacity | `outputs.tf` |
| `fabric_capacity_name` | Name of the Fabric capacity | `outputs.tf` |
| `fabric_admins_group_object_id` | Object ID of the `Fabric-Capacity-Admins` group | `outputs.tf` |
| `us_regions_policy_id` | Resource ID of the US-regions-only policy definition | `outputs.tf` |
| `naming_standard_policy_id` | Resource ID of the naming-standard policy definition | `outputs.tf` |
| `admin_group_policy_id` | Resource ID of the admin-group-only policy definition | `outputs.tf` |
| `fabric_capacity_admin_role_id` | Resource ID of the custom role definition | `outputs.tf` |
| `governance_initiative_id` | Resource ID of the governance initiative | `outputs.tf` |
| `gh_runner_identity_principal_id` | Object ID of the `fabric-gh-runner` identity (for Fabric workspace grants) | `github-runner-identity.tf` |
| `gh_runner_identity_client_id` | Client ID of the `fabric-gh-runner` identity (for `DefaultAzureCredential`) | `github-runner-identity.tf` |

---

## 6. Naming standard

Full detail in [`naming-standard.md`](../naming-standard.md). The core convention is `{BU}-{Function}-{Env}-{Region}`, applied consistently across all Fabric resources. Fabric has no built-in policy engine, so naming is enforced by discipline, automation, and (for capacities) the Azure Policy above.

**Convention tokens:**

| Token | Meaning | Examples |
|-------|---------|----------|
| `{BU}` | Business unit | `fin`, `mktg`, `hr`, `eng`, `sales`, `ops` |
| `{Function}` | Workload purpose | `dw`, `analytics`, `ingest`, `ml`, `report` |
| `{Env}` | Environment tier | `dev`, `tst`, `stg`, `prod` |
| `{Region}` | Azure region short code | `eus`, `eus2`, `wus`, `wus2`, `wus3`, `cus`, `ncus`, `scus`, `wcus` |
| `{Layer}` | Medallion layer | `raw`, `bronze`, `silver`, `gold` |
| `{Source}` / `{Domain}` / `{Freq}` | Source system / data domain / schedule | `sap`, `crm` / `customers`, `orders` / `daily`, `hourly` |

**General rules:** all lowercase, no spaces; hyphens between tokens, underscores within tokens; ISO date stamps (`YYYYMMDD`) where needed.

**Per-resource patterns (quick reference):**

| Resource | Prefix | Pattern | Example |
|----------|--------|---------|---------|
| Capacity | — | `{BU}-{Function}-{Env}-{Region}` | `fin-dw-prod-eus` |
| Domain | — | `{BU}` or `{BU}-{SubDomain}` | `finance` |
| Workspace | — | `{BU}-{Function}-{Env}` | `fin-dw-prod` |
| Lakehouse | `lh_` | `lh_{BU}_{Layer}_{Env}` | `lh_fin_gold_prod` |
| Warehouse | `wh_` | `wh_{BU}_{Function}_{Env}` | `wh_fin_dw_prod` |
| Pipeline | `pl_` | `pl_{BU}_{Source}_to_{Layer}_{Freq}` | `pl_fin_sap_to_bronze_daily` |
| Dataflow Gen2 | `df_` | `df_{BU}_{Source}_{Domain}_{Layer}` | `df_fin_sap_gl_bronze` |
| Notebook | `nb_` | `nb_{BU}_{Function}_{Domain}` | `nb_fin_transform_gl_entries` |
| Spark Job | `sj_` | `sj_{BU}_{Function}_{Domain}_{Freq}` | `sj_fin_aggregate_gl_daily` |
| Semantic Model | `sm_` | `sm_{BU}_{Domain}_{Env}` | `sm_fin_profitability_prod` |
| Report | `rpt_` | `rpt_{BU}_{Domain}_{Audience}` | `rpt_fin_monthly_close_exec` |
| Paginated Report | `prpt_` | `prpt_{BU}_{Domain}_{Desc}` | `prpt_fin_invoice_detail` |
| KQL Database | `kql_` | `kql_{BU}_{Domain}_{Env}` | `kql_eng_telemetry_prod` |
| KQL Queryset | `kqs_` | `kqs_{BU}_{Domain}_{Purpose}` | `kqs_eng_telemetry_anomalies` |
| Eventstream | `es_` | `es_{BU}_{Source}_{Domain}` | `es_eng_iot_sensor_readings` |
| ML Experiment | `exp_` | `exp_{BU}_{Domain}_{Technique}` | `exp_fin_fraud_xgboost` |
| ML Model | `mdl_` | `mdl_{BU}_{Domain}_{Version}` | `mdl_fin_fraud_v1` |
| Delta Table | — | `{Layer}_{Domain}_{Entity}` | `gold_fact_sales` |
| Shortcut | `sc_` | `sc_{Source}_{Domain}` | `sc_lh_fin_raw_customers` |
| Reflex | `rx_` | `rx_{BU}_{Domain}_{Trigger}` | `rx_sales_pipeline_deal_alert` |
| Environment | `env_` | `env_{BU}_{Purpose}_{Env}` | `env_fin_spark_prod` |

The capacity's Terraform `capacity_name` validation and Azure Policy 2 both enforce the capacity row of this table.

---

## 7. Capacity best-practices target state

Full detail in [`capacity-best-practices.md`](../capacity-best-practices.md). This is the target configuration applied by `scripts/configure_capacity.py` (run as the **Configure Capacity** job in the deploy pipeline, or manually via `FABRIC_CAPACITY_ID=<guid> python scripts/configure_capacity.py`). Terraform provisions the capacity; the script applies these runtime settings via the Power BI Admin API and Fabric REST API.

| Area | Target state |
|------|--------------|
| Administration | Exactly one `administrationMembers` entry — the `Fabric-Capacity-Admins` group object ID (mirrors Policy 3); no individual users |
| Workload memory | Max memory %: `SemanticModel` 40%, `Dataflow` 40%, `PaginatedReport` 20% (ceilings, not reservations; enforced per workload) |
| Semantic model query timeout | `QueryTimeout` = 600 s; over-limit queries are cancelled |
| Overload notifications | Enabled; emails all `Fabric-Capacity-Admins` members when the capacity is throttled (sustained ≥100% CU) |
| Workspace assignment | `SpecificUsersAndGroups` — `Fabric-Capacity-Admins` only |
| Autoscale | Enabled on F64+ (silently skipped below F64); max autoscale CUs 25% above base SKU |

Deviations require a PR that updates both `capacity-best-practices.md` and the matching constant in `scripts/configure_capacity.py`, approved by a `Fabric-Capacity-Admins` member. Ad-hoc portal changes are overwritten on the next deployment run.
