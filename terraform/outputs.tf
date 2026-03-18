output "fabric_capacity_id" {
  description = "Resource ID of the Fabric capacity."
  value       = azurerm_fabric_capacity.this.id
}

output "fabric_capacity_name" {
  description = "Name of the Fabric capacity."
  value       = azurerm_fabric_capacity.this.name
}

output "fabric_admins_group_object_id" {
  description = "Object ID of the Fabric-Capacity-Admins Entra group."
  value       = data.azuread_group.fabric_capacity_admins.object_id
}

output "us_regions_policy_id" {
  description = "Resource ID of the US-regions-only policy definition."
  value       = azurerm_policy_definition.fabric_us_regions_only.id
}

output "naming_standard_policy_id" {
  description = "Resource ID of the naming-standard policy definition."
  value       = azurerm_policy_definition.fabric_naming_standard.id
}

output "admin_group_policy_id" {
  description = "Resource ID of the admin-group-only policy definition."
  value       = azurerm_policy_definition.fabric_admin_group_only.id
}

output "fabric_capacity_admin_role_id" {
  description = "Resource ID of the custom Fabric Capacity Administrator role definition."
  value       = azurerm_role_definition.fabric_capacity_admin.role_definition_resource_id
}

output "governance_initiative_id" {
  description = "Resource ID of the Fabric Governance policy initiative."
  value       = azurerm_policy_set_definition.fabric_governance.id
}
