/* ===========================================================================
   report_usage_by_user.sql

   >>> RUN THIS ON THE lh_fabric_management LAKEHOUSE SQL ENDPOINT. <<<
   Schema: fabricmanagement. Populated by
   notebooks/heavy_interactive_workloads_to_lakehouse.ipynb.

   "Which reports do these specific users run?"

   READ THIS BEFORE TRUSTING A ZERO
   --------------------------------
   1. COVERAGE. report_activity_events only holds the days the notebook has
      actually pulled. The activity log is capped at ~200 requests/hour and a busy
      day costs ~30 requests, so early runs cover only a handful of days. Query 0
      below tells you how much history you have. A user with no rows may simply
      predate your coverage -- absence of evidence is not evidence of absence.
   2. VIEWS vs EXCEL. is_view covers ViewReport / ViewDashboard / ViewTile.
      AnalyzedByExternalApplication (Analyze in Excel / XMLA) is tracked but is
      NOT a view -- on this tenant it runs at nearly the same volume as
      ViewReport, so a user who lives in Excel looks idle in a views-only query.
      Query 2 counts it separately; query 1 (gold) does not.
   3. WINDOW. gold_report_user_usage rolls up usage_window_days (default 28).
      report_activity_events holds everything ever pulled -- use it to look back
      further than the mart does.
   =========================================================================== */

-- Reusable list of the 26 users in question. Repeated inline per query so each
-- one can be run standalone.
--   afolabi.omole, amuranaka, andrew.sellka, bhipps, brian.hasek, bvilleda,
--   cody.tsuda, david.crawford, david.dawe, dsoloway, dwagner, fariha.islam,
--   gplevritis, hdown, jcondurso, jennifer.abendanio, kevin.orr, lmar, mweaver,
--   patrick.fortin, robert.hardy, ron.fontaine, shelley.miller, smote,
--   steve.ainsworth, tom.smaldone   (all @imperialdade.com)


-- 0. Coverage check -- run this FIRST -----------------------------------------
SELECT  MIN(activity_date)            AS first_day,
        MAX(activity_date)            AS last_day,
        COUNT(DISTINCT activity_date) AS days_of_history,
        COUNT(*)                      AS events
FROM    fabricmanagement.report_activity_events;


-- 1. THE ANSWER: every report each of these users ran ------------------------
--    One row per user x report, from the gold mart (views only, 28-day window).
SELECT  u.user_id,
        u.report_name,
        u.workspace_name,
        u.view_count,
        u.active_days,
        u.exec_cadence,              -- Daily / Weekly / Monthly / Sporadic / One-off
        u.median_gap_days,
        u.first_view_utc,
        u.last_view_utc,
        u.days_since_last_view,
        u.pct_of_report_views,       -- this user's share of the report's total views
        u.last_consumption_method    -- Power BI Web / Mobile / Embed / ...
FROM    fabricmanagement.gold_report_user_usage AS u
WHERE   u.user_id IN (
            'afolabi.omole@imperialdade.com',    'amuranaka@imperialdade.com',
            'andrew.sellka@imperialdade.com',    'bhipps@imperialdade.com',
            'brian.hasek@imperialdade.com',      'bvilleda@imperialdade.com',
            'cody.tsuda@imperialdade.com',       'david.crawford@imperialdade.com',
            'david.dawe@imperialdade.com',       'dsoloway@imperialdade.com',
            'dwagner@imperialdade.com',          'fariha.islam@imperialdade.com',
            'gplevritis@imperialdade.com',       'hdown@imperialdade.com',
            'jcondurso@imperialdade.com',        'jennifer.abendanio@imperialdade.com',
            'kevin.orr@imperialdade.com',        'lmar@imperialdade.com',
            'mweaver@imperialdade.com',          'patrick.fortin@imperialdade.com',
            'robert.hardy@imperialdade.com',     'ron.fontaine@imperialdade.com',
            'shelley.miller@imperialdade.com',   'smote@imperialdade.com',
            'steve.ainsworth@imperialdade.com',  'tom.smaldone@imperialdade.com')
ORDER BY u.user_id, u.view_count DESC;


-- 2. Same question, but counting Excel/XMLA as consumption -------------------
--    Straight off the event fact, so it also covers ALL history pulled, not just
--    the mart's 28-day window. Use this one if "run a report" includes Analyze
--    in Excel -- on this tenant that is roughly half of all activity.
SELECT  e.user_id,
        e.item_name                                                    AS report_name,
        e.item_kind,
        e.workspace_name,
        COUNT(*)                                                       AS events,
        SUM(CASE WHEN e.is_view = 1 THEN 1 ELSE 0 END)                 AS views,
        SUM(CASE WHEN e.activity = 'AnalyzedByExternalApplication'
                 THEN 1 ELSE 0 END)                                    AS excel_xmla,
        SUM(CASE WHEN e.activity IN ('ExportReport','ExportArtifact','PrintReport')
                 THEN 1 ELSE 0 END)                                    AS exports,
        COUNT(DISTINCT e.activity_date)                                AS active_days,
        MIN(e.creation_time)                                           AS first_seen_utc,
        MAX(e.creation_time)                                           AS last_seen_utc
FROM    fabricmanagement.report_activity_events AS e
WHERE   e.user_id IN (
            'afolabi.omole@imperialdade.com',    'amuranaka@imperialdade.com',
            'andrew.sellka@imperialdade.com',    'bhipps@imperialdade.com',
            'brian.hasek@imperialdade.com',      'bvilleda@imperialdade.com',
            'cody.tsuda@imperialdade.com',       'david.crawford@imperialdade.com',
            'david.dawe@imperialdade.com',       'dsoloway@imperialdade.com',
            'dwagner@imperialdade.com',          'fariha.islam@imperialdade.com',
            'gplevritis@imperialdade.com',       'hdown@imperialdade.com',
            'jcondurso@imperialdade.com',        'jennifer.abendanio@imperialdade.com',
            'kevin.orr@imperialdade.com',        'lmar@imperialdade.com',
            'mweaver@imperialdade.com',          'patrick.fortin@imperialdade.com',
            'robert.hardy@imperialdade.com',     'ron.fontaine@imperialdade.com',
            'shelley.miller@imperialdade.com',   'smote@imperialdade.com',
            'steve.ainsworth@imperialdade.com',  'tom.smaldone@imperialdade.com')
  AND   e.item_id IS NOT NULL
GROUP BY e.user_id, e.item_name, e.item_kind, e.workspace_name
ORDER BY e.user_id, events DESC;


-- 3. One line per user -- the roll-up ----------------------------------------
--    LEFT JOIN off the user list so users with NO activity still appear with 0.
--    An IN filter alone would silently drop them, which is usually the answer
--    you actually wanted.
WITH target_users (user_id) AS (
    SELECT 'afolabi.omole@imperialdade.com'   UNION ALL
    SELECT 'amuranaka@imperialdade.com'       UNION ALL
    SELECT 'andrew.sellka@imperialdade.com'   UNION ALL
    SELECT 'bhipps@imperialdade.com'          UNION ALL
    SELECT 'brian.hasek@imperialdade.com'     UNION ALL
    SELECT 'bvilleda@imperialdade.com'        UNION ALL
    SELECT 'cody.tsuda@imperialdade.com'      UNION ALL
    SELECT 'david.crawford@imperialdade.com'  UNION ALL
    SELECT 'david.dawe@imperialdade.com'      UNION ALL
    SELECT 'dsoloway@imperialdade.com'        UNION ALL
    SELECT 'dwagner@imperialdade.com'         UNION ALL
    SELECT 'fariha.islam@imperialdade.com'    UNION ALL
    SELECT 'gplevritis@imperialdade.com'      UNION ALL
    SELECT 'hdown@imperialdade.com'           UNION ALL
    SELECT 'jcondurso@imperialdade.com'       UNION ALL
    SELECT 'jennifer.abendanio@imperialdade.com' UNION ALL
    SELECT 'kevin.orr@imperialdade.com'       UNION ALL
    SELECT 'lmar@imperialdade.com'            UNION ALL
    SELECT 'mweaver@imperialdade.com'         UNION ALL
    SELECT 'patrick.fortin@imperialdade.com'  UNION ALL
    SELECT 'robert.hardy@imperialdade.com'    UNION ALL
    SELECT 'ron.fontaine@imperialdade.com'    UNION ALL
    SELECT 'shelley.miller@imperialdade.com'  UNION ALL
    SELECT 'smote@imperialdade.com'           UNION ALL
    SELECT 'steve.ainsworth@imperialdade.com' UNION ALL
    SELECT 'tom.smaldone@imperialdade.com'
)
SELECT  t.user_id,
        COUNT(DISTINCT e.item_id)                                      AS distinct_reports,
        COALESCE(SUM(CASE WHEN e.is_view = 1 THEN 1 ELSE 0 END), 0)    AS views,
        COALESCE(SUM(CASE WHEN e.activity = 'AnalyzedByExternalApplication'
                          THEN 1 ELSE 0 END), 0)                       AS excel_xmla,
        COUNT(DISTINCT e.activity_date)                                AS active_days,
        MAX(e.creation_time)                                           AS last_seen_utc
FROM    target_users AS t
LEFT JOIN fabricmanagement.report_activity_events AS e
       ON e.user_id = t.user_id
      AND e.item_id IS NOT NULL
GROUP BY t.user_id
ORDER BY views DESC, excel_xmla DESC, t.user_id;


-- 4. JUST THE LIST: distinct reports these users touched ---------------------
--    Any tracked activity counts (views AND Analyze in Excel), so nothing is
--    missed. DISTINCT is on item_id, not name, because a renamed report would
--    otherwise show up twice; the newest name wins.
SELECT  MAX(e.item_name)      AS report_name,
        MAX(e.workspace_name) AS workspace_name
FROM    fabricmanagement.report_activity_events AS e
WHERE   e.user_id IN (
            'afolabi.omole@imperialdade.com',    'amuranaka@imperialdade.com',
            'andrew.sellka@imperialdade.com',    'bhipps@imperialdade.com',
            'brian.hasek@imperialdade.com',      'bvilleda@imperialdade.com',
            'cody.tsuda@imperialdade.com',       'david.crawford@imperialdade.com',
            'david.dawe@imperialdade.com',       'dsoloway@imperialdade.com',
            'dwagner@imperialdade.com',          'fariha.islam@imperialdade.com',
            'gplevritis@imperialdade.com',       'hdown@imperialdade.com',
            'jcondurso@imperialdade.com',        'jennifer.abendanio@imperialdade.com',
            'kevin.orr@imperialdade.com',        'lmar@imperialdade.com',
            'mweaver@imperialdade.com',          'patrick.fortin@imperialdade.com',
            'robert.hardy@imperialdade.com',     'ron.fontaine@imperialdade.com',
            'shelley.miller@imperialdade.com',   'smote@imperialdade.com',
            'steve.ainsworth@imperialdade.com',  'tom.smaldone@imperialdade.com')
  AND   e.item_id IS NOT NULL
GROUP BY e.item_id
ORDER BY report_name;


-- 4b. Same list, with enough context to prioritise it -------------------------
SELECT  MAX(e.item_name)                                            AS report_name,
        MAX(e.workspace_name)                                       AS workspace_name,
        COUNT(DISTINCT e.user_id)                                   AS users_from_list,
        SUM(CASE WHEN e.is_view = 1 THEN 1 ELSE 0 END)              AS views,
        SUM(CASE WHEN e.activity = 'AnalyzedByExternalApplication'
                 THEN 1 ELSE 0 END)                                 AS excel_xmla,
        MAX(e.creation_time)                                        AS last_seen_utc
FROM    fabricmanagement.report_activity_events AS e
WHERE   e.user_id IN (
            'afolabi.omole@imperialdade.com',    'amuranaka@imperialdade.com',
            'andrew.sellka@imperialdade.com',    'bhipps@imperialdade.com',
            'brian.hasek@imperialdade.com',      'bvilleda@imperialdade.com',
            'cody.tsuda@imperialdade.com',       'david.crawford@imperialdade.com',
            'david.dawe@imperialdade.com',       'dsoloway@imperialdade.com',
            'dwagner@imperialdade.com',          'fariha.islam@imperialdade.com',
            'gplevritis@imperialdade.com',       'hdown@imperialdade.com',
            'jcondurso@imperialdade.com',        'jennifer.abendanio@imperialdade.com',
            'kevin.orr@imperialdade.com',        'lmar@imperialdade.com',
            'mweaver@imperialdade.com',          'patrick.fortin@imperialdade.com',
            'robert.hardy@imperialdade.com',     'ron.fontaine@imperialdade.com',
            'shelley.miller@imperialdade.com',   'smote@imperialdade.com',
            'steve.ainsworth@imperialdade.com',  'tom.smaldone@imperialdade.com')
  AND   e.item_id IS NOT NULL
GROUP BY e.item_id
ORDER BY views DESC, excel_xmla DESC;


-- 5. Raw event trail for one user --------------------------------------------
--    When you need to see the individual opens rather than a count.
SELECT  e.creation_time,
        e.activity,
        e.item_name AS report_name,
        e.workspace_name,
        e.consumption_method,
        e.distribution_method,
        e.client_ip
FROM    fabricmanagement.report_activity_events AS e
WHERE   e.user_id = 'brian.hasek@imperialdade.com'        -- <-- change me
ORDER BY e.creation_time DESC;
