"""
compare_tenant_settings.py

Compares two Fabric tenants' tenant settings and writes a CSV:
  - a *baseline* tenant  -> read from a local JSON export
                           (e.g. transcribed from admin-portal screenshots / a PDF)
  - a *live* tenant      -> pulled live from the Fabric Admin REST API

Both tenant labels are configurable (--label-baseline / --label-live) so no tenant
names are hard-coded. Default output is LONG format: one row per (tenant, setting),
leading column Tenant, sorted so both tenants' rows for the same setting sit
adjacent. Use --wide for a side-by-side diff (one row per setting).

Alignment: settings are matched on a normalised title (case-, punctuation- and
"(preview)"-insensitive), since the admin-portal title is identical across tenants.
settingName is filled from the live pull where a match is found.

Authentication
--------------
  DefaultAzureCredential against the Fabric scope (same as assess_tenant_settings.py).
  Requires a Fabric administrator identity (Tenant.Read.All) to read admin APIs.

Environment variables
----------------------
  COMPARE_BASELINE_JSON  (optional) default path to the baseline tenant JSON.
  COMPARE_LABEL_BASELINE (optional) default baseline label.
  COMPARE_LABEL_LIVE     (optional) default live label.

Exit codes: 0 ok, 1 failure.
"""

import argparse
import csv
import json
import os
import re
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"
TENANT_SETTINGS_URL = f"{FABRIC_API}/admin/tenantsettings"
_HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_BASELINE_JSON = os.environ.get(
    "COMPARE_BASELINE_JSON", os.path.join(_HERE, "baseline_tenant_settings.json"))
DEFAULT_LABEL_BASELINE = os.environ.get("COMPARE_LABEL_BASELINE", "Baseline")
DEFAULT_LABEL_LIVE = os.environ.get("COMPARE_LABEL_LIVE", "Live")

_credential = DefaultAzureCredential()


# ---------------------------------------------------------------------------
# Live pull
# ---------------------------------------------------------------------------

def _headers() -> dict:
    token = _credential.get_token("https://api.fabric.microsoft.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_tenant_settings() -> list:
    """GET all tenant settings, following continuationToken pagination (with 429 backoff)."""
    results, url, params, attempts = [], TENANT_SETTINGS_URL, None, 0
    while url:
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code == 429:
            attempts += 1
            wait = min(int(resp.headers.get("Retry-After", 10)), 60)
            print(f"  [429] throttled (attempt {attempts}) — waiting {wait}s ...", flush=True)
            if attempts > 8:
                resp.raise_for_status()
            time.sleep(wait)
            continue
        attempts = 0
        resp.raise_for_status()
        body = resp.json()
        results.extend(body.get("value", []))
        next_uri, token = body.get("continuationUri"), body.get("continuationToken")
        if next_uri:
            url, params = next_uri, None
        elif token:
            url, params = TENANT_SETTINGS_URL, {"continuationToken": token}
        else:
            url = None
    return results


# ---------------------------------------------------------------------------
# Normalisation and row-shaping
# ---------------------------------------------------------------------------

def norm_title(title: str) -> str:
    """Case/punctuation/(preview)-insensitive key for matching titles across tenants."""
    t = (title or "").lower().replace("’", "'").replace("“", "").replace("”", "")
    t = re.sub(r"\(.*?\)", " ", t)          # drop parentheticals: (preview), (.pbix), ...
    t = re.sub(r"[^a-z0-9]+", " ", t)        # non-alphanumerics -> space
    t = re.sub(r"\s+", " ", t).strip()
    if t.startswith("user can "):            # PDF sometimes says "User can" vs API "Users can"
        t = "users can " + t[len("user can "):]
    return t


def baseline_row(s: dict) -> dict:
    groups = s.get("groups") or []
    return {
        "state": s.get("state", ""),
        "apply_to": s.get("apply_to", ""),
        "groups": "; ".join(groups),
        "detail": s.get("detail", ""),
        "title": s.get("title", ""),
    }


def _apply_to(live: dict) -> tuple:
    """(apply_to label, joined group names) from a live tenant-setting object."""
    enabled = bool(live.get("enabled"))
    inc = [g.get("name", "") for g in (live.get("enabledSecurityGroups") or [])]
    exc = [g.get("name", "") for g in (live.get("excludedSecurityGroups") or [])]
    if not enabled:
        return "Entire org", ""
    if exc:
        return "Except groups", "; ".join(exc)
    if live.get("canSpecifySecurityGroups") and inc:
        return "Specific groups", "; ".join(inc)
    return "Entire org", ""


def live_row(live: dict) -> dict:
    apply_to, groups = _apply_to(live)
    return {
        "state": "Enabled" if live.get("enabled") else "Disabled",
        "apply_to": apply_to,
        "groups": groups,
        "detail": "",
        "title": live.get("title", ""),
        "settingName": live.get("settingName", ""),
    }


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def write_long(path, baseline, live, name_by_key, label_baseline, label_live) -> int:
    cols = ["Tenant", "settingName", "Title", "State", "Apply_to", "Security_groups", "Detail"]
    rows = []
    for key in set(baseline) | set(live):
        sname = name_by_key.get(key, "")
        if key in baseline:
            b = baseline[key]
            rows.append([label_baseline, sname, b["title"], b["state"], b["apply_to"], b["groups"], b["detail"]])
        if key in live:
            i = live[key]
            rows.append([label_live, i.get("settingName", sname), i["title"],
                         i["state"], i["apply_to"], i["groups"], i["detail"]])
    rows.sort(key=lambda r: (norm_title(r[2]), r[0]))
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)


def write_wide(path, baseline, live, name_by_key, label_baseline, label_live) -> int:
    a, b = label_baseline, label_live
    cols = ["settingName", "Title",
            f"{a}_State", f"{b}_State", "State_match",
            f"{a}_Apply_to", f"{b}_Apply_to", "Scope_match",
            f"{a}_groups", f"{b}_groups", "Present_in"]
    rows = []
    for key in set(baseline) | set(live):
        bl, lv = baseline.get(key), live.get(key)
        title = (bl or lv)["title"]
        sname = (lv or {}).get("settingName") or name_by_key.get(key, "")
        bl_state = bl["state"] if bl else ""
        lv_state = lv["state"] if lv else ""
        bl_scope = bl["apply_to"] if bl else ""
        lv_scope = lv["apply_to"] if lv else ""
        present = "both" if bl and lv else (a if bl else b)
        state_match = "" if not (bl and lv) else ("yes" if bl_state == lv_state else "NO")
        scope_match = "" if not (bl and lv) else ("yes" if bl_scope == lv_scope else "NO")
        rows.append([sname, title, bl_state, lv_state, state_match,
                     bl_scope, lv_scope, scope_match,
                     bl["groups"] if bl else "", lv["groups"] if lv else "", present])
    rows.sort(key=lambda r: norm_title(r[1]))
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a baseline tenant (local JSON) vs a live tenant (Fabric Admin API).")
    parser.add_argument("--out", default="tenant_settings_comparison.csv",
                        help="Output CSV path (default: tenant_settings_comparison.csv).")
    parser.add_argument("--wide", action="store_true",
                        help="Side-by-side diff (one row per setting) instead of long format.")
    parser.add_argument("--baseline-json", default=DEFAULT_BASELINE_JSON,
                        help="Path to the baseline tenant settings JSON export.")
    parser.add_argument("--label-baseline", default=DEFAULT_LABEL_BASELINE,
                        help="Label for the baseline (JSON) tenant in the output.")
    parser.add_argument("--label-live", default=DEFAULT_LABEL_LIVE,
                        help="Label for the live (API) tenant in the output.")
    args = parser.parse_args()

    with open(args.baseline_json) as fh:
        baseline_raw = json.load(fh)["settings"]
    baseline = {norm_title(s["title"]): baseline_row(s) for s in baseline_raw}

    print(f"Pulling {args.label_live} tenant settings from the Fabric Admin API ...")
    try:
        live = list_tenant_settings()
    except requests.HTTPError as exc:
        print(f"ERROR: HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        if exc.response.status_code in (401, 403):
            print("  -> Needs a Fabric administrator identity (Tenant.Read.All).")
        return 1

    live_map = {norm_title(s.get("title", "")): live_row(s) for s in live if s.get("title")}
    name_by_key = {k: v["settingName"] for k, v in live_map.items()}

    matched = len(set(baseline) & set(live_map))
    print(f"  {args.label_baseline}: {len(baseline)} settings | {args.label_live}: {len(live_map)} settings "
          f"| matched by title: {matched}")

    if args.wide:
        n = write_wide(args.out, baseline, live_map, name_by_key, args.label_baseline, args.label_live)
    else:
        n = write_long(args.out, baseline, live_map, name_by_key, args.label_baseline, args.label_live)
    print(f"  Wrote {n} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
