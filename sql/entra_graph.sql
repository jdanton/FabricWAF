/* ===========================================================================
   entra_graph.sql

   >>> RUN THIS ON AZURE SQL DATABASE (or SQL Server 2017+). <<<
   The graph NODE / EDGE / MATCH feature does NOT exist in Fabric Warehouse or the
   Lakehouse SQL endpoint. The CSV / Delta extracts (graph_nodes, graph_edges,
   graph_access_nodes, graph_access_edges) are just the node/edge DATA — the graph
   itself is built here, in Azure SQL.

   Access graph = Entra security groups (scripts/entra_groups_to_graph.py) + workspace
   access (notebooks/build_access_graph_to_lakehouse.ipynb).

   ONE heterogeneous NODE table (User / Group / ServicePrincipal / Contact /
   Workspace, keyed by Entra objectId / workspace GUID) + one EDGE table per
   relationship: MEMBER_OF (member -> group) and HAS_ACCESS (principal ->
   workspace, with role). Nodes are upserted (WHERE NOT EXISTS) so the two
   sources union on node_id without duplicates.

   Inputs (CSV -> staging):
     graph_nodes.csv    (Entra)   -> dbo.stg_nodes
     access_nodes.csv   (access)  -> dbo.stg_nodes           (append)
     graph_edges.csv    (Entra)   -> dbo.stg_member_edges
     access_edges.csv   (access)  -> dbo.stg_access_edges
   =========================================================================== */

-- 1. Graph tables -----------------------------------------------------------
CREATE TABLE dbo.GraphNode (
    node_id      NVARCHAR(64)  NOT NULL,   -- Entra objectId / workspace GUID
    node_type    NVARCHAR(32)  NOT NULL,   -- Group|User|ServicePrincipal|Contact|Workspace
    display_name NVARCHAR(256) NULL,
    upn          NVARCHAR(256) NULL,
    mail         NVARCHAR(256) NULL,
    CONSTRAINT PK_GraphNode PRIMARY KEY (node_id)
) AS NODE;
GO

CREATE TABLE dbo.MemberOf ( edge_type NVARCHAR(32) NULL ) AS EDGE;          -- member -> group
GO
CREATE TABLE dbo.HasAccess (                                                -- principal -> workspace
    edge_type     NVARCHAR(32) NULL,
    role          NVARCHAR(32) NULL,       -- Admin|Member|Contributor|Viewer
    is_write_role BIT          NULL
) AS EDGE;
GO

-- 2. Staging (plain heaps the CSVs load into) -------------------------------
CREATE TABLE dbo.stg_nodes (
    node_id NVARCHAR(64) NOT NULL, node_type NVARCHAR(32) NOT NULL,
    display_name NVARCHAR(256) NULL, upn NVARCHAR(256) NULL, mail NVARCHAR(256) NULL
);
CREATE TABLE dbo.stg_member_edges (
    from_id NVARCHAR(64) NOT NULL, to_id NVARCHAR(64) NOT NULL,
    edge_type NVARCHAR(32) NULL, from_type NVARCHAR(32) NULL, to_type NVARCHAR(32) NULL
);
CREATE TABLE dbo.stg_access_edges (
    from_id NVARCHAR(64) NOT NULL, to_id NVARCHAR(64) NOT NULL,
    edge_type NVARCHAR(32) NULL, from_type NVARCHAR(32) NULL, to_type NVARCHAR(32) NULL,
    role NVARCHAR(32) NULL, is_write_role BIT NULL
);
GO

/* Load the CSVs into the three staging tables (Azure Data Studio import, bcp,
   BULK INSERT from blob, SqlBulkCopy, ...). Load BOTH graph_nodes.csv and
   access_nodes.csv into dbo.stg_nodes. Then run sections 3-4. */

-- 3. Nodes — upsert so Entra + access node sets union on node_id ------------
-- IMPORTANT: workspace_access frequently keys USERS by UPN/email while the Entra graph
-- keys them by objectId GUID. Skip an email-keyed principal when an Entra (GUID) node
-- already carries that email as upn/mail — otherwise the same person becomes two nodes
-- and their access never connects to their group memberships (the correlation returns 0).
INSERT INTO dbo.GraphNode (node_id, node_type, display_name, upn, mail)
SELECT s.node_id, MAX(s.node_type), MAX(s.display_name), MAX(s.upn), MAX(s.mail)
FROM   dbo.stg_nodes s
WHERE  NOT EXISTS (SELECT 1 FROM dbo.GraphNode n WHERE n.node_id = s.node_id)
  AND  NOT EXISTS (SELECT 1 FROM dbo.stg_nodes e
                   WHERE e.node_id <> s.node_id AND (e.upn = s.node_id OR e.mail = s.node_id))
GROUP  BY s.node_id;
GO

-- 4. Edges — resolve $node_id via the join ----------------------------------
INSERT INTO dbo.MemberOf ($from_id, $to_id, edge_type)
SELECT nf.$node_id, nt.$node_id, s.edge_type
FROM   dbo.stg_member_edges s
JOIN   dbo.GraphNode nf ON nf.node_id = s.from_id
JOIN   dbo.GraphNode nt ON nt.node_id = s.to_id;
GO

-- Resolve the (often email-keyed) access principal to its node by id, then upn, then
-- mail, so access edges land on the SAME node as the membership edges.
INSERT INTO dbo.HasAccess ($from_id, $to_id, edge_type, [role], is_write_role)
SELECT nf.$node_id, nt.$node_id, s.edge_type, s.[role], s.is_write_role
FROM   dbo.stg_access_edges s
JOIN   dbo.GraphNode nt ON nt.node_id = s.to_id
CROSS APPLY (SELECT TOP 1 node_id FROM dbo.GraphNode
             WHERE node_id = s.from_id OR upn = s.from_id OR mail = s.from_id
             ORDER BY CASE WHEN node_id = s.from_id THEN 0 ELSE 1 END) rf
JOIN   dbo.GraphNode nf ON nf.node_id = rf.node_id;
GO

-- 5. Sanity checks ----------------------------------------------------------
SELECT node_type, COUNT(*) AS nodes FROM dbo.GraphNode GROUP BY node_type;
SELECT (SELECT COUNT(*) FROM dbo.MemberOf)  AS member_of_edges,
       (SELECT COUNT(*) FROM dbo.HasAccess) AS has_access_edges;
GO

/* ===========================================================================
   CORRELATION — direct user->workspace grants that a group the user already
   belongs to ALSO has (i.e. safe to replace with existing group membership).
   Add "AND ua.is_write_role = 1" to focus on the risky write-level grants.
   =========================================================================== */
SELECT DISTINCT
    u.display_name AS user_name,
    u.mail         AS user_mail,
    w.display_name AS workspace,
    ua.[role]      AS direct_role,
    g.display_name AS covered_by_group
FROM  dbo.GraphNode u, dbo.HasAccess ua, dbo.GraphNode w,
      dbo.MemberOf  mo, dbo.GraphNode g, dbo.HasAccess ga
WHERE MATCH(u-(ua)->w AND u-(mo)->g AND g-(ga)->w)
  AND u.node_type = 'User'
  AND w.node_type = 'Workspace'
ORDER BY user_name, workspace;
GO

/* Inverse — direct user grants NOT covered by any group membership (these need
   a new/expanded group before you can remove the direct grant):

SELECT u.display_name, w.display_name AS workspace, ua.role
FROM   dbo.GraphNode u, dbo.HasAccess ua, dbo.GraphNode w
WHERE  MATCH(u-(ua)->w) AND u.node_type='User' AND w.node_type='Workspace'
  AND  NOT EXISTS (
        SELECT 1 FROM dbo.MemberOf mo, dbo.GraphNode g, dbo.HasAccess ga
        WHERE  MATCH(u-(mo)->g AND g-(ga)->w));
*/
