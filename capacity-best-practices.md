# Fabric Capacity — Configuration Best Practices

This document defines the target configuration state for every Microsoft Fabric
capacity managed by this repository. The `configure_capacity.py` script reads
these standards and applies them automatically as part of the GitHub Actions
deployment pipeline.

---

## 1. Administration

| Setting | Required value | Why |
|---------|---------------|-----|
| `administrationMembers` | Exactly one entry — the `Fabric-Capacity-Admins` Entra group object ID | Prevents ad-hoc individuals from holding the capacity admin role. Mirrors Policy 3 in `terraform/policy.tf`. |

Individual user accounts must never appear in `administrationMembers`. The
`Fabric-Capacity-Admins` group provides a single, auditable control point for
all capacity lifecycle operations (pause, resume, scale, delete).

---

## 2. Workload Memory Allocation

Workloads share the capacity's total memory. Allocating a maximum percentage per
workload prevents a single workload from monopolising the capacity during a surge.

The values below are the **maximum percentage of capacity memory** each workload
may use. They are intentionally conservative — the actual ceiling can be raised
in `configure_capacity.py` without changing this document's intent.

| Workload | Max memory % | Rationale |
|----------|-------------|-----------|
| `SemanticModel` | 40 % | Core BI query workload; needs headroom but must not crowd out pipelines |
| `Dataflow` | 40 % | ETL workload; burst-heavy but short-lived |
| `PaginatedReport` | 20 % | Low baseline but can spike during scheduled runs |

> **Note:** Percentages represent the ceiling, not a reservation. Unused
> capacity is always available to other workloads. Total across all workloads
> may exceed 100 % — Fabric enforces each workload's ceiling independently.

---

## 3. Semantic Model Query Timeout

| Setting | Required value | Why |
|---------|---------------|-----|
| `QueryTimeout` | 600 seconds | Prevents runaway DAX queries from consuming all available CUs. Long-running queries should be fixed in the model, not given unlimited runtime. |

Queries that exceed 600 s are cancelled and return an error to the client. If
legitimate queries need longer, they should be broken into smaller operations or
moved to a DirectLake model.

---

## 4. Capacity Overload Notifications

| Setting | Required value | Why |
|---------|---------------|-----|
| Overload notifications | Enabled | Alerts capacity admins by email when the capacity is throttled, allowing proactive scaling before users are impacted. |

Notifications fire when the capacity is consistently at or above 100 % CU
utilisation. They are sent to all members of the `Fabric-Capacity-Admins` group.

---

## 5. Workspace Assignment Permissions

| Setting | Required value | Why |
|---------|---------------|-----|
| Who can assign workspaces | `SpecificUsersAndGroups` — `Fabric-Capacity-Admins` only | Prevents teams from routing workloads to a capacity without approval, which could degrade performance for other workspaces sharing the same capacity. |

Workspace assignment is a privileged operation. Limiting it to the
`Fabric-Capacity-Admins` group ensures capacity planning remains centralised.

---

## 6. Autoscale

Autoscale is supported on F64 and larger SKUs. For eligible capacities:

| Setting | Required value | Why |
|---------|---------------|-----|
| Autoscale | Enabled | Prevents hard throttling during legitimate demand spikes without requiring manual intervention. |
| Max autoscale CUs | 25 % above base SKU | Bounds unexpected cost growth while providing burst headroom. |

Autoscale is silently skipped on SKUs below F64 — the script checks the SKU
before attempting to enable it.

---

## Applying the Configuration

The `scripts/configure_capacity.py` script applies all settings above via the
Power BI Admin API and the Fabric REST API. It runs automatically as the
**Configure Capacity** job in the GitHub Actions deployment pipeline after a
successful deploy.

To apply manually:

```bash
pip install azure-identity requests

# Apply best-practice configuration to a specific capacity
FABRIC_CAPACITY_ID=<capacity-guid> python scripts/configure_capacity.py

# Dry run — print what would change without applying
FABRIC_CAPACITY_ID=<capacity-guid> python scripts/configure_capacity.py --dry-run
```

The capacity GUID is available as a Terraform output:

```bash
terraform -chdir=terraform output -raw fabric_capacity_id
```

---

## Deviating from These Standards

Any deviation from the values above requires a pull request that:

1. Updates this document with the new target value and a documented reason
2. Updates the corresponding constant in `scripts/configure_capacity.py`
3. Is approved by a member of `Fabric-Capacity-Admins`

Ad-hoc changes made through the Fabric portal or Power BI Admin portal will be
overwritten on the next deployment run.
