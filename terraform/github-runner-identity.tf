# ---------------------------------------------------------------------------
# Managed identity for the fabric-gh-runner self-hosted GitHub Actions runner
#
# This creates a user-assigned managed identity, attaches it to the runner VM,
# and grants it the permissions it needs to validate and deploy Fabric content.
#
# Fabric workspace membership (Contributor on prod workspaces) must be set
# separately via the Fabric Admin API or portal — it is not manageable through
# Azure RBAC or Terraform.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Data — look up the existing runner VM
# ---------------------------------------------------------------------------

data "azurerm_virtual_machine" "gh_runner" {
  name                = "fabric-gh-runner"
  resource_group_name = var.resource_group_name
}

# ---------------------------------------------------------------------------
# User-assigned managed identity
# ---------------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "gh_runner" {
  name                = "id-fabric-gh-runner"
  resource_group_name = var.resource_group_name
  location            = var.location

  tags = local.tags
}

# Attach the managed identity to the runner VM
resource "azurerm_virtual_machine_extension" "gh_runner_identity" {
  name                       = "ManagedIdentityExtensionForLinux"
  virtual_machine_id         = data.azurerm_virtual_machine.gh_runner.id
  publisher                  = "Microsoft.ManagedIdentity"
  type                       = "ManagedIdentityExtensionForLinux"
  type_handler_version       = "1.0"
  auto_upgrade_minor_version = true
}

resource "azurerm_linux_virtual_machine_identity" "gh_runner" {
  virtual_machine_id = data.azurerm_virtual_machine.gh_runner.id

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.gh_runner.id]
  }
}

# ---------------------------------------------------------------------------
# Azure RBAC — grant the runner identity the Fabric Capacity Administrator
# custom role so it can read/update capacities at the policy scope
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "gh_runner_fabric_capacity" {
  scope              = var.policy_scope
  role_definition_id = azurerm_role_definition.fabric_capacity_admin.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.gh_runner.principal_id
}

# The managed identity also needs read access to list workspaces via the
# Fabric REST API. This is granted at the Fabric workspace level (not Azure
# RBAC). See the note below.

# ---------------------------------------------------------------------------
# Output — principal ID needed to add the identity to Fabric workspaces
# ---------------------------------------------------------------------------

output "gh_runner_identity_principal_id" {
  description = <<-EOT
    Object ID of the fabric-gh-runner managed identity. Use this to grant the
    runner Contributor access on each production Fabric workspace via the Fabric
    Admin API or portal:

      PATCH https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/roleAssignments
      {
        "role": "Contributor",
        "principal": {
          "id": "<this output value>",
          "type": "ServicePrincipal"
        }
      }
  EOT
  value       = azurerm_user_assigned_identity.gh_runner.principal_id
}

output "gh_runner_identity_client_id" {
  description = "Client ID of the fabric-gh-runner managed identity (used for DefaultAzureCredential when multiple identities are attached to the VM)."
  value       = azurerm_user_assigned_identity.gh_runner.client_id
}
