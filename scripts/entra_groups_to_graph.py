"""
entra_groups_to_graph.py

Pulls every Entra (Azure AD) security group and its direct memberships from Microsoft
Graph and emits a nodes/edges pair ready to load into SQL Server / Azure SQL graph
tables. This is step 1 of correlating direct user->workspace access (from
workspace_access_to_lakehouse / the workspace_access tables) against existing security
groups: once Users, Groups and Workspaces are all nodes you can ask "which direct
user->workspace grants are already covered by a group the user belongs to?".

Output (gitignored — contains user names / UPNs):
  graph_nodes.csv   node_id, node_type, display_name, upn, mail
  graph_edges.csv   from_id, to_id, edge_type, from_type, to_type

  node_type : Group | User | ServicePrincipal | Device
  edge_type : MEMBER_OF   (member -> group; nested groups produce Group->Group edges)

node_id / from_id / to_id are Entra objectId GUIDs — stable keys to join on when the
workspace nodes + HAS_ACCESS edges are added later. Load with sql/entra_graph.sql.

Authentication:
  DefaultAzureCredential against Microsoft Graph. Needs a directory-reader identity with
  Group.Read.All / Directory.Read.All (a Global/Directory admin token works).

Usage:
  pip install azure-identity requests
  python entra_groups_to_graph.py                 # all security groups
  python entra_groups_to_graph.py --top-groups 50 # quick sample (first 50 groups)

Exit codes: 0 ok, 2 failure.
"""

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from azure.identity import DefaultAzureCredential

GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
MAX_WORKERS = 8

TYPE_MAP = {
    "#microsoft.graph.user": "User",
    "#microsoft.graph.group": "Group",
    "#microsoft.graph.servicePrincipal": "ServicePrincipal",
    "#microsoft.graph.device": "Device",
    "#microsoft.graph.orgContact": "Contact",   # external / synced mail contacts
}

_credential = DefaultAzureCredential()
_tok = {"v": None, "exp": 0.0}


def _headers() -> dict:
    if time.time() > _tok["exp"]:
        t = _credential.get_token(GRAPH_SCOPE)
        _tok["v"], _tok["exp"] = t.token, t.expires_on - 120
    return {"Authorization": f"Bearer {_tok['v']}"}


def _get(url: str) -> dict:
    last_exc = None
    for attempt in range(6):
        try:
            r = requests.get(url, headers=_headers(), timeout=60)
        except requests.exceptions.RequestException as exc:   # DNS/connection/timeout blip
            last_exc = exc
            time.sleep(min(2 ** attempt, 30))
            continue
        if r.status_code == 429:
            wait = min(int(r.headers.get("Retry-After", 10)), 60)
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            raise PermissionError(
                f"HTTP {r.status_code} on Graph -> needs Group.Read.All / "
                "Directory.Read.All (a Global/Directory admin token).")
        r.raise_for_status()
        return r.json()
    if last_exc:
        raise last_exc
    r.raise_for_status()


def _get_all(url: str) -> list:
    """Follow @odata.nextLink pagination."""
    out = []
    while url:
        body = _get(url)
        out.extend(body.get("value", []))
        url = body.get("@odata.nextLink")
    return out


def list_security_groups() -> list:
    url = (f"{GRAPH}/groups?$top=999&$filter=securityEnabled eq true"
           "&$select=id,displayName,mail,description,securityEnabled,mailEnabled,"
           "groupTypes,onPremisesSyncEnabled")
    groups = _get_all(url)
    # keep pure security groups; drop Microsoft 365 (Unified) groups
    return [g for g in groups if "Unified" not in (g.get("groupTypes") or [])]


def members_of(group_id: str) -> list:
    url = (f"{GRAPH}/groups/{group_id}/members?$top=999"
           "&$select=id,displayName,userPrincipalName,mail,accountEnabled")
    return _get_all(url)


def main() -> int:
    p = argparse.ArgumentParser(description="Entra security groups + memberships -> graph nodes/edges.")
    p.add_argument("--top-groups", type=int, default=0, help="Only process the first N groups (sampling).")
    p.add_argument("--nodes-out", default="graph_nodes.csv")
    p.add_argument("--edges-out", default="graph_edges.csv")
    args = p.parse_args()

    print("Listing security groups from Microsoft Graph...")
    groups = list_security_groups()
    if args.top_groups:
        groups = groups[:args.top_groups]
    print(f"  {len(groups)} security group(s)")

    # nodes keyed by objectId (dedup); groups first
    nodes: dict[str, dict] = {}
    for g in groups:
        nodes[g["id"]] = {"node_id": g["id"], "node_type": "Group",
                          "display_name": g.get("displayName"), "upn": None,
                          "mail": g.get("mail")}

    edges: list[dict] = []
    print(f"  fetching memberships ({MAX_WORKERS} workers)...")
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(members_of, g["id"]): g for g in groups}
        for fut in as_completed(futures):
            g = futures[fut]
            try:
                members = fut.result()
            except Exception as exc:  # noqa: BLE001 — report and continue
                print(f"    ! members failed for {g.get('displayName')}: {exc}")
                members = []
            for m in members:
                mtype = TYPE_MAP.get(m.get("@odata.type"), "Unknown")
                mid = m.get("id")
                if mid and mid not in nodes:
                    nodes[mid] = {"node_id": mid, "node_type": mtype,
                                  "display_name": m.get("displayName"),
                                  "upn": m.get("userPrincipalName"), "mail": m.get("mail")}
                edges.append({"from_id": mid, "to_id": g["id"], "edge_type": "MEMBER_OF",
                              "from_type": mtype, "to_type": "Group"})
            done += 1
            if done % 100 == 0 or done == len(groups):
                print(f"    {done}/{len(groups)} groups", flush=True)

    # write nodes / edges
    with open(args.nodes_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["node_id", "node_type", "display_name", "upn", "mail"])
        w.writeheader()
        w.writerows(nodes.values())
    with open(args.edges_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["from_id", "to_id", "edge_type", "from_type", "to_type"])
        w.writeheader()
        w.writerows(edges)

    # summary
    by_type: dict[str, int] = {}
    for n in nodes.values():
        by_type[n["node_type"]] = by_type.get(n["node_type"], 0) + 1
    print("\n" + "=" * 60)
    print(f"  nodes : {len(nodes)}  " + "  ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    print(f"  edges : {len(edges)}  (MEMBER_OF)")
    print(f"  -> {args.nodes_out}, {args.edges_out}")
    print("  Load with sql/entra_graph.sql (nodes first, then edges by $node_id join).")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PermissionError as exc:
        print(f"ERROR: {exc}")
        sys.exit(2)
