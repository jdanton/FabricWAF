data "azuread_group" "fabric_capacity_admins" {
  display_name     = var.fabric_admins_group_name
  security_enabled = true
}

locals {
  required_tags = {
    costCenter  = var.cost_center
    createdDate = var.created_date
    createdBy   = var.created_by
  }

  tags = merge(local.required_tags, var.additional_tags)
}

resource "azurerm_fabric_capacity" "this" {
  name                = var.capacity_name
  resource_group_name = var.resource_group_name
  location            = var.location

  sku {
    name = var.sku_name
    tier = "Fabric"
  }

  # Locked to the Fabric-Capacity-Admins Entra group; enforced by policy as well.
  administration_members = [data.azuread_group.fabric_capacity_admins.object_id]

  tags = local.tags
}
