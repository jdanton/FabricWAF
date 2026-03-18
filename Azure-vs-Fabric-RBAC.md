# RBAC Deep Dive: Azure vs Microsoft Fabric

## The Fundamental Difference

Azure RBAC is **hierarchical, granular, and policy-enforceable**. Fabric RBAC is **flat, workspace-scoped, and evolving** — with OneLake security trying to close the gap at the data layer.

---

## 1. Scope & Inheritance

| Dimension | Azure | Fabric |
|-----------|-------|--------|
| Scope hierarchy | Management Group → Subscription → Resource Group → Resource | Tenant → Domain → Capacity → Workspace → Item |
| Permission inheritance | Full downward inheritance — assign Reader at subscription, it flows to every RG and resource below | **No inheritance.** Workspace roles don't cascade from domains or capacities. Each workspace is an island. |
| Cross-scope assignment | One role assignment can cover thousands of resources via scope | Must be repeated per workspace. A user who needs Viewer on 50 workspaces gets 50 separate role assignments. |
| Deny assignments | Supported — explicit deny overrides allow | Not supported. No way to say "this user cannot access this workspace" if a broader group grants it. |

**The gap:** If you reorganize domains or move workspaces between capacities, permissions don't follow. Every workspace must be re-evaluated independently. There's no "assign at domain level and inherit down" pattern.

---

## 2. Role Granularity

### Azure: 100+ built-in roles, unlimited custom roles

Azure ships with highly specific roles (e.g., "Storage Blob Data Reader", "Cosmos DB Account Reader", "Key Vault Secrets Officer") and lets you create custom roles scoped to exact action sets. You can say "this user can read blobs in this storage account but nothing else."

### Fabric: 4 workspace roles, that's it

| Role | Can manage workspace | Can create/edit items | Can view items | Can share |
|------|---------------------|----------------------|----------------|-----------|
| Admin | Yes | Yes | Yes | Yes |
| Member | Limited | Yes | Yes | Yes |
| Contributor | No | Yes | Yes | If granted |
| Viewer | No | No | Yes | If granted |

There are no custom workspace roles. You cannot create a role like "Pipeline Operator" (can run pipelines but not edit them) or "Report Publisher" (can publish reports but not touch lakehouses). The four roles apply uniformly to **all item types** in the workspace.

**The gap:** The Contributor role grants full create/read/update/delete on every item type — lakehouses, warehouses, pipelines, reports, notebooks. You cannot scope a Contributor to only pipelines. If someone needs to edit a pipeline, they can also edit your lakehouse tables. The only workaround is splitting items across multiple workspaces, which creates its own management overhead.

---

## 3. Data-Level Security

### Azure: Native to each service

Each Azure data service has its own mature data security model — SQL Server has database roles, RLS, dynamic data masking. ADLS Gen2 has POSIX ACLs at the folder/file level. Cosmos DB has resource tokens. These are all GA and battle-tested.

### Fabric: OneLake Security (Preview)

OneLake security is the new unified data access control layer. It's promising but still in preview with significant limitations:

**What it can do:**
- Define roles that grant access to specific tables or folders within a lakehouse
- Row-level security (RLS) via SQL predicates
- Column-level security (CLS) by hiding columns
- Roles can be managed via UI or API

**What it can't do (yet):**

| Limitation | Impact |
|-----------|--------|
| RLS/CLS only works on Delta Parquet tables | Unstructured data in the Files/ section can't have row/column restrictions |
| RLS rules are static — no dynamic resolution from user attributes or lookup tables | Organizations with complex permission matrices (thousands of user-data combinations) would need thousands of static roles |
| RLS rules can't use JOINs to other tables | Can't do things like "show only rows where region = user's region" by joining to a user-region mapping table |
| RLS predicates max out at 1,000 characters | Complex multi-condition rules may not fit |
| Admin/Member/Contributor roles bypass OneLake security entirely | Anyone with write access to the workspace sees all data regardless of RLS/CLS — the security only constrains Viewers |
| RLS enforcement is engine-dependent | Supported in Spark notebooks and SQL analytics endpoint, but not all Fabric engines enforce it — potential data exposure through unsupported paths |
| CLS + RLS can't span multiple roles | If one role has RLS and another has CLS on the same table, queries fail. Both must be in a single role. |
| Distribution lists can't be resolved by the SQL endpoint | Users in distribution lists appear to have no role membership when accessing via SQL |
| Cross-region shortcuts don't work with OneLake security | Accessing shortcutted data across capacity regions returns 404 errors |

**The gap:** In Azure, you can set a POSIX ACL on a specific folder in ADLS Gen2 that says "this security group can read, but not write, and this applies to all current and future child files." In Fabric, the closest equivalent is OneLake security roles — but they bypass for anyone with Contributor or above, and they don't support dynamic user-attribute-based filtering that many enterprises rely on.

---

## 4. Conditional & Context-Aware Access

| Capability | Azure | Fabric |
|-----------|-------|--------|
| Conditional Access policies | Full support — require MFA, compliant device, specific location, risk level | Inherits Entra ID Conditional Access for sign-in, but no Fabric-specific conditions |
| Attribute-based access control (ABAC) | GA — use resource tags, user attributes, environment conditions in role assignments | Not available |
| Just-in-time access | Supported via PIM (Privileged Identity Management) — time-boxed elevation | Not natively supported. No "elevate to Workspace Admin for 2 hours" pattern. |
| Service principal RBAC | Full custom role support | Service principals can be workspace members, but workspace role limits still apply |

**The gap:** Azure ABAC lets you write conditions like "this user can read blobs tagged department=finance only if the user's department attribute is also finance." There is no equivalent attribute-based scoping in Fabric. PIM-style just-in-time elevation doesn't exist — once someone is a Workspace Admin, they stay an Admin until manually removed.

---

## 5. Audit & Compliance

| Capability | Azure | Fabric |
|-----------|-------|--------|
| Activity logs | Azure Activity Log captures every RBAC change with full context | Fabric audit logs capture workspace role changes, but with less granularity |
| Access reviews | Azure AD Access Reviews — scheduled, automated review of who has what | No built-in access review. Manual process only. |
| Compliance policies | Azure Policy can audit/deny non-compliant role assignments | No policy engine. Can't automatically flag or prevent over-permissioned roles. |
| Role assignment alerts | Azure Monitor can alert on sensitive role changes | Must build custom alerting via Fabric REST API + external tooling |

**The gap:** Azure Access Reviews let you say "every quarter, each workspace owner must review and re-confirm all Contributor+ assignments or they expire." Fabric has nothing like this. Over-permissioning tends to accumulate silently.

---

## 6. Multi-Tenancy & External Sharing

| Capability | Azure | Fabric |
|-----------|-------|--------|
| B2B guest access | Azure B2B with full RBAC scoping — guests get only what's explicitly assigned | Fabric supports B2B guests, but OneLake security requires the most inclusive Entra external collaboration setting to work |
| Cross-tenant | Azure Lighthouse for managed service provider scenarios | Limited cross-tenant support; external data sharing preview conflicts with OneLake security |

**The gap:** In Azure, you can invite a guest user and scope them to a single resource group with a single read-only role. In Fabric, B2B guest users with OneLake security roles require your Entra tenant's external collaboration settings to be set to "Guest users have the same access as members" — the **most permissive** option — which may conflict with your organization's security posture.

---

## Summary: Where Fabric Falls Short

1. **No permission inheritance** — each workspace is a silo; no assign-once-flow-down model
2. **Only 4 roles, no custom roles** — can't separate "pipeline operator" from "lakehouse editor"
3. **Contributor+ bypasses data security** — OneLake RLS/CLS only constrains Viewers
4. **OneLake security gaps** — RLS is static, limited to Delta tables, engine-dependent
5. **No deny assignments** — can't explicitly block access granted by a group
6. **No policy enforcement** — can't auto-audit or prevent over-permissioned workspaces
7. **No built-in access reviews** — permission sprawl is invisible without custom tooling
8. **No ABAC or PIM** — no attribute-based conditions, no just-in-time elevation
9. **No dynamic RLS** — can't resolve user attributes at query time for row filtering
10. **B2B requires most-permissive tenant setting** — security trade-off for guest collaboration

---

## Practical Workarounds

Since you can't fix these with configuration alone, your governance model needs to compensate:

- **Workspace-per-security-boundary**: Split items that need different access into separate workspaces rather than trying to share one workspace with complex item-level permissions
- **Security groups over individuals**: Assign Entra security groups to workspace roles, not individual users — easier to audit and rotate
- **Least privilege by default**: Default to Viewer; elevate to Contributor only for specific needs with documented justification
- **Automated audits**: Use the Fabric Admin REST API to scan workspace memberships on a schedule and flag Contributor+ sprawl
- **Separate prod from dev**: Never give developers Contributor on production workspaces — use deployment pipelines to promote content instead
- **Document everything**: Since there's no policy engine to encode your rules, write them down and socialize them relentlessly

