Here's the comparison table:

| Layer | Azure Hierarchy | Fabric Hierarchy | Key Difference |
|-------|----------------|-----------------|----------------|
| **Top-level grouping** | Management Groups | Fabric Tenant (Admin Portal) | Azure allows nested management group trees; Fabric has a single flat tenant boundary |
| **Logical boundary** | Subscriptions | Domains | Subscriptions enforce billing and access isolation; Domains are purely organizational tags on workspaces — no billing or hard security boundary |
| **Infrastructure unit** | Resource Groups | Capacities | Resource groups are free logical containers; Capacities are paid SKUs (F2–F2048) that govern compute/memory — moving a workspace between capacities changes its performance tier |
| **Organizational container** | *(no direct equivalent)* | Workspaces | Azure resources sit directly in resource groups; Fabric adds an extra container layer where RBAC, Git integration, and deployment pipelines attach |
| **Resources / Items** | Resources (VMs, Storage, DBs…) | Items (Lakehouses, Warehouses, Pipelines, Reports…) | Broadly equivalent — the things you actually build and use |
| **Policy enforcement** | Azure Policy (deny, audit, remediate) | **None** | Azure can block non-compliant resource creation at the ARM layer; Fabric has no policy engine — naming, configuration, and governance require manual discipline or API-based audits |
| **Access control** | RBAC (scoped to mgmt group, sub, RG, or resource) | Workspace roles (Admin, Member, Contributor, Viewer) | Azure RBAC is granular and hierarchical with inheritance; Fabric roles are flat and scoped only to the workspace level — no inheritance from domains or capacities |
| **Tagging / Metadata** | Resource tags (key-value, policy-enforceable) | Descriptions and endorsements (Promoted, Certified) | Azure tags are structured and can trigger policy rules; Fabric metadata is freeform text with no automated enforcement |
| **Cross-region** | Resources deploy to any region per resource group | Capacity is region-locked; domains span regions | In Azure each resource picks its region; in Fabric the capacity pins all its workspaces to one region, but a domain can group workspaces across capacities in different regions |

The biggest takeaway for your audience: Azure gives you guardrails (Policy, hierarchical RBAC, enforceable tags) that Fabric simply doesn't have. That's why the naming standard, the cleanup script, and workspace-creation gating matter so much — discipline is the only policy engine you've got.

Want me to add this into the naming standards markdown, or keep it separate?