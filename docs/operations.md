# Operations

How to authenticate, run, publish, and schedule the FabricWAF tooling — plus how the
private tenant outputs are kept out of source control.

## Identities & auth

| Component | Auth | Required role |
|---|---|---|
| Terraform (`terraform/`) | `az login` / service principal | Owner/Contributor + policy-assignment rights |
| Python scripts (`scripts/`) | `DefaultAzureCredential` → Fabric / Power BI / Graph scope | **Fabric administrator** (`Tenant.Read.All`; write for apply/deploy) |
| Notebooks (`notebooks/`) | `notebookutils.credentials.getToken("pbi")` | Notebook runs as a **Fabric administrator** identity |
| `push_notebook.sh` | `az rest` with the Fabric CLI token | Contributor on the target workspace |
| CI/CD | `fabric-gh-runner` managed identity (no secrets) | see [governance-compliance.md](governance-compliance.md) |

For the scripts and the publisher, log in to the Fabric scope explicitly:

```bash
az login --tenant "<tenant-id>" --scope "https://api.fabric.microsoft.com/.default"
```

> Conditional-access can expire the token mid-session (`AADSTS70043`) — just re-run the
> `az login` above.

## Prerequisites

- **Azure CLI** (`az`) for Terraform, the scripts' credential chain, and `push_notebook.sh`.
- **Python 3.10+** with `requests`, `azure-identity` (scripts).
- A **Fabric administrator** identity — most inventory/audit APIs are admin-scoped.
- For the notebooks: a Fabric workspace on a capacity, the **`lh_fabric_management`**
  lakehouse (schema-enabled), and `semantic-link-labs` (installed in-notebook via
  `%pip`). For the performance pipeline, the **Microsoft Fabric Capacity Metrics** app
  with **write/owner** on its model, and the *Semantic Model Execute Queries REST API*
  tenant setting ON.

## Run order — management lakehouse

The notebooks have dependencies; run them in this order:

```
1. gateway_inventory_to_lakehouse        (independent)
2. connection_inventory_to_lakehouse     (independent) ──┐
3. gold_governance_to_lakehouse          (needs #2)      │
4. build_semantic_model                  (needs #3)      │  governance track
   └─ add_file_extension_column          (one-off, after the model exists)

A. heavy_interactive_workloads_to_lakehouse   (independent)   ┐ performance track
B. build_interactive_workload_model           (needs #A)      ┘
```

The gold and model notebooks fail fast if their input table is missing (e.g. gold
raises `RuntimeError` if `connection_semantic_model_map` doesn't exist yet). If a
pre-SCD2 version of `gateways`/`connections` exists from an old run, drop it first (see
[management-lakehouse.md](management-lakehouse.md#temporal-model-scd2--snapshots)).

## Publishing notebooks

Notebooks are generated from `gen_*.py`, then pushed with:

```bash
scripts/push_notebook.sh <workspace-id> <path/to.ipynb> [display-name] [folder]
```

Governance notebooks go in the `governance` folder, performance notebooks in the
`Performance` folder. Update-by-name pushes content but does **not** move an item between
folders. See [management-lakehouse.md](management-lakehouse.md#regenerating--publishing).

## Scheduling

- **Notebooks:** schedule each in Fabric (notebook schedule or a Data Pipeline chaining
  them in run order). SCD2 + daily snapshots mean re-runs accumulate history rather than
  overwrite it.
- **Audit / tenant-settings scripts:** run on a cron / Azure Automation runbook; the
  audit script can email results.
- **CI/CD:** the GitHub Actions workflow runs on PR (validate only) and on push to `main`
  (validate → deploy → configure-capacity).

## Private tenant outputs — keep out of git

Several scripts and notebooks emit **real tenant data** (workspace names, GUIDs, emails,
per-capacity CU). These must not be committed to a public repo:

| File / pattern | Produced by |
|---|---|
| `scripts/*.csv` (`cu_by_workspace.csv`, `heavy_interactive_ops.csv`, `tenant_settings_comparison.csv`) | `pull_cu_usage.py`, `report_heavy_interactive_ops.py`, `compare_tenant_settings.py` |
| `scripts/*_tenant_settings.json` | tenant-settings baseline exports |
| `tenant-settings-report.json` | `assess_tenant_settings.py` |
| `audit-report.json` | `audit_fabric.py` (tenant-wide audit results) |
| `ID-Fabric.md` | working notes with tenant identifiers |

**Recommended `.gitignore` block** (verify it's present before committing — the repo's
`.gitignore` currently does **not** exclude these):

```gitignore
# --- Real tenant outputs (private — never commit) ---
scripts/*.csv
scripts/*_tenant_settings.json
tenant-settings-report.json
audit-report.json
ID-Fabric.md
```

`compare_tenant_settings.py` is genericised (configurable `--label-baseline` /
`--label-live`, no hard-coded tenant names). `audit_fabric.py` writes `audit-report.json`
with **real tenant data** (workspace names, GUIDs, owner emails), so it is gitignored;
if you want a sample committed to the repo, sanitise it first and force-add it
(`git add -f audit-report.json`).
