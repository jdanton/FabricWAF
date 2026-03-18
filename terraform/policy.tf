# ---------------------------------------------------------------------------
# Policy 1 — Restrict Fabric capacity deployments to US regions
# ---------------------------------------------------------------------------

resource "azurerm_policy_definition" "fabric_us_regions_only" {
  name         = "fabric-capacity-us-regions-only"
  policy_type  = "Custom"
  mode         = "All"
  display_name = "Fabric Capacity — US regions only"
  description  = "Denies Microsoft.Fabric/capacities deployments outside of US Azure regions."

  metadata = jsonencode({
    category = "Microsoft Fabric"
    version  = "1.0.0"
  })

  policy_rule = jsonencode({
    if = {
      allOf = [
        {
          field  = "type"
          equals = "Microsoft.Fabric/capacities"
        },
        {
          field = "location"
          notIn = [
            "eastus",
            "eastus2",
            "westus",
            "westus2",
            "westus3",
            "centralus",
            "northcentralus",
            "southcentralus",
            "westcentralus"
          ]
        }
      ]
    }
    then = {
      effect = "Deny"
    }
  })
}

resource "azurerm_policy_assignment" "fabric_us_regions_only" {
  name                 = "fabric-us-regions-only"
  scope                = var.policy_scope
  policy_definition_id = azurerm_policy_definition.fabric_us_regions_only.id
  display_name         = "Fabric Capacity — US regions only"
  description          = "Denies Fabric capacity deployments outside of US Azure regions."

  enforce = true
}

# ---------------------------------------------------------------------------
# Policy 2 — Enforce naming standard on Fabric capacities
#
# Pattern: {BU}-{Function}-{Env}-{Region}
#   BU       : fin | mktg | hr | eng | sales | ops
#   Function : dw | analytics | ingest | ml | report
#   Env      : dev | tst | stg | prod
#   Region   : eus | eus2 | wus | wus2 | wus3 | cus | ncus | scus | wcus
#
# Example: fin-dw-prod-eus
# ---------------------------------------------------------------------------

locals {
  # Regex used by both the policy rule and the deny message.
  # Azure Policy uses RE2 syntax (no lookahead/lookbehind).
  fabric_naming_regex = "^(fin|mktg|hr|eng|sales|ops)-(dw|analytics|ingest|ml|report)-(dev|tst|stg|prod)-(eus|eus2|wus|wus2|wus3|cus|ncus|scus|wcus)$"
}

resource "azurerm_policy_definition" "fabric_naming_standard" {
  name         = "fabric-capacity-naming-standard"
  policy_type  = "Custom"
  mode         = "All"
  display_name = "Fabric Capacity — enforce naming standard"
  description  = "Denies Fabric capacity names that do not match the pattern {BU}-{Function}-{Env}-{Region}."

  metadata = jsonencode({
    category = "Microsoft Fabric"
    version  = "1.0.0"
  })

  parameters = jsonencode({
    namingRegex = {
      type = "String"
      metadata = {
        displayName = "Naming regex"
        description = "RE2 regular expression the capacity name must match."
      }
      defaultValue = local.fabric_naming_regex
    }
  })

  policy_rule = jsonencode({
    if = {
      allOf = [
        {
          field  = "type"
          equals = "Microsoft.Fabric/capacities"
        },
        {
          # Deny when the name does NOT match the naming pattern.
          not = {
            field = "name"
            match = "[parameters('namingRegex')]"
          }
        }
      ]
    }
    then = {
      effect = "Deny"
    }
  })
}

resource "azurerm_policy_assignment" "fabric_naming_standard" {
  name                 = "fabric-naming-standard"
  scope                = var.policy_scope
  policy_definition_id = azurerm_policy_definition.fabric_naming_standard.id
  display_name         = "Fabric Capacity — enforce naming standard"
  description          = "Blocks Fabric capacities whose names do not follow {BU}-{Function}-{Env}-{Region}."

  enforce = true

  parameters = jsonencode({
    namingRegex = {
      value = local.fabric_naming_regex
    }
  })
}

# ---------------------------------------------------------------------------
# Optional: bundle both policies into an initiative (policy set)
# ---------------------------------------------------------------------------

resource "azurerm_policy_set_definition" "fabric_governance" {
  name         = "fabric-capacity-governance"
  policy_type  = "Custom"
  display_name = "Fabric Capacity Governance"
  description  = "Initiative that enforces US-only regions and naming standards for Microsoft Fabric capacities."

  metadata = jsonencode({
    category = "Microsoft Fabric"
    version  = "1.0.0"
  })

  policy_definition_reference {
    policy_definition_id = azurerm_policy_definition.fabric_us_regions_only.id
    reference_id         = "fabric-us-regions-only"
  }

  policy_definition_reference {
    policy_definition_id = azurerm_policy_definition.fabric_naming_standard.id
    reference_id         = "fabric-naming-standard"

    parameter_values = jsonencode({
      namingRegex = {
        value = local.fabric_naming_regex
      }
    })
  }
}
