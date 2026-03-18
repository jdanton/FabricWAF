# Reference Architecture

**Fabric Capacities segmented by Business Unit · Function · Environment**

---

## Identity Plane

**Microsoft Entra ID** — Conditional Access · PIM · Service Principals

---

## Capacities

### Sales · Analytics · Prod — F64 · East US

| Workspace | Items |
|-----------|-------|
| **Sales DW** | Lakehouse · Warehouse · Pipeline · Semantic Model |
| **Sales Reports** | Semantic Model · Report · Report · Dashboard |

**Tags:** `Sales` · `SALES-001` · `Prod` · `Confidential`
> BU · Cost Center · Env · Data Classification

---

### Finance · ETL · Prod — F32 · East US

| Workspace | Items |
|-----------|-------|
| **Finance Ingest** | Lakehouse · Pipeline · Notebook · Dataflow |
| **Finance DW** | Warehouse · Semantic Model · Report |

**Tags:** `Finance` · `FIN-042` · `Prod` · `Restricted`
> BU · Cost Center · Env · Data Classification

> **Sizing note:** Right-size per workload — F2 for Dev sandboxes → F64+ for production analytics

---

### Marketing · Reporting · Dev — F2 · East US

| Workspace | Items |
|-----------|-------|
| **Mkt Dev** | Lakehouse · Notebook · Report |
| **Mkt Sandbox** | Lakehouse · Semantic Model · Notebook |

**Tags:** `Marketing` · `MKT-017` · `Dev` · `Internal`
> BU · Cost Center · Env · Data Classification

---

## Private Networking

| Component | Direction | Connects To |
|-----------|-----------|-------------|
| **Private Link** | Inbound to Fabric endpoints | Corporate users |
| **Managed Private Endpoints** | Outbound to Azure PaaS services | Azure SQL · ADLS · Key Vault |
| **VNet Data Gateway** | On-prem & IaaS sources | SQL Server · Oracle · SAP |

---

## Governance & Monitoring

| Pillar | Detail |
|--------|--------|
| **Naming Standard** | `{BU}-{Fn}-{Env}-{Region}` |
| **Tagging** | Owner · Cost Center · Env · Data Classification |
| **API Automation** | Enforce via CI/CD & scripts |
| **Audit → Sentinel** | Activity logs to Log Analytics |

> ⚠ **Gaps today:** No Azure Policy · Coarse RBAC (Admin / Member / Contributor / Viewer) · Limited audit events

---

## Storage Layer

**OneLake** — Unified storage · Delta / Parquet · Shortcuts · V-Order optimized

---

## Git Integration & CI/CD

| | |
|-|-|
| Workspace → Git sync | Pipeline definitions & models in source control |

---

## Legend

| Color | Item Type |
|-------|-----------|
| Blue | Lakehouse |
| Red | Warehouse |
| Green | Pipeline / Notebook |
| Yellow | Semantic Model |
| Gray | Report / Dashboard |
