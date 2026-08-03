# Management Lakehouse

Six PySpark notebooks (`notebooks/`) build a tenant-wide governance & observability
dataset in the **`lh_fabric_management`** lakehouse and serve it through Direct Lake
semantic models. They run inside Fabric under a **Fabric administrator** identity.

| # | Notebook | Writes / builds | Primary source |
|---|---|---|---|
| 1 | `gateway_inventory_to_lakehouse.ipynb` | gateway inventory tables | `/v2.0/myorg/gatewayclusters` + scanner |
| 2 | `connection_inventory_to_lakehouse.ipynb` | connection inventory tables | `/v1/connections` + scanner + item connections |
| 3 | `gold_governance_to_lakehouse.ipynb` | `gold_file_dependencies`, `gold_report_risk` | `/admin/reports` + connection map |
| 4 | `heavy_interactive_workloads_to_lakehouse.ipynb` | `gold_heavy_interactive_items` | Fabric Capacity Metrics model (DAX) |
| 5 | `build_semantic_model.ipynb` | `Fabric_Governance` Direct Lake model + measures | gold tables (1–3) |
| 6 | `build_interactive_workload_model.ipynb` | `Interactive_Workloads` Direct Lake model + measures | gold table (4) |

Plus `add_file_extension_column.ipynb` — a one-off that adds the `file_extension`
column to the `Fabric_Governance` model (Direct Lake does not auto-add new source
columns).

> **Notebooks are generated.** Each `.ipynb` is produced by a `gen_*.py` script kept in
> the session scratchpad, not hand-edited. To change a notebook, edit its generator and
> re-run it, then re-publish. See [Regenerating & publishing](#regenerating--publishing).

---

## The lakehouse & write pattern

- **Lakehouse:** `lh_fabric_management`, schema **`fabricmanagement`** (schema-enabled
  lakehouse → tables live at `Tables/fabricmanagement/<table>`).
- **Path-based writes.** Notebooks resolve the lakehouse's OneLake path and write by
  absolute URI, so **no default lakehouse needs to be attached** (this avoids the
  `No default context found` error from `saveAsTable` with a partial namespace):

  ```python
  lh = notebookutils.lakehouse.get("lh_fabric_management")
  base = lh["properties"]["oneLakeTablesPath"]        # abfss://…/Tables
  def _table_uri(name): return f"{base}/{SCHEMA}/{name}"
  df.write.format("delta").mode("overwrite").save(_table_uri("gateways"))
  ```

- **Identity / token.** One bearer token works for both `api.powerbi.com` and
  `api.fabric.microsoft.com`:

  ```python
  token = notebookutils.credentials.getToken("pbi")
  ```

---

## 1 — Gateway inventory

`gateway_inventory_to_lakehouse.ipynb` — every gateway cluster in the tenant and what
it is bound to.

**Key discovery.** The documented `/v1/gateways` endpoint is *membership-scoped* — a
tenant admin who is not a member of a gateway sees **0** rows. The tenant-wide list
comes from the **undocumented** admin endpoint:

```
GET https://api.powerbi.com/v2.0/myorg/gatewayclusters?$expand=memberGateways,permissions
```

This requires "tenant administration" mode (the *Manage connections and gateways* →
*Tenant administration* toggle in Fabric) and returns all clusters (VNet + on-prem).

Data-source → workspace/semantic-model bindings come from the **metadata scanner API**
(`POST /admin/workspaces/getInfo` → poll `scanStatus` → `GET .../scanResult`), whose
`datasourceInstances[].gatewayId` links a data source back to its gateway.

**Tables:** `gateways` (SCD2), `gateway_members`, `gateway_admins`,
`gateway_datasources`, `gateway_semantic_model_map`.

---

## 2 — Connection inventory

`connection_inventory_to_lakehouse.ipynb` — the same idea for **connections**
(cloud + on-prem), with report→model lineage and per-item usage.

- **Connections:** `/v1/connections` — permission-scoped **even for an admin** (there is
  no tenant-wide connection API; `/v2.0/myorg/gatewayClusterDatasources` returns
  **HTTP 501**). The table is therefore tagged `coverage = "caller-scoped"`.
- **Per-item usage:** for each governed item type it calls **List Item Connections**
  (`/v1/workspaces/{ws}/items/{id}/connections`, needs `Item.ReadWrite.All` — no admin
  variant), fanned out with a `ThreadPoolExecutor` (`MAX_WORKERS = 8`) and a cached
  token. Item types scanned:

  ```python
  ITEM_TYPES = ["SemanticModel", "Dataflow", "Datamart", "DataPipeline",
                "Eventstream", "CopyJob", "MirroredDatabase"]
  ```

**Data-source risk classifier** (`classify_path()`), the same idea reused in gold:

| Bucket | Example | `risk_tier` |
|---|---|---|
| `LocalMappedDrive` | `D:\...`, `Z:\share\file.xlsx` | **High** |
| `FileShareUNC` | `\\server\share\...` | **Medium** |
| `SqlServer` | on-prem SQL host | **Medium** |
| `SharePoint` / `SharePointPersonal` | SPO / OneDrive | Low/Medium |
| `AzureSQL`, `FabricWarehouse`, `FabricOneLake`, `PowerBIDataset` | cloud-native | **Low** |

**Tables:** `connections` (SCD2), `connection_activity`,
`connection_semantic_model_map`, `connection_item_map`.

---

## 3 — Gold governance marts

`gold_governance_to_lakehouse.ipynb` — turns the connection map into two decision-ready
marts.

- Pulls `/admin/reports` (tenant-wide report → `datasetId` lineage) into an internal
  `report_model_map`, joins it to `connection_semantic_model_map` from pipeline 2.
- **`gold_file_dependencies`** — "which files/data sources have the most connections",
  with the reclassified source bucket + `file_extension`.
- **`gold_report_risk`** — per-report risk roll-up. Score:

  ```
  risk_score = 3 × High + 2 × Medium + 1 × Low   (count of data sources in each tier)
  ```

  Drives the "top-10 riskiest reports" view.

**Path reclassification (cell 2b).** A UDF normalises Windows back-slashes before
bucketing (`BS = chr(92); norm = low.replace(BS, "/")`) and extracts `file_extension`,
collapsing the `Unknown`/`Other` buckets. Extra buckets added here: `SharePoint`,
`SharePointPersonal`, `FabricWarehouse`, `AzureSQL`, `FabricOneLake`, `PowerBIDataset`,
`SqlServer`, `Service`, `File`, `Web`, `Database`.

> **Gotcha seen in practice:** after re-running gold, the *model data* was correct
> (DAX confirmed `Unknown` dropped 1602 → 1) but the **report still showed stale
> buckets** — a Copilot/quick-summary **report cache**, not a data problem. Refresh the
> model and rebuild the visual.

---

## 4 — Heavy interactive workloads (performance)

`heavy_interactive_workloads_to_lakehouse.ipynb` (Fabric folder **`Performance`**) —
ranks reports & semantic models by **interactive** CU so you can prioritise what to move
into a warehouse.

- Queries the **Fabric Capacity Metrics** app's semantic model with Power BI
  `executeQueries` (DAX) against `Metrics By Item And Operation`.
- Its fact tables are **DirectQuery-gated**, so the notebook first binds parameters via
  `Default.UpdateParameters` (`DefaultCapacityID`, `StartDate`, `EndDate`) — this needs
  **write/owner** on the metrics model.
- **Interactive is classified by operation name**, *not* by "Billing type" (which is
  `Billable`, not `Interactive` — an early version returned 0 items because of this).
  A set of interactive operation names (`INTERACTIVE_OPS`) splits interactive vs
  background CU.
- Item names are resolved with
  `MAXX(FILTER(ALL('Items'), 'Items'[Item Id] = …[Item Id]), 'Items'[Item name])`
  — **not** `LOOKUPVALUE`, because `Items` has multiple (daily) rows per Item Id.

**Table:** `gold_heavy_interactive_items` (one row per item: interactive/background/total
CU, `interactive_ops`, `top_operation`, `artifact_kind`, workspace, capacity).

---

## 5 & 6 — Direct Lake serving models

Both use **semantic-link-labs** (`sempy_labs`). The critical call signature (it drifted
across versions — `tables` not `lakehouse_tables`, and `source`/`source_type` are
required):

```python
from sempy_labs.directlake import generate_direct_lake_semantic_model
TABLES = {"gold_file_dependencies": f"{SCHEMA}.gold_file_dependencies", ...}  # schema-qualified
generate_direct_lake_semantic_model(
    dataset=MODEL, tables=TABLES, source=LH, source_type="Lakehouse",
    overwrite=True, refresh=True,
)
```

Measures are added over a TOM connection (`connect_semantic_model(..., readonly=False)` →
`tom.add_measure(...)`); list them with `tom.all_measures()` (not `labs.list_measures`).

- **`build_semantic_model.ipynb`** → `Fabric_Governance` over the governance gold tables.
- **`build_interactive_workload_model.ipynb`** → `Interactive_Workloads` with measures
  `Interactive CU`, `Background CU`, `Total CU`, `% Interactive`, `Interactive
  Operations`, `Items`.

**Reports are built in the Fabric UI, not generated.** `create_report_from_reportjson`
was tried and abandoned — the generated report JSON is version-sensitive and hangs on
"loading your report". Each model notebook ends with markdown instructions for building
the report by hand (Auto-create report → the listed fields).

**Pip note.** Install order matters — `semantic-link-labs` pins an older PyJWT; install
`"PyJWT>=2.6.0"` after it in the same cell:

```python
%pip install -q semantic-link-labs "PyJWT>=2.6.0"
```

---

## Temporal model (SCD2 + snapshots)

- **Dimensions** (`gateways`, `connections`) are **SCD Type 2**: a Delta `MERGE` closes
  the previous version (`valid_to`, `is_current = false`) and inserts the new one, using
  `whenNotMatchedBySourceUpdate` to retire rows that vanished from the source.
- **Bridges / usage** tables are **daily snapshots** partitioned by `snapshot_date`
  (`partitionBy("snapshot_date")` + `replaceWhere` for idempotent re-runs).

If a pre-SCD2 table exists from an earlier run, the MERGE fails with
`DELTA_MERGE_UNRESOLVED_EXPRESSION (is_current)` — drop it first:
`notebookutils.fs.rm(_table_uri(t), True)` then re-run.

Column-level schemas are in **[data-dictionary.md](data-dictionary.md)**.

---

## Regenerating & publishing

**Regenerate** a notebook by editing its `gen_*.py` generator and re-running it
(`python gen_gold_nb.py` → writes the `.ipynb`).

**Publish** with `scripts/push_notebook.sh` (uses `az rest` + the Fabric Items API):

```bash
scripts/push_notebook.sh <workspace-id> <path.ipynb> [display-name] [folder]
```

- Requires an `az login` with the Fabric scope
  (`az login --scope "https://api.fabric.microsoft.com/.default"`).
- Resolves `[folder]` to a `folderId` via `/v1/workspaces/{ws}/folders` and places the
  item there on create.
- **Create-or-update by display name.** Updating an existing item pushes new content but
  **does not move it** between folders — delete/recreate to relocate.

Publish the notebooks into the workspace that hosts `lh_fabric_management` (an F-SKU
capacity), placing governance notebooks in a `governance` folder and performance
notebooks in a `Performance` folder.

## Key API discoveries (quick reference)

| Need | Endpoint | Gotcha |
|---|---|---|
| All gateways tenant-wide | `GET /v2.0/myorg/gatewayclusters?$expand=…` | Undocumented; `/v1/gateways` is membership-scoped (returns 0 for a non-member admin) |
| Data source → workspace/model | Metadata scanner `POST /admin/workspaces/getInfo` | Async: poll `scanStatus` → `scanResult` |
| Connections | `GET /v1/connections` | Permission-scoped even for admin; no tenant-wide API |
| Cloud connection datasources | `/v2.0/myorg/gatewayClusterDatasources` | **HTTP 501** — not available |
| Item connections | `GET /v1/workspaces/{ws}/items/{id}/connections` | Needs `Item.ReadWrite.All`; no admin variant |
| Report → dataset lineage | `GET /admin/reports` | Tenant-wide |
| Capacity CU by item/op | Capacity Metrics model via `executeQueries` | Facts DirectQuery-gated — bind params with `Default.UpdateParameters` first |
