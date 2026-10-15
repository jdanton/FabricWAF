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

## 2. Capacity-level Surge Protection

| Setting | Required value | Why |
|---------|---------------|-----|
| Surge protection | Enabled | Rejects new **background** jobs (refreshes, pipelines, Spark) before the capacity falls into deep throttling. That keeps interactive users working and shortens recovery. |
| Background rejection threshold | 75 % (starting point) | Tune from the Capacity Metrics app **Background rejection** chart: set it above typical peaks and below the level where interactive rejection starts. |
| Background recovery threshold | 50 % (starting point) | Must be > 5 and below the rejection threshold. Set it near the typical background percentage. |

`configure_capacity.py` enables surge protection with these defaults only when
it's **disabled**. If it's already enabled, the per-capacity thresholds are
treated as tuned and left alone. API: `GET/PATCH /v1/capacities/{id}/surgeProtection`
(capacity admin).

Surge protection doesn't block operations billed through on-demand Spark billing,
or OneLake operations. To fully protect a mission-critical solution, put it on its own capacity.

### Workspace-level surge protection (preview)

Workspace-level surge protection caps how many CUs any single workspace can use over a rolling 24 hours. A workspace that hits the cap is set to **Blocked**. Configure it in **OneLake catalog → Govern → Capacities → Surge protection**. There is no public API yet, so it isn't automated here.

| Setting | Recommendation |
|---------|---------------|
| Workspace consumption limit | Enable on shared/dev capacities; start at 20–30 % per workspace |
| Production workspaces | Mark as **Mission critical** (exempt from the per-workspace cap) |
| Block duration | Time-boxed (e.g. 4 h), not indefinite, so blocked teams recover without a ticket |

---

## 3. Capacity Overage (GA September 2026)

Capacity overage pays off throttling debt automatically and bills it at **3× the
pay-as-you-go rate** on a separate meter. It is **on by default for new
capacities** with a 25 % threshold, so leaving it untouched is itself a cost decision.

| Setting | Required value | Why |
|---------|---------------|-----|
| Overage | Enabled | Removes user-visible throttling during short spikes without a permanent SKU change. |
| Rolling 24-hour threshold | **10 %** of the SKU's daily CU-hours; never above **⅓** | Above ⅓ of daily CU-hours, overage costs more than scaling up the SKU. The threshold is a *spending threshold, not a hard cap*: in-flight work keeps running after it's reached. |
| Cost alert | Azure Cost Management budget on the **Capacity Overage Capacity Usage CU** meter | Overage cost appears only on the Azure bill. Alert on it, then right-size the SKU if it's recurring. |

Example: an F64 has 1,536 daily CU-hours, which gives a target threshold of 153 CU-h and a ceiling of 512 CU-h.
The threshold also consumes Fabric **quota** (threshold ÷ 24 CUs).

`configure_capacity.py` sets the threshold when overage is disabled or above
the ⅓ ceiling. API: `GET/PATCH /v1/capacities/{id}/overageConfiguration`.

> **Terraform note:** `azurerm_fabric_capacity` (ARM API 2023-11-01) has no
> overage argument. The ARM `properties.overage` block exists only in the
> 2026-08-01-preview and 2026-09-01-preview API versions. Overage is therefore
> managed through the Fabric REST API, not Terraform, until azurerm adopts a
> stable API version.

---

## 4. Capacity Overload Notifications

| Setting | Required value | Why |
|---------|---------------|-----|
| Overload notifications | Enabled | Emails capacity admins when the capacity is throttled, so they can scale before users are affected. |
| Surge-protection notifications | Enabled (Govern → Capacities) | Notifies when background rejection or a workspace block starts. |

Notifications go to all members of the `Fabric-Capacity-Admins` group. For
event-driven alerting, subscribe to **Capacity Overview Events** (GA) in
Real-Time hub, or use the capacity alert templates (GA September 2026). These
replace polling the Metrics app.

---

## 5. Workspace Assignment Permissions

| Setting | Required value | Why |
|---------|---------------|-----|
| Who can assign workspaces | `SpecificUsersAndGroups` — `Fabric-Capacity-Admins` only | Prevents teams from routing workloads to a capacity without approval, which could degrade performance for other workspaces sharing the same capacity. |

Workspace assignment is a privileged operation. Limiting it to the
`Fabric-Capacity-Admins` group ensures capacity planning remains centralised.

---

## 6. Capacity-scoped Fabric Policy (preview)

Each capacity can have **one active capacity-scoped policy set**. Today it
holds one policy: **Allow item creation**. Use it to allow-list which item types
can be created on production capacities, and by whom.

| Setting | Recommendation | Why |
|---------|---------------|-----|
| Prod capacities | Activate a policy set (`ps_cap_{BU}_{Env}_{Purpose}`) that allows item creation only for the deployment identity and a platform group | Production content should arrive through deployment pipelines or Git, not be hand-built in the portal. |
| Dev/test capacities | No policy set, or one that blocks only unapproved item types | Keeps developers productive. |
| Item types in rules | Use **API identifiers** (`DataPipeline`, `Reflex`, `KQLDashboard`), not display names | The rule API rejects display names. |

Activation needs a **capacity admin**. Rules take effect within 15 minutes.
Power BI items aren't enforced yet. Fabric policies are **not supported on
capacities in West US** (or West/North Europe), so prefer another US region for
capacities that need them. Full guidance, including the REST API, is in
[docs/governance-compliance.md](docs/governance-compliance.md#fabric-policies-preview).

---

## 7. Semantic Model Query Timeout

| Setting | Recommended value | Why |
|---------|---------------|-----|
| Power BI workloads → Semantic models → Query timeout | 600 seconds | Stops runaway DAX queries from using up the capacity. Fix long-running queries in the model rather than giving them unlimited runtime. |

Set this in the capacity's **Power BI workloads** settings. The Premium Gen1
`/capacities/{id}/Workloads` API that this repo used to call has no effect on
F SKUs, so this setting is applied manually and checked during capacity reviews.

---

## 8. On-demand Billing for Spark (formerly "Autoscale Billing for Spark")

F SKUs don't support the Premium P-SKU "autoscale" (extra v-cores). The repo
previously enabled that on F64+, but it had no effect on F SKUs. The F-SKU
options for burst headroom are capacity overage (§3) and **on-demand billing
for Spark**:

| Setting | Recommendation | Why |
|---------|---------------|-----|
| On-demand billing for Spark | **Decide per capacity — not on by default** | Moves Spark jobs off the shared capacity onto pay-as-you-go serverless billing (0.5 CU-hour rate). There's no smoothing or bursting, and jobs are queued or throttled at the max CU you set. Good for capacities dominated by Spark batch work; adds variable cost. |

Being able to run *all* workloads on demand, and the zero-provisioned F0 SKU, are
announced but not available yet (October 2026).

---

## Applying the Configuration

The `scripts/configure_capacity.py` script applies §1–§4 via the Power BI
Admin API and the Fabric REST API. §5–§8 are set in the portal (Govern tab) or
through the policy-set API and checked during capacity reviews. It runs automatically as the
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
