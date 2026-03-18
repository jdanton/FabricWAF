# ---------------------------------------------------------------------------
# Fabric Capacity
# ---------------------------------------------------------------------------

variable "resource_group_name" {
  description = "Name of the resource group where the Fabric capacity will be created."
  type        = string
}

variable "location" {
  description = "Azure region for the Fabric capacity. Must be a US region (e.g. eastus, westus2)."
  type        = string
  default     = "eastus"

  validation {
    condition = contains([
      "eastus", "eastus2", "westus", "westus2", "westus3",
      "centralus", "northcentralus", "southcentralus", "westcentralus"
    ], var.location)
    error_message = "Location must be a US Azure region."
  }
}

# Capacity name must follow the pattern: {BU}-{Function}-{Env}-{Region}
#   BU       : fin | mktg | hr | eng | sales | ops
#   Function : dw | analytics | ingest | ml | report
#   Env      : dev | tst | stg | prod
#   Region   : eus | eus2 | wus | wus2 | wus3 | cus | ncus | scus | wcus
# Example   : fin-dw-prod-eus
variable "capacity_name" {
  description = "Name for the Fabric capacity. Must match pattern {BU}-{Function}-{Env}-{Region} (see naming-standard.md)."
  type        = string

  validation {
    condition = can(regex(
      "^(fin|mktg|hr|eng|sales|ops)-(dw|analytics|ingest|ml|report)-(dev|tst|stg|prod)-(eus|eus2|wus|wus2|wus3|cus|ncus|scus|wcus)$",
      var.capacity_name
    ))
    error_message = "capacity_name must follow pattern {BU}-{Function}-{Env}-{Region}, e.g. fin-dw-prod-eus. See naming-standard.md for valid token values."
  }
}

variable "sku_name" {
  description = "Fabric capacity SKU name (e.g. F2, F4, F8, F16, F32, F64, F128, F256, F512, F1024, F2048)."
  type        = string
  default     = "F2"

  validation {
    condition     = can(regex("^F(2|4|8|16|32|64|128|256|512|1024|2048)$", var.sku_name))
    error_message = "sku_name must be one of: F2, F4, F8, F16, F32, F64, F128, F256, F512, F1024, F2048."
  }
}

variable "administration_members" {
  description = "List of UPNs or service principal object IDs that will administer the Fabric capacity."
  type        = list(string)
}

# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

variable "cost_center" {
  description = "Cost center code for billing attribution."
  type        = string
}

variable "created_by" {
  description = "Identity (UPN or service principal) that provisioned the capacity."
  type        = string
}

variable "created_date" {
  description = "ISO 8601 date the capacity was created (YYYY-MM-DD)."
  type        = string
  default     = "2026-03-18"

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.created_date))
    error_message = "created_date must be in YYYY-MM-DD format."
  }
}

variable "additional_tags" {
  description = "Optional extra tags to merge with the required tags."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

variable "policy_scope" {
  description = "ARM resource ID of the scope at which to assign the policies (subscription or management group)."
  type        = string
}
