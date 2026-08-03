# FabricWAF Documentation

Reference documentation for the FabricWAF project — governing Microsoft Fabric with
Terraform policy, compliance tooling, and a tenant-wide management lakehouse.

Start with the [architecture overview](architecture.md), then dive into the pillar that
matters:

| Doc | Covers |
|---|---|
| **[architecture.md](architecture.md)** | The three pillars, the end-to-end data flow, and a system diagram. Read this first. |
| **[infrastructure.md](infrastructure.md)** | Terraform: the Fabric capacity, the three Azure Policies + governance initiative, the custom RBAC role, the `fabric-gh-runner` identity, variables & outputs, naming + capacity best-practices. |
| **[governance-compliance.md](governance-compliance.md)** | The Python tooling (`audit_fabric`, `assess_tenant_settings`, `validate_fabric`, `deploy_fabric`, `configure_capacity`, `compare_tenant_settings`, `list_workspaces`), the tenant-settings catalog/profiles, and the GitHub Actions deployment gate. |
| **[management-lakehouse.md](management-lakehouse.md)** | The six inventory/performance notebooks, the key Fabric API discoveries, the risk classifier, the SCD2/snapshot temporal model, and `push_notebook.sh` publishing. |
| **[data-dictionary.md](data-dictionary.md)** | Column-level schemas for every `lh_fabric_management` table (gateways, connections, gold marts, heavy-workload mart). |
| **[operations.md](operations.md)** | Auth/identities, prerequisites, notebook run order, scheduling, and keeping private tenant outputs out of git. |

## The three pillars at a glance

1. **Provisioning & policy** — Terraform provisions Fabric capacities under Azure Policy
   (approved regions, compliant names, locked admin group) + a custom RBAC role.
   → [infrastructure.md](infrastructure.md)
2. **Compliance & CI/CD** — tenant-wide audit + tenant-settings assessment, and a
   validate-before-deploy GitHub Actions gate on a secret-less managed-identity runner.
   → [governance-compliance.md](governance-compliance.md)
3. **Management lakehouse** — notebooks inventory gateways, connections, data-source
   risk, and interactive CU into `lh_fabric_management`, served through Direct Lake
   models + reports. → [management-lakehouse.md](management-lakehouse.md) ·
   [data-dictionary.md](data-dictionary.md)

## Related reference material (repo root)

- [naming-standard.md](../naming-standard.md) — naming conventions for all Fabric resources.
- [capacity-best-practices.md](../capacity-best-practices.md) — capacity target state.
- [tenant-settings-best-practices.md](../tenant-settings-best-practices.md) — tenant-settings catalog + profiles.
- [reference-architecture.md](../reference-architecture.md) — architecture narrative.
- [Azure-vs-Fabric-RBAC.md](../Azure-vs-Fabric-RBAC.md), [azure-fabric-differences.md](../azure-fabric-differences.md) — platform differences.
- [fabric-private-network-limations.MD](../fabric-private-network-limations.MD) — private-networking constraints.
