"""Practice problems: prompt, expected output columns, hidden reference
solution, and the edge cases worth saying out loud in the interview."""

PROBLEMS = [
    {
        "id": 1,
        "title": "Flagged ads per advertiser",
        "prompt": (
            "For **every** advertiser, count how many of their ads have been flagged "
            "at least once. Advertisers with no flagged ads, including advertisers "
            "with no ads at all, should show `0`."
        ),
        "columns": ["advertiser_id", "flagged_ads"],
        "solution": """\
SELECT
  adv.advertiser_id,
  COUNT(DISTINCT f.ad_id) AS flagged_ads
FROM advertisers AS adv
LEFT JOIN ads AS a
  ON a.advertiser_id = adv.advertiser_id
LEFT JOIN policy_flags AS f
  ON f.ad_id = a.ad_id
GROUP BY adv.advertiser_id;""",
        "edge_cases": [
            "Advertisers with no ads must survive: start FROM advertisers and LEFT JOIN "
            "both tables. An INNER JOIN anywhere in the chain silently drops them.",
            "An ad flagged several times counts once: COUNT(DISTINCT f.ad_id), not "
            "COUNT(*) or COUNT(f.flag_id).",
            "COUNT(*) returns 1, not 0, for an advertiser with no ads, because the LEFT JOIN "
            "still produces one all-NULL row. Count a column from the right-hand table.",
        ],
    },
    {
        "id": 2,
        "title": "Top 3 advertisers per country (last 30 days)",
        "prompt": (
            "For each country, find the top 3 advertisers by number of policy flags in "
            "the last 30 days (`flag_ts` on or after `CURRENT_DATE - 30 days`).\n\n"
            "Include ties at the cutoff: if several advertisers tie for 3rd place, return "
            "all of them. Rank like a sports table: if two advertisers tie for 1st, the "
            "next one is 3rd. Advertisers with no flags in the window don't appear."
        ),
        "columns": ["country", "advertiser_id", "flag_count"],
        "solution": """\
WITH recent AS (
  SELECT
    adv.country,
    adv.advertiser_id,
    COUNT(*) AS flag_count
  FROM policy_flags AS f
  JOIN ads AS a
    ON a.ad_id = f.ad_id
  JOIN advertisers AS adv
    ON adv.advertiser_id = a.advertiser_id
  WHERE f.flag_ts >= CURRENT_DATE - INTERVAL 30 DAY
  GROUP BY adv.country, adv.advertiser_id
),
ranked AS (
  SELECT
    *,
    RANK() OVER (PARTITION BY country ORDER BY flag_count DESC) AS rnk
  FROM recent
)
SELECT country, advertiser_id, flag_count
FROM ranked
WHERE rnk <= 3;""",
        "edge_cases": [
            "RANK, not ROW_NUMBER (drops the advertiser tied at 3rd) and not DENSE_RANK "
            "(after a tie at 1st it keeps going to a 4th and 5th advertiser).",
            "Apply the 30-day filter in WHERE, before counting and ranking. Older flags "
            "change the ranking: in DE the two most-flagged advertisers overall have "
            "almost no recent flags.",
            "A country can return fewer than 3 rows (GB has only 2 advertisers with recent "
            "flags) or more than 3 (US, tie at 3rd).",
            "QUALIFY RANK() OVER (...) <= 3 is a shorter form in BigQuery and DuckDB.",
        ],
    },
    {
        "id": 3,
        "title": "Daily click spikes",
        "prompt": (
            "For each advertiser, compute clicks per day across all of their ads, the "
            "previous day's count, and a spike flag:\n\n"
            "`is_spike = 1` if `clicks > 3 × prev_clicks`, else `0`.\n\n"
            "Each advertiser's first day has `prev_clicks = NULL` and `is_spike = 0`."
        ),
        "columns": ["advertiser_id", "click_date", "clicks", "prev_clicks", "is_spike"],
        "solution": """\
WITH daily AS (
  SELECT
    a.advertiser_id,
    CAST(c.click_ts AS DATE) AS click_date,
    COUNT(*) AS clicks
  FROM clicks AS c
  JOIN ads AS a
    ON a.ad_id = c.ad_id
  GROUP BY a.advertiser_id, CAST(c.click_ts AS DATE)
),
with_prev AS (
  SELECT
    *,
    LAG(clicks) OVER (PARTITION BY advertiser_id ORDER BY click_date) AS prev_clicks
  FROM daily
)
SELECT
  advertiser_id,
  click_date,
  clicks,
  prev_clicks,
  CASE WHEN clicks > 3 * prev_clicks THEN 1 ELSE 0 END AS is_spike
FROM with_prev;""",
        "edge_cases": [
            "LAG returns the previous row, not the previous calendar day. This data has "
            "clicks every day, but in general check click_date - prev_date = 1 or build a "
            "date spine first so missing days count as 0.",
            "First day: prev_clicks is NULL, so clicks > 3 * NULL is NULL and the CASE falls "
            "through to ELSE 0. Say this out loud rather than relying on it silently.",
            "Strictly greater: exactly 3x is not a spike (advertiser 4 has one such day). "
            "Writing clicks > 3 * prev_clicks also avoids integer division and "
            "divide-by-zero, which clicks / prev_clicks > 3 can hit.",
            "Aggregate to advertiser + day first, then LAG. LAG over per-ad rows compares "
            "the wrong rows.",
        ],
    },
    {
        "id": 4,
        "title": "Shared payment methods: suspended ↔ active",
        "prompt": (
            "Find pairs of advertisers that share a payment method where one advertiser is "
            "**suspended** and the other is **active**. Return each pair once."
        ),
        "columns": ["suspended_id", "active_id", "payment_method_id"],
        "solution": """\
SELECT DISTINCT
  ps.advertiser_id AS suspended_id,
  pa.advertiser_id AS active_id,
  ps.payment_method_id
FROM payment_methods AS ps
JOIN advertisers AS s
  ON s.advertiser_id = ps.advertiser_id
 AND s.status = 'suspended'
JOIN payment_methods AS pa
  ON pa.payment_method_id = ps.payment_method_id
JOIN advertisers AS act
  ON act.advertiser_id = pa.advertiser_id
 AND act.status = 'active';""",
        "edge_cases": [
            "payment_methods can contain the same (advertiser, payment method) twice "
            "(advertiser 12 re-added a card), so the self-join returns the pair twice. "
            "Use DISTINCT or dedupe first.",
            "Orienting the join by status already returns each pair once. The generic "
            "a.id < b.id trick would wrongly drop pairs where the suspended id is the larger one.",
            "A payment method shared by 3 advertisers (1 suspended + 2 active) yields 2 pairs. "
            "Active+active and suspended+suspended sharing must not appear.",
            "If a pair shared two different payment methods, clarify whether you want one "
            "row per pair (aggregate the methods) or one per method.",
        ],
    },
    {
        "id": 5,
        "title": "Country risk rates",
        "prompt": (
            "For each country, compute:\n\n"
            "- `pct_suspended`: % of the country's advertisers that are suspended\n"
            "- `pct_ads_flagged`: % of the country's ads (ads of that country's advertisers) "
            "flagged at least once, at any time\n\n"
            "Both on a 0–100 scale, rounded to 1 decimal. Every country appears; countries "
            "whose advertisers have no ads show `0` for `pct_ads_flagged`."
        ),
        "columns": ["country", "pct_suspended", "pct_ads_flagged"],
        "solution": """\
WITH flagged_ads AS (
  SELECT DISTINCT ad_id
  FROM policy_flags
),
adv_stats AS (
  SELECT
    country,
    COUNT(*) AS n_advertisers,
    SUM(CASE WHEN status = 'suspended' THEN 1 ELSE 0 END) AS n_suspended
  FROM advertisers
  GROUP BY country
),
ad_stats AS (
  SELECT
    adv.country,
    COUNT(*) AS n_ads,
    COUNT(fa.ad_id) AS n_flagged
  FROM ads AS a
  JOIN advertisers AS adv
    ON adv.advertiser_id = a.advertiser_id
  LEFT JOIN flagged_ads AS fa
    ON fa.ad_id = a.ad_id
  GROUP BY adv.country
)
SELECT
  s.country,
  ROUND(100.0 * s.n_suspended / s.n_advertisers, 1) AS pct_suspended,
  COALESCE(ROUND(100.0 * d.n_flagged / NULLIF(d.n_ads, 0), 1), 0) AS pct_ads_flagged
FROM adv_stats AS s
LEFT JOIN ad_stats AS d
  ON d.country = s.country;""",
        "edge_cases": [
            "Two different grains: aggregate advertisers and ads separately, then join on "
            "country. Counting advertisers after joining to ads fans out: an advertiser with "
            "4 ads counts 4 times.",
            "Dedupe flags to one row per ad before joining. An ad flagged 8 times is still "
            "one flagged ad.",
            "Countries with no ads: LEFT JOIN from the advertiser side plus COALESCE(..., 0), "
            "and guard the denominator with NULLIF(n_ads, 0) or SAFE_DIVIDE.",
            "Integer division: 100 * 1 / 3 is 0 in Postgres or SQL Server, so use 100.0. "
            "BigQuery and DuckDB `/` already return a float.",
        ],
    },
    {
        "id": 6,
        "title": "Latest flag per ad",
        "prompt": (
            "For each ad that has at least one flag, return its most recent flag. If two "
            "flags on the same ad share the latest `flag_ts`, keep the one with the "
            "**lowest** `flag_id`."
        ),
        "columns": ["ad_id", "flag_id", "reason", "flag_ts"],
        "solution": """\
SELECT ad_id, flag_id, reason, flag_ts
FROM (
  SELECT
    f.*,
    ROW_NUMBER() OVER (
      PARTITION BY ad_id
      ORDER BY flag_ts DESC, flag_id ASC
    ) AS rn
  FROM policy_flags AS f
) AS t
WHERE rn = 1;""",
        "edge_cases": [
            "ROW_NUMBER needs a deterministic tie-breaker. RANK keeps both tied flags, and "
            "ROW_NUMBER without flag_id picks one arbitrarily (it can change between runs).",
            "Joining back to MAX(flag_ts) per ad returns both tied rows.",
            "MAX(flag_id) is not a shortcut: flag_id is assigned at ingestion, so it isn't "
            "time-ordered.",
            "Ads with no flags are excluded. If they were wanted, LEFT JOIN from ads.",
        ],
    },
    {
        "id": 7,
        "title": "Days to first flag",
        "prompt": (
            "For each advertiser with at least one flag on any of their ads, how many days "
            "passed between the advertiser's `created_at` and their first flag? Use whole "
            "calendar days: (date of first flag) − `created_at`."
        ),
        "columns": ["advertiser_id", "days_to_first_flag"],
        "solution": """\
SELECT
  adv.advertiser_id,
  DATE_DIFF('day', adv.created_at, CAST(MIN(f.flag_ts) AS DATE)) AS days_to_first_flag
FROM advertisers AS adv
JOIN ads AS a
  ON a.advertiser_id = adv.advertiser_id
JOIN policy_flags AS f
  ON f.ad_id = a.ad_id
GROUP BY adv.advertiser_id, adv.created_at;""",
        "edge_cases": [
            "Use the advertiser's created_at, not the ad's. The first flag is the MIN across "
            "all of the advertiser's ads.",
            "Timestamp vs date: convert flag_ts to a date before differencing. Dividing an "
            "hour difference by 24 and rounding can be off by one.",
            "INNER JOINs are deliberate here: advertisers with no flags are excluded, as the "
            "prompt asks.",
            "Argument order differs by dialect: DuckDB DATE_DIFF('day', start, end) vs "
            "BigQuery DATE_DIFF(end, start, DAY). Sanity-check the sign, and mention you'd "
            "check for flags before created_at (negative values).",
        ],
    },
]

PROBLEMS_BY_ID = {p["id"]: p for p in PROBLEMS}
