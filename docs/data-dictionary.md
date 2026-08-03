# Data Dictionary — `lh_fabric_management`

All tables live in the **`fabricmanagement`** schema of the `lh_fabric_management`
lakehouse (`Tables/fabricmanagement/<table>`), written as Delta by the notebooks in
[management-lakehouse.md](management-lakehouse.md).

**Temporal convention**

| Pattern | Tables | Extra columns |
|---|---|---|
| **SCD Type 2** (history via MERGE) | `gateways`, `connections` | `valid_from`, `valid_to`, `is_current`, `row_hash` |
| **Daily snapshot** (partitioned) | everything else | `snapshot_date` (partition), `scan_timestamp` |
| **Current overwrite** (latest only) | `report_model_map`, `gold_*` | `run_timestamp` |

SCD2 rows: `is_current = true` is the live version; a change closes the old row
(`valid_to` set, `is_current = false`) and inserts a new one. `row_hash` is the change
key. `whenNotMatchedBySourceUpdate` retires rows whose source key disappeared.

---

## Gateway pipeline (`gateway_inventory_to_lakehouse`)

### `gateways` — SCD2
One row per gateway cluster (current + history).

| Column | Notes |
|---|---|
| `gateway_id` | cluster id (business key) |
| `gateway_name` | |
| `gateway_type` | e.g. on-prem / VNet / personal |
| `member_count`, `admin_count` | counts of member gateways / admins |
| `primary_status`, `primary_version`, `primary_machine` | primary member gateway health |
| `contact_info` | |
| `vnet_subnet_id` | for VNet data gateways |
| `cloud_datasource_refresh`, `custom_connectors` | cluster feature flags |
| `source` | provenance tag (`gatewayclusters`) |
| `valid_from`, `valid_to`, `is_current`, `row_hash` | SCD2 control columns |

### `gateway_members` — snapshot
One row per member gateway per snapshot.

`gateway_id`, `gateway_name`, `member_id`, `member_name`, `status`, `state`, `version`,
`version_status`, `update_status`, `machine`, `department`, `contact_info`,
`vnet_subnet_id`, `expiry_date`, `scan_timestamp`, `snapshot_date`.

### `gateway_admins` — snapshot
One row per admin principal on a gateway.

`gateway_id`, `gateway_name`, `principal_id`, `principal_type`, `role`, `tenant_id`,
`scan_timestamp`, `snapshot_date`.

### `gateway_datasources` — snapshot
Data sources bound to each gateway.

`gateway_id`, `gateway_name`, `gateway_type`, `datasource_id`, `datasource_type`,
`datasource_server`, `datasource_database`, `connection_details`, `scan_timestamp`,
`snapshot_date`.

### `gateway_semantic_model_map` — snapshot
Gateway data source → the semantic model that uses it (from the scanner API).

`gateway_datasources` columns **+** `workspace_id`, `workspace_name`,
`semantic_model_id`, `semantic_model_name`, `is_misconfigured`, `scan_timestamp`,
`snapshot_date`. `is_misconfigured` flags a model bound to a data source the gateway
can't actually serve.

---

## Connection pipeline (`connection_inventory_to_lakehouse`)

### `connections` — SCD2
One row per connection (cloud + on-prem). `coverage = "caller-scoped"` because there is
no tenant-wide connection API.

| Column | Notes |
|---|---|
| `connection_id` | business key |
| `display_name` | |
| `connectivity_type` | `ShareableCloud`, `OnPremisesGateway`, … |
| `gateway_id` | null for cloud connections |
| `datasource_type` | connector type |
| `path` | data-source path/connection string (input to the risk classifier) |
| `privacy_level` | |
| `credential_type`, `single_sign_on_type`, `connection_encryption` | credential config |
| `skip_test_connection` | |
| `allow_usage_in_gateway`, `allow_usage_in_user_code` | sharing flags |
| `created_datetime` | |
| `coverage` | `caller-scoped` provenance note |
| `source_kind`, `source_host`, `risk_tier` | from `classify_path()` |
| `valid_from`, `valid_to`, `is_current`, `row_hash` | SCD2 control columns |

### `connection_activity` — snapshot
`connection_id`, `created_datetime`, `last_bound_datetime`,
`last_credential_used_datetime`, `scan_timestamp`, `snapshot_date`.

### `connection_semantic_model_map` — snapshot
Connection/data source → semantic model. **This is the join input for the gold marts.**

`datasource_type`, `path`, `source_kind`, `source_host`, `risk_tier`, `gateway_id`,
`connection_id`, `connection_name`, `connectivity_type`, `match_method`, `workspace_id`,
`workspace_name`, `semantic_model_id`, `semantic_model_name`, `scan_timestamp`,
`snapshot_date`. `match_method` records how the model↔source link was resolved (scanner
vs item-connections).

### `connection_item_map` — snapshot
Per-item usage from **List Item Connections** (all governed item types).

`connection_id`, `connection_name`, `connectivity_type`, `path`, `source_kind`,
`source_host`, `risk_tier`, `workspace_id`, `workspace_name`, `item_id`, `item_name`,
`item_type`, `scan_timestamp`, `snapshot_date`.

---

## Gold marts (`gold_governance_to_lakehouse`)

### `report_model_map` — current overwrite
Report → semantic model lineage from `/admin/reports`.

`report_id`, `report_name`, `report_type`, `dataset_id`, `workspace_id`,
`workspace_name`, `created_by`, `modified_date`, `web_url`, `scan_timestamp`.

### `gold_file_dependencies` — current overwrite
One row per data source, with dependent counts. Answers "which files have the most
connections".

| Column | Notes |
|---|---|
| `path` | data-source path (grain) |
| `source_kind`, `source_host`, `risk_tier` | re-classified in cell 2b |
| `datasource_type` | |
| `file_extension` | parsed from `path` (`.xlsx`, `.csv`, …) |
| `models_using` | distinct semantic models on this source |
| `workspaces_using` | distinct workspaces |
| `reports_using` | distinct reports (via report → model → source) |
| `run_timestamp` | |

### `gold_report_risk` — current overwrite
One row per report with a risk roll-up. Answers "top-10 riskiest reports".

| Column | Notes |
|---|---|
| `report_id`, `report_name`, `report_type` | |
| `workspace_id`, `workspace_name` | the **report's** workspace |
| `dataset_id`, `semantic_model_name` | its model |
| `n_sources` | distinct data sources behind the model |
| `high_sources`, `medium_sources`, `low_sources` | distinct sources per risk tier |
| `risk_score` | `3×High + 2×Medium + 1×Low` |
| `riskiest_kind`, `riskiest_host`, `riskiest_source`, `riskiest_tier` | the single worst source |
| `run_timestamp` | |

---

## Performance mart (`heavy_interactive_workloads_to_lakehouse`)

### `gold_heavy_interactive_items` — current overwrite
One row per (capacity × item), interactive-vs-background CU split over `window_days`.

| Column | Notes |
|---|---|
| `capacity_id`, `capacity_name` | |
| `workspace` | item's workspace name |
| `item_id`, `item_name`, `artifact_kind` | `artifact_kind` = Report / Dataset / Model / PaginatedReport / Datamart / … |
| `total_cu_s` | total CU-seconds |
| `interactive_cu_s` | CU from `INTERACTIVE_OPS` operations |
| `background_cu_s` | remainder (refresh, notebook runs, data movement) |
| `interactive_ops`, `total_ops` | operation counts |
| `top_operation` | highest-CU operation name for the item |
| `window_days` | lookback window (default 14) |
| `run_timestamp` | |

Derived measures (in the `Interactive_Workloads` model): `% Interactive =
interactive_cu_s / total_cu_s`. A low value means an item is refresh-heavy rather than
query-heavy — a different remediation than warehouse migration.
