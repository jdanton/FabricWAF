output "fabric_capacity_id" {
  description = "Resource ID of the Fabric capacity."
  value       = azurerm_fabric_capacity.this.id
}

output "fabric_capacity_name" {
  description = "Name of the Fabric capacity."
  value       = azurerm_fabric_capacity.this.name
}

output "us_regions_policy_id" {
  description = "Resource ID of the US-regions-only policy definition."
  value       = azurerm_policy_definition.fabric_us_regions_only.id
}

output "naming_standard_policy_id" {
  description = "Resource ID of the naming-standard policy definition."
  value       = azurerm_policy_definition.fabric_naming_standard.id
}

output "governance_initiative_id" {
  description = "Resource ID of the Fabric Governance policy initiative."
  value       = azurerm_policy_set_definition.fabric_governance.id
}
