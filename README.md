# FabricWAF

Terraform and governance standards for deploying Microsoft Fabric capacities on Azure in a well-architected, policy-enforced manner.

## Reference Architecture

![Reference Architecture](reference-architecture.svg)

## Contents

```
FabricWAF/
├── naming-standard.md          # Naming conventions for all Fabric resources
├── reference-architecture.md   # Architecture narrative and component breakdown
├── reference-architecture.svg  # Architecture diagram
└── terraform/
    ├── providers.tf         # AzureRM + AzureAD provider configuration
    ├── variables.tf         # Input variables with built-in validation
    ├── main.tf              # Fabric capacity resource + required tags
    ├── policy.tf            # Azure Policy definitions, assignments, RBAC role, and initiative
    └── outputs.tf           # Resource ID outputs
```

## Prerequisites

- Terraform >= 1.6.0
- AzureRM provider >= 4.0.0
- AzureAD provider >= 2.47.0
- An Entra security group named `Fabric-Capacity-Admins` (or set via `fabric_admins_group_name`)
- Azure subscription with permissions to create:
  - `Microsoft.Fabric/capacities`
  - `Microsoft.Authorization/policyDefinitions`
  - `Microsoft.Authorization/policyAssignments`
  - `Microsoft.Authorization/policySetDefinitions`
  - `Microsoft.Authorization/roleDefinitions`
  - `Microsoft.Authorization/roleAssignments`

## Usage

**1. Authenticate to Azure**

```bash
az login
az account set --subscription "<subscription-id>"
```

**2. Create a `terraform.tfvars` file**

```hcl
resource_group_name      = "rg-fabric-prod"
location                 = "eastus"
capacity_name            = "fin-dw-prod-eus"
sku_name                 = "F8"
fabric_admins_group_name = "Fabric-Capacity-Admins"
cost_center              = "CC-1234"
created_by               = "platform-team@contoso.com"
created_date             = "2026-03-18"
policy_scope             = "/subscriptions/<subscription-id>"
```

**3. Deploy**

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

## Fabric Capacity

The `azurerm_fabric_capacity` resource is created with the SKU you provide. Administration is locked to the `Fabric-Capacity-Admins` Entra group — the group's object ID is resolved at plan time via the `azuread` provider and set as the only `administration_members` entry.

Three tags are required on every capacity:

| Tag | Description |
|-----|-------------|
| `costCenter` | Cost center code for billing attribution |
| `createdDate` | ISO 8601 date the capacity was provisioned (`YYYY-MM-DD`) |
| `createdBy` | UPN or service principal that deployed the capacity |

Additional tags can be passed via `additional_tags`.

### Available SKUs

`F2` `F4` `F8` `F16` `F32` `F64` `F128` `F256` `F512` `F1024` `F2048`

## Azure Policies

Three custom policy definitions are deployed and bundled into the `fabric-capacity-governance` initiative.

### Policy 1 — US Regions Only

Denies `Microsoft.Fabric/capacities` deployments outside of US Azure regions.

Allowed regions: `eastus`, `eastus2`, `westus`, `westus2`, `westus3`, `centralus`, `northcentralus`, `southcentralus`, `westcentralus`

### Policy 2 — Naming Standard

Denies capacity names that do not match the naming pattern defined in [naming-standard.md](naming-standard.md):

```
{BU}-{Function}-{Env}-{Region}
```

| Token | Allowed values |
|-------|---------------|
| `{BU}` | `fin` `mktg` `hr` `eng` `sales` `ops` |
| `{Function}` | `dw` `analytics` `ingest` `ml` `report` |
| `{Env}` | `dev` `tst` `stg` `prod` |
| `{Region}` | `eus` `eus2` `wus` `wus2` `wus3` `cus` `ncus` `scus` `wcus` |

**Examples:** `fin-dw-prod-eus`, `eng-ml-stg-wus2`, `sales-analytics-dev-eus2`

The same regex is enforced locally in `variables.tf`, so `terraform plan` will catch a non-compliant name before it reaches Azure.

### Policy 3 — Admin Group Enforcement

Denies any capacity whose `administrationMembers` contains anyone other than the `Fabric-Capacity-Admins` Entra group. The policy triggers a `Deny` if either condition is true:

- Any member in the array is **not** the approved group's object ID
- The approved group is **absent** from the array entirely

This prevents ad-hoc individuals or other groups from being set as capacity admins through the portal or any other path.

## RBAC — Fabric Capacity Administrator Role

A custom Azure role (`Fabric Capacity Administrator`) is created and assigned exclusively to the `Fabric-Capacity-Admins` group at the policy scope. The role grants only the Fabric-specific actions needed to manage capacities:

| Action | Purpose |
|--------|---------|
| `Microsoft.Fabric/capacities/read` | View capacity |
| `Microsoft.Fabric/capacities/write` | Create or update capacity |
| `Microsoft.Fabric/capacities/delete` | Delete capacity |
| `Microsoft.Fabric/capacities/resume/action` | Resume a paused capacity |
| `Microsoft.Fabric/capacities/suspend/action` | Pause a running capacity |

> **Note:** If `Owner` or `Contributor` are already assigned at the subscription scope, those roles also carry `capacities/write`. Review existing broad role assignments and scope them down as needed — the custom role and policy together enforce intent, but broad roles at higher scopes can bypass the RBAC restriction. The Policy 3 `Deny` will still catch any capacity created with a non-compliant admin list regardless of who created it.

## Naming Standard

See [naming-standard.md](naming-standard.md) for the full naming convention covering all Fabric resource types: capacities, workspaces, lakehouses, warehouses, pipelines, notebooks, semantic models, and more.

## Variables Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `resource_group_name` | `string` | — | Resource group for the Fabric capacity |
| `location` | `string` | `eastus` | Azure region (US regions only) |
| `capacity_name` | `string` | — | Name following `{BU}-{Function}-{Env}-{Region}` |
| `sku_name` | `string` | `F2` | Fabric capacity SKU |
| `fabric_admins_group_name` | `string` | `Fabric-Capacity-Admins` | Display name of the Entra security group for capacity administration |
| `cost_center` | `string` | — | Cost center tag value |
| `created_by` | `string` | — | Created-by tag value |
| `created_date` | `string` | `2026-03-18` | Created-date tag value (`YYYY-MM-DD`) |
| `additional_tags` | `map(string)` | `{}` | Extra tags merged with required tags |
| `policy_scope` | `string` | — | ARM ID of the subscription or management group for policy assignment and role assignment |

## Outputs

| Output | Description |
|--------|-------------|
| `fabric_capacity_id` | Resource ID of the Fabric capacity |
| `fabric_capacity_name` | Name of the Fabric capacity |
| `fabric_admins_group_object_id` | Object ID of the resolved `Fabric-Capacity-Admins` Entra group |
| `us_regions_policy_id` | Resource ID of the US-regions-only policy definition |
| `naming_standard_policy_id` | Resource ID of the naming-standard policy definition |
| `admin_group_policy_id` | Resource ID of the admin-group-only policy definition |
| `fabric_capacity_admin_role_id` | Resource ID of the custom Fabric Capacity Administrator role definition |
| `governance_initiative_id` | Resource ID of the Fabric Governance policy initiative (v2.0.0) |
