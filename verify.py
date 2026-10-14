"""Self-check: python verify.py

1. Every planted edge case exists in the seeded data.
2. Every reference solution runs and returns rows.
3. The grader accepts alternate correct answers and rejects common mistakes.
"""

import sys

from app import grade, new_connection
from problems import PROBLEMS, PROBLEMS_BY_ID

con = new_connection()
failures = []


def one(sql):
    return con.execute(sql).fetchone()[0]


def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        failures.append(name)


print("Edge cases in the seed data")
check("an advertiser with no ads", one("""
    SELECT COUNT(*) FROM advertisers adv
    WHERE NOT EXISTS (SELECT 1 FROM ads a WHERE a.advertiser_id = adv.advertiser_id)""") >= 1)
check("ads with zero flags", one("""
    SELECT COUNT(*) FROM ads a
    WHERE NOT EXISTS (SELECT 1 FROM policy_flags f WHERE f.ad_id = a.ad_id)""") >= 1)
check("one ad flagged several times (>= 5)", one(
    "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM policy_flags GROUP BY ad_id)") >= 5)
check("tie at 3rd place in last-30-day flag counts within one country", one("""
    WITH c AS (
      SELECT adv.country, adv.advertiser_id, COUNT(*) n
      FROM policy_flags f JOIN ads a USING (ad_id) JOIN advertisers adv USING (advertiser_id)
      WHERE f.flag_ts >= CURRENT_DATE - INTERVAL 30 DAY GROUP BY ALL),
    r AS (SELECT *, RANK() OVER (PARTITION BY country ORDER BY n DESC) rk FROM c)
    SELECT COUNT(*) FROM (SELECT country FROM r WHERE rk = 3 GROUP BY country HAVING COUNT(*) > 1)""") >= 1)
check("a country whose advertisers have no ads", one("""
    SELECT COUNT(*) FROM (
      SELECT country FROM advertisers adv LEFT JOIN ads a USING (advertiser_id)
      GROUP BY country HAVING COUNT(a.ad_id) = 0)""") >= 1)
check("flags older than 30 days", one(
    "SELECT COUNT(*) FROM policy_flags WHERE flag_ts < CURRENT_DATE - INTERVAL 30 DAY") >= 1)
check("no flags near the 30-day boundary (days 28-34)", one("""
    SELECT COUNT(*) FROM policy_flags
    WHERE flag_ts >= CURRENT_DATE - INTERVAL 34 DAY AND flag_ts < CURRENT_DATE - INTERVAL 27 DAY""") == 0)
check("a NULL landing_domain", one("SELECT COUNT(*) FROM ads WHERE landing_domain IS NULL") >= 1)
check("two flags on the same ad with the same timestamp, at that ad's latest flag", one("""
    SELECT COUNT(*) FROM (
      SELECT ad_id, flag_ts, COUNT(*) n,
             flag_ts = MAX(flag_ts) OVER (PARTITION BY ad_id) AS is_latest
      FROM (SELECT ad_id, flag_ts FROM policy_flags) GROUP BY ad_id, flag_ts)
    WHERE n >= 2""") >= 1 and one("""
    SELECT COUNT(*) FROM policy_flags f
    WHERE flag_ts = (SELECT MAX(flag_ts) FROM policy_flags g WHERE g.ad_id = f.ad_id)
    GROUP BY ad_id ORDER BY 1 DESC LIMIT 1""") >= 2)
pairs = """
    SELECT pm.payment_method_id, sa.status s1, sb.status s2
    FROM payment_methods pm
    JOIN payment_methods pm2 ON pm2.payment_method_id = pm.payment_method_id
     AND pm2.advertiser_id > pm.advertiser_id
    JOIN advertisers sa ON sa.advertiser_id = pm.advertiser_id
    JOIN advertisers sb ON sb.advertiser_id = pm2.advertiser_id"""
check("payment method shared by a suspended+active pair", one(
    f"SELECT COUNT(*) FROM ({pairs}) WHERE s1 <> s2") >= 1)
check("payment method shared by an active+active pair", one(
    f"SELECT COUNT(*) FROM ({pairs}) WHERE s1 = 'active' AND s2 = 'active'") >= 1)
daily = """
    WITH d AS (SELECT a.advertiser_id, CAST(click_ts AS DATE) dt, COUNT(*) n
               FROM clicks c JOIN ads a USING (ad_id) GROUP BY ALL)
    SELECT *, LAG(n) OVER (PARTITION BY advertiser_id ORDER BY dt) prev FROM d"""
check("at least 3 click spikes (> 3x prior day)", one(
    f"SELECT COUNT(*) FROM ({daily}) WHERE n > 3 * prev") >= 3)
check("every advertiser with ads has clicks on each of the 14 days", one("""
    SELECT COUNT(*) FROM (SELECT DISTINCT advertiser_id FROM ads) adv
    CROSS JOIN range(1, 15) AS r(k)
    WHERE NOT EXISTS (
      SELECT 1 FROM clicks c JOIN ads a USING (ad_id)
      WHERE a.advertiser_id = adv.advertiser_id
        AND CAST(c.click_ts AS DATE) = CURRENT_DATE - CAST(r.k AS INTEGER))""") == 0)

print("\nReference solutions")
for p in PROBLEMS:
    n = len(con.execute(p["solution"]).fetchall())
    check(f"P{p['id']} {p['title']}: {n} rows", n > 0)

# Alternate formulations that must be graded correct, and common mistakes
# that must be graded wrong.
CORRECT = {
    1: ["""SELECT adv.advertiser_id AS id,
               COALESCE(SUM(CASE WHEN EXISTS (SELECT 1 FROM policy_flags f WHERE f.ad_id = a.ad_id)
                                 THEN 1 END), 0) AS n
        FROM advertisers adv LEFT JOIN ads a ON a.advertiser_id = adv.advertiser_id
        GROUP BY 1 ORDER BY n DESC"""],
    2: ["""SELECT adv.country, adv.advertiser_id, COUNT(*) AS flag_count
        FROM policy_flags f JOIN ads a USING (ad_id) JOIN advertisers adv USING (advertiser_id)
        WHERE DATE(f.flag_ts) >= CURRENT_DATE - 30
        GROUP BY adv.country, adv.advertiser_id QUALIFY RANK() OVER (PARTITION BY country ORDER BY COUNT(*) DESC) <= 3"""],
    3: ["""WITH d AS (SELECT a.advertiser_id, DATE_TRUNC('day', click_ts) AS click_date,
                            COUNT(*) AS clicks
                     FROM clicks JOIN ads a USING (ad_id) GROUP BY ALL)
        SELECT advertiser_id, click_date, clicks,
               LAG(clicks) OVER w AS prev_clicks,
               IF(clicks > 3 * LAG(clicks) OVER w, 1, 0) AS is_spike
        FROM d WINDOW w AS (PARTITION BY advertiser_id ORDER BY click_date)"""],
    4: ["""SELECT s.advertiser_id, a.advertiser_id, s.payment_method_id
        FROM (SELECT DISTINCT p.* EXCLUDE (added_at) FROM payment_methods p
              JOIN advertisers x USING (advertiser_id) WHERE x.status = 'suspended') s
        JOIN (SELECT DISTINCT p.* EXCLUDE (added_at) FROM payment_methods p
              JOIN advertisers x USING (advertiser_id) WHERE x.status = 'active') a
          USING (payment_method_id)"""],
    5: ["""SELECT adv.country,
               ROUND(100 * AVG(CASE WHEN status = 'suspended' THEN 1.0 ELSE 0 END), 1),
               COALESCE((SELECT ROUND(100.0 * COUNT(DISTINCT f.ad_id) / NULLIF(COUNT(DISTINCT a.ad_id), 0), 1)
                         FROM ads a JOIN advertisers x USING (advertiser_id)
                         LEFT JOIN policy_flags f USING (ad_id)
                         WHERE x.country = adv.country), 0)
        FROM advertisers adv GROUP BY adv.country"""],
    6: ["""SELECT ad_id, flag_id, reason, flag_ts FROM policy_flags
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ad_id ORDER BY flag_ts DESC, flag_id) = 1"""],
    7: ["""SELECT advertiser_id, MIN(CAST(f.flag_ts AS DATE)) - adv.created_at
        FROM advertisers adv JOIN ads a USING (advertiser_id) JOIN policy_flags f USING (ad_id)
        GROUP BY advertiser_id, adv.created_at"""],
}
WRONG = {
    1: [("COUNT(*) instead of DISTINCT ad", """
        SELECT adv.advertiser_id, COUNT(*) FROM advertisers adv
        LEFT JOIN ads a USING (advertiser_id) LEFT JOIN policy_flags f USING (ad_id) GROUP BY 1"""),
        ("INNER JOIN drops advertisers", """
        SELECT a.advertiser_id, COUNT(DISTINCT f.ad_id) FROM ads a
        JOIN policy_flags f USING (ad_id) GROUP BY 1""")],
    2: [("ROW_NUMBER drops the tie", PROBLEMS_BY_ID[2]["solution"].replace("RANK()", "ROW_NUMBER()")),
        ("DENSE_RANK keeps too many", PROBLEMS_BY_ID[2]["solution"].replace("RANK()", "DENSE_RANK()")),
        ("no 30-day filter", PROBLEMS_BY_ID[2]["solution"].replace(
            "WHERE f.flag_ts >= CURRENT_DATE - INTERVAL 30 DAY", ""))],
    3: [(">= instead of >", PROBLEMS_BY_ID[3]["solution"].replace(
            "clicks > 3 * prev_clicks", "clicks >= 3 * prev_clicks")),
        ("per ad instead of per advertiser", """
        WITH d AS (SELECT ad_id AS advertiser_id, CAST(click_ts AS DATE) click_date, COUNT(*) clicks
                   FROM clicks GROUP BY ALL)
        SELECT *, LAG(clicks) OVER (PARTITION BY advertiser_id ORDER BY click_date),
               0 FROM d""")],
    4: [("missing DISTINCT", PROBLEMS_BY_ID[4]["solution"].replace("SELECT DISTINCT", "SELECT")),
        ("id < id trick drops pairs", """
        SELECT DISTINCT p1.advertiser_id, p2.advertiser_id, p1.payment_method_id
        FROM payment_methods p1 JOIN payment_methods p2
          ON p1.payment_method_id = p2.payment_method_id AND p1.advertiser_id < p2.advertiser_id
        JOIN advertisers a1 ON a1.advertiser_id = p1.advertiser_id
        JOIN advertisers a2 ON a2.advertiser_id = p2.advertiser_id
        WHERE a1.status = 'suspended' AND a2.status = 'active'""")],
    5: [("fan-out when counting advertisers after joining ads", """
        SELECT adv.country,
               ROUND(100.0 * SUM(CASE WHEN status = 'suspended' THEN 1 ELSE 0 END) / COUNT(*), 1),
               COALESCE(ROUND(100.0 * COUNT(DISTINCT f.ad_id) / COUNT(DISTINCT a.ad_id), 1), 0)
        FROM advertisers adv LEFT JOIN ads a USING (advertiser_id)
        LEFT JOIN policy_flags f USING (ad_id) GROUP BY 1"""),
        ("countries without ads dropped", PROBLEMS_BY_ID[5]["solution"].replace(
            "LEFT JOIN ad_stats", "JOIN ad_stats"))],
    6: [("tie broken by highest flag_id", PROBLEMS_BY_ID[6]["solution"].replace(
            "flag_id ASC", "flag_id DESC")),
        ("join back to MAX(flag_ts) keeps both tied rows", """
        SELECT f.ad_id, f.flag_id, f.reason, f.flag_ts FROM policy_flags f
        JOIN (SELECT ad_id, MAX(flag_ts) m FROM policy_flags GROUP BY 1) x
          ON x.ad_id = f.ad_id AND x.m = f.flag_ts"""),
        ("MAX(flag_id) as latest", """
        SELECT ad_id, flag_id, reason, flag_ts FROM policy_flags
        WHERE flag_id IN (SELECT MAX(flag_id) FROM policy_flags GROUP BY ad_id)""")],
    7: [("ad created_at instead of advertiser", """
        SELECT a.advertiser_id, DATE_DIFF('day', MIN(a.created_at), CAST(MIN(f.flag_ts) AS DATE))
        FROM ads a JOIN policy_flags f USING (ad_id) GROUP BY 1"""),
        ("rounded hours / 24", """
        SELECT adv.advertiser_id,
               ROUND(DATE_DIFF('hour', adv.created_at, MIN(f.flag_ts)) / 24.0)
        FROM advertisers adv JOIN ads a USING (advertiser_id) JOIN policy_flags f USING (ad_id)
        GROUP BY adv.advertiser_id, adv.created_at""")],
}

print("\nGrader")
for pid, queries in CORRECT.items():
    for sql in queries:
        res = grade(con, sql, PROBLEMS_BY_ID[pid])
        check(f"P{pid} alternate answer graded correct", res.get("correct") is True)
for pid, cases in WRONG.items():
    for name, sql in cases:
        res = grade(con, sql, PROBLEMS_BY_ID[pid])
        check(f"P{pid} wrong ({name}) graded incorrect", "error" not in res and res["correct"] is False)
res = grade(con, "SELEC 1", PROBLEMS_BY_ID[1])
check("syntax error reported", "error" in res and "syntax" in res["error"].lower())
res = grade(con, "DELETE FROM ads", PROBLEMS_BY_ID[1])
check("DELETE is rolled back", res.get("correct") is False and one("SELECT COUNT(*) FROM ads") == 60)
res = grade(con, "DROP TABLE ads", PROBLEMS_BY_ID[1])
check("DROP TABLE is rolled back", one("SELECT COUNT(*) FROM ads") == 60)
res = grade(con, "SELECT SUM(a.click_id + b.click_id + c.click_id) FROM clicks a, clicks b, clicks c", PROBLEMS_BY_ID[1])
check("runaway query cancelled by timeout", "error" in res and "cancelled" in res["error"])
res = grade(con, "SELECT * FROM read_csv('/etc/passwd')", PROBLEMS_BY_ID[1])
check("file access blocked", "error" in res)

print("\nApp (headless)")
from streamlit.testing.v1 import AppTest  # noqa: E402

at = AppTest.from_file("app.py", default_timeout=30).run()
check("app renders without exceptions", not at.exception)
for p in PROBLEMS:
    pid = p["id"]
    at.button(key=f"nav_{pid}").click().run()
    at.text_area(key=f"sql_{pid}").input(p["solution"]).run()
    at.button(key=f"check_{pid}").click().run()
    check(f"P{pid} reference pasted into the UI is graded correct",
          not at.exception and at.session_state.status[pid] == "correct")
at.button(key="reveal_7").click().run()
check("solution shown after a check", any("DATE_DIFF" in c.value for c in at.code))

print()
if failures:
    print(f"{len(failures)} check(s) failed")
    sys.exit(1)
print("All checks passed")
