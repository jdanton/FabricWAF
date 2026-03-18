# FabricWAF

Terraform and governance standards for deploying Microsoft Fabric capacities on Azure in a well-architected, policy-enforced manner.

## Contents

```
FabricWAF/
├── naming-standard.md      # Naming conventions for all Fabric resources
└── terraform/
    ├── providers.tf         # AzureRM provider configuration
    ├── variables.tf         # Input variables with built-in validation
    ├── main.tf              # Fabric capacity resource + required tags
    ├── policy.tf            # Azure Policy definitions, assignments, and initiative
    └── outputs.tf           # Resource ID outputs
```

## Prerequisites

- Terraform >= 1.6.0
- AzureRM provider >= 4.0.0
- Azure subscription with permissions to create:
  - `Microsoft.Fabric/capacities`
  - `Microsoft.Authorization/policyDefinitions`
  - `Microsoft.Authorization/policyAssignments`
  - `Microsoft.Authorization/policySetDefinitions`

## Usage

**1. Authenticate to Azure**

```bash
az login
az account set --subscription "<subscription-id>"
```

**2. Create a `terraform.tfvars` file**

```hcl
resource_group_name    = "rg-fabric-prod"
location               = "eastus"
capacity_name          = "fin-dw-prod-eus"
sku_name               = "F8"
administration_members = ["admin@contoso.com"]
cost_center            = "CC-1234"
created_by             = "platform-team@contoso.com"
created_date           = "2026-03-18"
policy_scope           = "/subscriptions/<subscription-id>"
```

**3. Deploy**

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

## Fabric Capacity

The `azurerm_fabric_capacity` resource is created with the SKU and admin members you provide. Three tags are required on every capacity:

| Tag | Description |
|-----|-------------|
| `costCenter` | Cost center code for billing attribution |
| `createdDate` | ISO 8601 date the capacity was provisioned (`YYYY-MM-DD`) |
| `createdBy` | UPN or service principal that deployed the capacity |

Additional tags can be passed via `additional_tags`.

### Available SKUs

`F2` `F4` `F8` `F16` `F32` `F64` `F128` `F256` `F512` `F1024` `F2048`

## Azure Policies

Two custom policy definitions are deployed and bundled into a single initiative (`fabric-capacity-governance`).

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

## Naming Standard

See [naming-standard.md](naming-standard.md) for the full naming convention covering all Fabric resource types: capacities, workspaces, lakehouses, warehouses, pipelines, notebooks, semantic models, and more.

## Variables Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `resource_group_name` | `string` | — | Resource group for the Fabric capacity |
| `location` | `string` | `eastus` | Azure region (US regions only) |
| `capacity_name` | `string` | — | Name following `{BU}-{Function}-{Env}-{Region}` |
| `sku_name` | `string` | `F2` | Fabric capacity SKU |
| `administration_members` | `list(string)` | — | UPNs or object IDs of capacity admins |
| `cost_center` | `string` | — | Cost center tag value |
| `created_by` | `string` | — | Created-by tag value |
| `created_date` | `string` | `2026-03-18` | Created-date tag value (`YYYY-MM-DD`) |
| `additional_tags` | `map(string)` | `{}` | Extra tags merged with required tags |
| `policy_scope` | `string` | — | ARM ID of the subscription or management group for policy assignment |

## Outputs

| Output | Description |
|--------|-------------|
| `fabric_capacity_id` | Resource ID of the Fabric capacity |
| `fabric_capacity_name` | Name of the Fabric capacity |
| `us_regions_policy_id` | Resource ID of the US-regions-only policy definition |
| `naming_standard_policy_id` | Resource ID of the naming-standard policy definition |
| `governance_initiative_id` | Resource ID of the Fabric Governance policy initiative |
