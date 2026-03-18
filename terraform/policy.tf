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

  # Object ID of the Fabric-Capacity-Admins Entra group — shared by policy and RBAC resources.
  fabric_admins_object_id = data.azuread_group.fabric_capacity_admins.object_id
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
# Policy 3 — Enforce Fabric-Capacity-Admins as the only allowed admin group
#
# Two conditions both trigger Deny:
#   a) any administration member is NOT the allowed group object ID
#   b) the allowed group is absent from administration members entirely
#
# Note: Microsoft.Fabric/capacities/administration.members[*] is the ARM
# alias for the administrationMembers array on the capacity resource.
# ---------------------------------------------------------------------------

resource "azurerm_policy_definition" "fabric_admin_group_only" {
  name         = "fabric-capacity-admin-group-only"
  policy_type  = "Custom"
  mode         = "All"
  display_name = "Fabric Capacity — Fabric-Capacity-Admins group only"
  description  = "Denies Fabric capacities whose administrationMembers contains anyone other than the approved Entra group."

  metadata = jsonencode({
    category = "Microsoft Fabric"
    version  = "1.0.0"
  })

  parameters = jsonencode({
    allowedAdminObjectId = {
      type = "String"
      metadata = {
        displayName = "Allowed admin group object ID"
        description = "Object ID of the Entra security group permitted to be a Fabric capacity admin."
      }
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
          anyOf = [
            {
              # Deny if any member is NOT the approved group.
              count = {
                field = "Microsoft.Fabric/capacities/administration.members[*]"
                where = {
                  field    = "Microsoft.Fabric/capacities/administration.members[*]"
                  notEquals = "[parameters('allowedAdminObjectId')]"
                }
              }
              greater = 0
            },
            {
              # Deny if the approved group is not present at all.
              count = {
                field = "Microsoft.Fabric/capacities/administration.members[*]"
                where = {
                  field  = "Microsoft.Fabric/capacities/administration.members[*]"
                  equals = "[parameters('allowedAdminObjectId')]"
                }
              }
              equals = 0
            }
          ]
        }
      ]
    }
    then = {
      effect = "Deny"
    }
  })
}

resource "azurerm_policy_assignment" "fabric_admin_group_only" {
  name                 = "fabric-admin-group-only"
  scope                = var.policy_scope
  policy_definition_id = azurerm_policy_definition.fabric_admin_group_only.id
  display_name         = "Fabric Capacity — Fabric-Capacity-Admins group only"
  description          = "Blocks capacities that set any admin other than the Fabric-Capacity-Admins Entra group."

  enforce = true

  parameters = jsonencode({
    allowedAdminObjectId = {
      value = local.fabric_admins_object_id
    }
  })
}

# ---------------------------------------------------------------------------
# RBAC — Custom role scoped to Fabric capacity operations
#
# Only members of the Fabric-Capacity-Admins group get this role, which
# means they are the only principals who can create/update/delete capacities.
#
# NOTE: Broad roles (Owner, Contributor) assigned at the subscription also
# carry Microsoft.Fabric/capacities/write. Review existing role assignments
# at var.policy_scope and remove or scope them down as needed.
# ---------------------------------------------------------------------------

resource "azurerm_role_definition" "fabric_capacity_admin" {
  name        = "Fabric Capacity Administrator"
  scope       = var.policy_scope
  description = "Can create, read, update, delete, suspend, and resume Microsoft Fabric capacities. Assign only to the Fabric-Capacity-Admins group."

  permissions {
    actions = [
      "Microsoft.Fabric/capacities/read",
      "Microsoft.Fabric/capacities/write",
      "Microsoft.Fabric/capacities/delete",
      "Microsoft.Fabric/capacities/resume/action",
      "Microsoft.Fabric/capacities/suspend/action",
      "Microsoft.Resources/subscriptions/resourceGroups/read",
    ]
    not_actions = []
  }

  assignable_scopes = [var.policy_scope]
}

resource "azurerm_role_assignment" "fabric_capacity_admins_group" {
  scope              = var.policy_scope
  role_definition_id = azurerm_role_definition.fabric_capacity_admin.role_definition_resource_id
  principal_id       = local.fabric_admins_object_id
}

# ---------------------------------------------------------------------------
# Initiative — bundle all three policies
# ---------------------------------------------------------------------------

resource "azurerm_policy_set_definition" "fabric_governance" {
  name         = "fabric-capacity-governance"
  policy_type  = "Custom"
  display_name = "Fabric Capacity Governance"
  description  = "Enforces US-only regions, naming standards, and admin-group restrictions for Microsoft Fabric capacities."

  metadata = jsonencode({
    category = "Microsoft Fabric"
    version  = "2.0.0"
  })

  parameters = jsonencode({
    allowedAdminObjectId = {
      type = "String"
      metadata = {
        displayName = "Allowed admin group object ID"
        description = "Object ID of the Entra group permitted to administer Fabric capacities."
      }
    }
    namingRegex = {
      type = "String"
      metadata = {
        displayName = "Capacity naming regex"
        description = "RE2 regex the capacity name must match."
      }
      defaultValue = local.fabric_naming_regex
    }
  })

  policy_definition_reference {
    policy_definition_id = azurerm_policy_definition.fabric_us_regions_only.id
    reference_id         = "fabric-us-regions-only"
  }

  policy_definition_reference {
    policy_definition_id = azurerm_policy_definition.fabric_naming_standard.id
    reference_id         = "fabric-naming-standard"

    parameter_values = jsonencode({
      namingRegex = { value = "[parameters('namingRegex')]" }
    })
  }

  policy_definition_reference {
    policy_definition_id = azurerm_policy_definition.fabric_admin_group_only.id
    reference_id         = "fabric-admin-group-only"

    parameter_values = jsonencode({
      allowedAdminObjectId = { value = "[parameters('allowedAdminObjectId')]" }
    })
  }
}
