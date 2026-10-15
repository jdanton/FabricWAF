# Microsoft Fabric Naming Standards

## Guiding Principles

1. **Establish a standard early** — `{BU}`, `{Function}`, and `{Env}` are the core tokens on every resource
2. **Region belongs to the capacity only** — the capacity is the only Fabric resource pinned to an Azure region. Workspaces inherit the region of the capacity they're assigned to, and items inherit it from the workspace. Reassigning a workspace to a capacity in another region would make a region-coded workspace or item name wrong. So `{Region}` appears **only** in capacity names.
3. **Apply consistently** across capacities, workspaces, lakehouses, warehouses, and pipelines
4. **Names are enforced by automation, not by Fabric** — Azure Policy enforces capacity names at the ARM layer. [Fabric policies](https://learn.microsoft.com/en-us/fabric/governance/fabric-policies-overview) (preview) can govern *who* creates *which item types* where, but they have no name condition. Workspace and item names are still enforced by the audit/validate scripts and the CI/CD gate.

---

## Convention Key

| Token | Description | Examples |
|-------|-------------|----------|
| `{BU}` | Business unit or department | `fin`, `mktg`, `hr`, `eng`, `sales`, `ops` |
| `{Function}` | Workload purpose or domain | `dw`, `analytics`, `ingest`, `ml`, `report` |
| `{Env}` | Environment tier | `dev`, `tst`, `stg`, `prod` |
| `{Region}` | Azure region short code — **capacity names only** | `eus`, `eus2`, `wus2`, `wus3`, `cus`, `scus` (US allow-list, see `terraform/policy.tf`) |
| `{Layer}` | Medallion layer | `raw`, `bronze`, `silver`, `gold` |
| `{Source}` | Source system identifier | `sap`, `sf` (Salesforce), `erp`, `crm`, `ga` |
| `{Domain}` | Business data domain | `customers`, `orders`, `products`, `employees` |
| `{Freq}` | Schedule frequency | `daily`, `hourly`, `weekly`, `adhoc` |
| `{Format}` | File format (where relevant) | `parquet`, `csv`, `delta`, `json` |

**General rules:**

- All lowercase, no spaces
- Hyphens (`-`) to separate tokens; underscores (`_`) within tokens when needed (e.g., `order_items`)
- Keep names short but meaningful — aim for readability over abbreviation
- Date stamps in ISO format when needed: `YYYYMMDD`

---

## Resource Naming Standards

### 1. Fabric Capacity

**Pattern:** `{BU}-{Function}-{Env}-{Region}`

| Example | Description |
|---------|-------------|
| `fin-dw-prod-eus` | Finance data warehouse, production, East US |
| `mktg-analytics-dev-eus2` | Marketing analytics, development, East US 2 |
| `eng-ml-stg-wus2` | Engineering ML workload, staging, West US 2 |

This is the **only** pattern that carries `{Region}`. Capacity names are enforced at the ARM layer by Azure Policy (`fabric-capacity-naming-standard` in `terraform/policy.tf`) and by the `capacity_name` variable validation.

---

### 2. Domain

**Pattern:** `{BU}` or `{BU}-{SubDomain}`

Domains are the top-level logical boundary for organizing workspaces by business area. A domain can span multiple capacities and regions — it is an organizational construct, not an infrastructure one. Define domains early so workspaces have a home from day one.

| Example | Description |
|---------|-------------|
| `finance` | Finance domain — all finance workspaces regardless of capacity |
| `sales` | Sales domain |
| `engineering` | Engineering domain |
| `engineering-platform` | Engineering platform sub-domain |
| `hr` | Human Resources domain |

---

### 3. Workspace

**Pattern:** `{BU}-{Function}-{Env}`

Workspaces are assigned to a domain and backed by a capacity. **No region code:** the capacity already carries the region, and a workspace can be reassigned to another capacity without being renamed.

| Example | Description |
|---------|-------------|
| `fin-dw-prod` | Finance data warehouse production workspace |
| `fin-dw-dev` | Finance data warehouse development workspace |
| `hr-analytics-prod` | HR analytics production workspace |
| `sales-reporting-tst` | Sales reporting test workspace |

---

### 4. Lakehouse

**Pattern:** `lh_{BU}_{Layer}_{Env}`

Use underscores because the lakehouse name becomes a SQL schema/catalog identifier.

| Example | Description |
|---------|-------------|
| `lh_fin_raw_prod` | Finance raw/landing zone, production |
| `lh_fin_bronze_prod` | Finance bronze layer, production |
| `lh_fin_silver_prod` | Finance silver (cleansed), production |
| `lh_fin_gold_prod` | Finance gold (curated), production |
| `lh_mktg_raw_dev` | Marketing raw data, development |

---

### 5. Warehouse

**Pattern:** `wh_{BU}_{Function}_{Env}`

| Example | Description |
|---------|-------------|
| `wh_fin_dw_prod` | Finance data warehouse, production |
| `wh_sales_reporting_prod` | Sales reporting warehouse, production |
| `wh_hr_analytics_dev` | HR analytics warehouse, development |

---

### 6. Data Pipeline

**Pattern:** `pl_{BU}_{Source}_to_{Layer}_{Freq}`

| Example | Description |
|---------|-------------|
| `pl_fin_sap_to_bronze_daily` | Finance: SAP ingest to bronze, daily |
| `pl_sales_sf_to_bronze_hourly` | Sales: Salesforce ingest to bronze, hourly |
| `pl_fin_bronze_to_silver_daily` | Finance: bronze-to-silver transformation, daily |
| `pl_mktg_silver_to_gold_daily` | Marketing: silver-to-gold curation, daily |
| `pl_hr_erp_to_raw_adhoc` | HR: ERP to raw landing, ad hoc |

---

### 7. Dataflow Gen2

**Pattern:** `df_{BU}_{Source}_{Domain}_{Layer}`

| Example | Description |
|---------|-------------|
| `df_fin_sap_gl_bronze` | Finance: SAP general ledger to bronze |
| `df_sales_crm_leads_silver` | Sales: CRM leads transform to silver |
| `df_hr_erp_employees_silver` | HR: ERP employee data to silver |

---

### 8. Notebook

**Pattern:** `nb_{BU}_{Function}_{Domain}`

| Example | Description |
|---------|-------------|
| `nb_fin_transform_gl_entries` | Finance: transform GL entries |
| `nb_mktg_feature_eng_campaigns` | Marketing: feature engineering for campaigns |
| `nb_eng_explore_telemetry` | Engineering: exploratory telemetry analysis |
| `nb_fin_dq_check_invoices` | Finance: data quality checks on invoices |

---

### 9. Spark Job Definition

**Pattern:** `sj_{BU}_{Function}_{Domain}_{Freq}`

| Example | Description |
|---------|-------------|
| `sj_fin_aggregate_gl_daily` | Finance: daily GL aggregation job |
| `sj_mktg_dedupe_contacts_weekly` | Marketing: weekly contact deduplication |

---

### 10. Semantic Model (Power BI Dataset)

**Pattern:** `sm_{BU}_{Domain}_{Env}`

| Example | Description |
|---------|-------------|
| `sm_fin_profitability_prod` | Finance profitability model, production |
| `sm_sales_pipeline_prod` | Sales pipeline model, production |
| `sm_hr_headcount_dev` | HR headcount model, development |

---

### 11. Power BI Report

**Pattern:** `rpt_{BU}_{Domain}_{Audience}`

| Example | Description |
|---------|-------------|
| `rpt_fin_monthly_close_exec` | Finance monthly close, executive audience |
| `rpt_sales_pipeline_ops` | Sales pipeline, operations audience |
| `rpt_hr_attrition_leadership` | HR attrition report, leadership audience |
| `rpt_mktg_campaign_roi_analysts` | Marketing campaign ROI, analyst audience |

---

### 12. Paginated Report

**Pattern:** `prpt_{BU}_{Domain}_{Description}`

| Example | Description |
|---------|-------------|
| `prpt_fin_invoice_detail` | Finance invoice detail paginated report |
| `prpt_hr_payroll_register` | HR payroll register |

---

### 13. KQL Database

**Pattern:** `kql_{BU}_{Domain}_{Env}`

| Example | Description |
|---------|-------------|
| `kql_eng_telemetry_prod` | Engineering telemetry KQL database, production |
| `kql_ops_monitoring_prod` | Operations monitoring, production |

---

### 14. KQL Queryset

**Pattern:** `kqs_{BU}_{Domain}_{Purpose}`

| Example | Description |
|---------|-------------|
| `kqs_eng_telemetry_anomalies` | Engineering: telemetry anomaly queries |
| `kqs_ops_latency_analysis` | Operations: latency analysis queryset |

---

### 15. Eventstream

**Pattern:** `es_{BU}_{Source}_{Domain}`

| Example | Description |
|---------|-------------|
| `es_eng_iot_sensor_readings` | Engineering: IoT sensor data stream |
| `es_sales_web_clickstream` | Sales: website clickstream events |
| `es_ops_applog_errors` | Operations: application error log stream |

---

### 16. ML Experiment

**Pattern:** `exp_{BU}_{Domain}_{Technique}`

| Example | Description |
|---------|-------------|
| `exp_fin_fraud_xgboost` | Finance: fraud detection using XGBoost |
| `exp_mktg_churn_logistic` | Marketing: churn prediction logistic regression |

---

### 17. ML Model

**Pattern:** `mdl_{BU}_{Domain}_{Version}`

| Example | Description |
|---------|-------------|
| `mdl_fin_fraud_v1` | Finance fraud model, version 1 |
| `mdl_mktg_churn_v3` | Marketing churn model, version 3 |

---

### 18. Lakehouse Table (Delta Table)

**Pattern:** `{Layer}_{Domain}_{Entity}`

Tables live inside a lakehouse, so the BU and env context is inherited.

| Example | Layer | Description |
|---------|-------|-------------|
| `raw_sap_gl_entries` | Raw | SAP GL entries as-landed |
| `bronze_sap_gl_entries` | Bronze | Cleansed GL entries with types applied |
| `silver_gl_journal` | Silver | Conformed journal entries |
| `gold_monthly_pnl` | Gold | Monthly P&L summary table |
| `gold_dim_customer` | Gold | Customer dimension |
| `gold_fact_sales` | Gold | Sales fact table |

---

### 19. Lakehouse Files / Folders

**Pattern:** `{Source}/{Domain}/{YYYY}/{MM}/{DD}/`

| Example | Description |
|---------|-------------|
| `sap/gl_entries/2025/03/18/` | SAP GL entries landed on 2025-03-18 |
| `sf/leads/2025/03/` | Salesforce leads for March 2025 |
| `adhoc/finance_recon/` | Ad hoc finance reconciliation files |

---

### 20. Shortcut

**Pattern:** `sc_{SourceLakehouse}_{Domain}`

| Example | Description |
|---------|-------------|
| `sc_lh_fin_raw_customers` | Shortcut to customer data in finance raw lakehouse |
| `sc_adls_ext_market_data` | Shortcut to external ADLS market data |

---

### 21. Data Activator Reflex

**Pattern:** `rx_{BU}_{Domain}_{Trigger}`

| Example | Description |
|---------|-------------|
| `rx_sales_pipeline_deal_alert` | Sales: alert when deal stage changes |
| `rx_ops_latency_threshold` | Ops: trigger on latency threshold breach |

---

### 22. Environment

**Pattern:** `env_{BU}_{Purpose}_{Env}`

| Example | Description |
|---------|-------------|
| `env_fin_spark_prod` | Finance Spark runtime config, production |
| `env_eng_ml_dev` | Engineering ML environment, development |

---

### 23. Policy Set (Fabric policies, preview)

**Pattern:** `ps_tenant_{Purpose}` (tenant scope) or `ps_cap_{BU}_{Env}_{Purpose}` (capacity scope)

A policy set's scope is fixed when it is created, so the scope goes in the name. Spaces aren't allowed. Capacity-scoped sets are named for the BU and environment they govern, **not** for the capacity: the same set can be activated on a replacement capacity in another region without a rename. Store every policy set in the governance workspace (`ops-governance-prod`).

| Example | Description |
|---------|-------------|
| `ps_tenant_baseline` | Tenant-wide workspace-settings and external-sharing rules |
| `ps_cap_fin_prod_itemcreation` | Item-creation allow list for finance production capacities |
| `ps_cap_eng_dev_itemcreation` | Item-creation rules for engineering dev capacities |

---

### 24. Variable Library

**Pattern:** `vl_{BU}_{Domain}`

A variable library holds per-stage values (connection IDs, lakehouse IDs, thresholds), so the name omits `{Env}`. The active value set selects the environment.

| Example | Description |
|---------|-------------|
| `vl_fin_gl` | Finance GL pipeline variables |
| `vl_eng_platform` | Engineering platform shared variables |

---

### 25. Copy Job

**Pattern:** `cj_{BU}_{Source}_to_{Layer}_{Freq}`

| Example | Description |
|---------|-------------|
| `cj_fin_sap_to_bronze_hourly` | Finance: incremental SAP copy to bronze, hourly |
| `cj_sales_crm_to_raw_daily` | Sales: CRM copy to raw, daily |

---

### 26. SQL Database

**Pattern:** `sqldb_{BU}_{Function}_{Env}`

| Example | Description |
|---------|-------------|
| `sqldb_fin_app_prod` | Finance operational app database, production |
| `sqldb_ops_metadata_dev` | Ops metadata store, development |

---

### 27. Mirrored Database

**Pattern:** `mir_{BU}_{Source}_{Env}`

| Example | Description |
|---------|-------------|
| `mir_fin_azuresql_prod` | Finance Azure SQL mirror, production |
| `mir_sales_snowflake_prod` | Sales Snowflake mirror, production |

---

### 28. Eventhouse

**Pattern:** `eh_{BU}_{Domain}_{Env}`

Creating an eventhouse also creates a default KQL database **with the same name**. Rename that database to the `kql_` pattern (section 13), or the audit will flag it.

| Example | Description |
|---------|-------------|
| `eh_eng_telemetry_prod` | Engineering telemetry eventhouse, production |

---

### 29. Real-Time Dashboard

**Pattern:** `rtd_{BU}_{Domain}_{Audience}` (API item type `KQLDashboard`)

| Example | Description |
|---------|-------------|
| `rtd_ops_latency_noc` | Ops latency dashboard for the NOC |

---

### 30. User Data Function

**Pattern:** `udf_{BU}_{Domain}_{Purpose}`

| Example | Description |
|---------|-------------|
| `udf_fin_gl_validate_journal` | Finance: journal-entry validation function |

---

### 31. Data Agent

**Pattern:** `da_{BU}_{Domain}_{Purpose}`

| Example | Description |
|---------|-------------|
| `da_sales_pipeline_qna` | Sales pipeline Q&A agent |

---

### 32. Apache Airflow Job

**Pattern:** `af_{BU}_{Function}_{Domain}`

| Example | Description |
|---------|-------------|
| `af_eng_orchestrate_telemetry` | Engineering telemetry orchestration DAGs |

---

### 33. dbt Job

**Pattern:** `dbt_{BU}_{Domain}_{Layer}` (API item type `DataBuildToolJob`)

| Example | Description |
|---------|-------------|
| `dbt_fin_gl_silver` | Finance GL models building the silver layer |

---

### 34. GraphQL API

**Pattern:** `gql_{BU}_{Domain}_{Env}`

| Example | Description |
|---------|-------------|
| `gql_sales_orders_prod` | Sales orders GraphQL API, production |

---

## Quick Reference Matrix

| Resource | Prefix | Pattern | Example |
|----------|--------|---------|---------|
| Capacity | — | `{BU}-{Function}-{Env}-{Region}` | `fin-dw-prod-eus` |
| Domain | — | `{BU}` or `{BU}-{SubDomain}` | `finance` |
| Workspace | — | `{BU}-{Function}-{Env}` | `fin-dw-prod` |
| Lakehouse | `lh_` | `lh_{BU}_{Layer}_{Env}` | `lh_fin_gold_prod` |
| Warehouse | `wh_` | `wh_{BU}_{Function}_{Env}` | `wh_fin_dw_prod` |
| Pipeline | `pl_` | `pl_{BU}_{Source}_to_{Layer}_{Freq}` | `pl_fin_sap_to_bronze_daily` |
| Dataflow Gen2 | `df_` | `df_{BU}_{Source}_{Domain}_{Layer}` | `df_fin_sap_gl_bronze` |
| Notebook | `nb_` | `nb_{BU}_{Function}_{Domain}` | `nb_fin_transform_gl_entries` |
| Spark Job | `sj_` | `sj_{BU}_{Function}_{Domain}_{Freq}` | `sj_fin_aggregate_gl_daily` |
| Semantic Model | `sm_` | `sm_{BU}_{Domain}_{Env}` | `sm_fin_profitability_prod` |
| Report | `rpt_` | `rpt_{BU}_{Domain}_{Audience}` | `rpt_fin_monthly_close_exec` |
| Paginated Report | `prpt_` | `prpt_{BU}_{Domain}_{Desc}` | `prpt_fin_invoice_detail` |
| KQL Database | `kql_` | `kql_{BU}_{Domain}_{Env}` | `kql_eng_telemetry_prod` |
| KQL Queryset | `kqs_` | `kqs_{BU}_{Domain}_{Purpose}` | `kqs_eng_telemetry_anomalies` |
| Eventstream | `es_` | `es_{BU}_{Source}_{Domain}` | `es_eng_iot_sensor_readings` |
| ML Experiment | `exp_` | `exp_{BU}_{Domain}_{Technique}` | `exp_fin_fraud_xgboost` |
| ML Model | `mdl_` | `mdl_{BU}_{Domain}_{Version}` | `mdl_fin_fraud_v1` |
| Delta Table | — | `{Layer}_{Domain}_{Entity}` | `gold_fact_sales` |
| Shortcut | `sc_` | `sc_{Source}_{Domain}` | `sc_lh_fin_raw_customers` |
| Reflex | `rx_` | `rx_{BU}_{Domain}_{Trigger}` | `rx_sales_pipeline_deal_alert` |
| Environment | `env_` | `env_{BU}_{Purpose}_{Env}` | `env_fin_spark_prod` |
| Policy Set | `ps_` | `ps_tenant_{Purpose}` / `ps_cap_{BU}_{Env}_{Purpose}` | `ps_cap_fin_prod_itemcreation` |
| Variable Library | `vl_` | `vl_{BU}_{Domain}` | `vl_fin_gl` |
| Copy Job | `cj_` | `cj_{BU}_{Source}_to_{Layer}_{Freq}` | `cj_fin_sap_to_bronze_hourly` |
| SQL Database | `sqldb_` | `sqldb_{BU}_{Function}_{Env}` | `sqldb_fin_app_prod` |
| Mirrored Database | `mir_` | `mir_{BU}_{Source}_{Env}` | `mir_fin_azuresql_prod` |
| Eventhouse | `eh_` | `eh_{BU}_{Domain}_{Env}` | `eh_eng_telemetry_prod` |
| Real-Time Dashboard | `rtd_` | `rtd_{BU}_{Domain}_{Audience}` | `rtd_ops_latency_noc` |
| User Data Function | `udf_` | `udf_{BU}_{Domain}_{Purpose}` | `udf_fin_gl_validate_journal` |
| Data Agent | `da_` | `da_{BU}_{Domain}_{Purpose}` | `da_sales_pipeline_qna` |
| Airflow Job | `af_` | `af_{BU}_{Function}_{Domain}` | `af_eng_orchestrate_telemetry` |
| dbt Job | `dbt_` | `dbt_{BU}_{Domain}_{Layer}` | `dbt_fin_gl_silver` |
| GraphQL API | `gql_` | `gql_{BU}_{Domain}_{Env}` | `gql_sales_orders_prod` |

> No pattern in this matrix contains `{Region}` except Capacity.

---

## Enforcement Recommendations

Fabric policies (preview) can't evaluate names: their conditions are workspace, security group, sensitivity label, item type, recipient domain, and workspace setting. Enforce naming with these guardrails:

- **Governance wiki** — publish this standard and link it from every workspace description
- **Workspace admin gating** — limit workspace creation to a small group who validate names before provisioning
- **Automated audits** — use Fabric REST APIs or the Admin API to scan resource names on a schedule and flag violations
- **CI/CD naming checks** — if deploying via deployment pipelines or Git integration, add a pre-deployment validation step that regex-checks names against the standard
- **Tagging & metadata** — supplement names with descriptions and tags; names carry structure, descriptions carry context
- **Onboarding templates** — provide pre-named workspace/lakehouse templates so teams start compliant by default