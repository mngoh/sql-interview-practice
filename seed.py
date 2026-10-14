"""Deterministic seed data for the SQL practice app (Google Ads abuse theme).

All dates are generated relative to DuckDB's CURRENT_DATE, so queries such as
"flags in the last 30 days" behave the same whenever the app is started. The
random seed is fixed, so the same day always produces the same data.

Edge cases deliberately planted (verify.py asserts every one of them):
  - advertisers with no ads (8 in US; 24 and 25, the whole of BR)
  - ads with zero flags, and advertiser 7 whose ads were never flagged
  - one ad flagged many times (advertiser 1's first ad)
  - a tie at 3rd place in last-30-day flag counts in US (advertisers 3 and 4)
  - a tie at 1st place in IN (so DENSE_RANK returns too many rows)
  - flags older than 30 days, including advertisers whose flags are all old
  - one ad with a NULL landing_domain
  - two flags on the same ad with the same timestamp, at that ad's latest flag
  - flag_id is assigned at ingestion, so it is not strictly time-ordered
  - payment methods shared by suspended+active, active+active and
    suspended+suspended advertisers, plus one duplicate payment_methods row
  - click spikes (> 3x the prior day), and one day at exactly 3x (not a spike)
  - every advertiser with ads has clicks on each of the last 14 days
"""

import random
from datetime import date, datetime, timedelta

import duckdb
import pandas as pd

SEED = 20261014

# table -> [(column, type, note)]; drives both the DDL and the schema panel.
TABLES = {
    "advertisers": [
        ("advertiser_id", "INTEGER", "primary key"),
        ("country", "TEXT", "2-letter country code"),
        ("created_at", "DATE", ""),
        ("status", "TEXT", "'active' | 'suspended'"),
    ],
    "ads": [
        ("ad_id", "INTEGER", "primary key"),
        ("advertiser_id", "INTEGER", "→ advertisers"),
        ("created_at", "DATE", ""),
        ("landing_domain", "TEXT", "nullable"),
    ],
    "policy_flags": [
        ("flag_id", "INTEGER", "primary key, assigned at ingestion"),
        ("ad_id", "INTEGER", "→ ads"),
        ("flag_ts", "TIMESTAMP", ""),
        ("reason", "TEXT", ""),
    ],
    "clicks": [
        ("click_id", "INTEGER", "primary key"),
        ("ad_id", "INTEGER", "→ ads"),
        ("click_ts", "TIMESTAMP", ""),
    ],
    "payment_methods": [
        ("advertiser_id", "INTEGER", "→ advertisers"),
        ("payment_method_id", "TEXT", "same id on two advertisers = shared card"),
        ("added_at", "DATE", ""),
    ],
}

TABLE_NOTES = {
    "advertisers": "One row per advertiser.",
    "ads": "One row per ad.",
    "policy_flags": "One row per flag. An ad can be flagged many times.",
    "clicks": "One row per click.",
    "payment_methods": "One row per (advertiser, payment method) addition.",
}

# advertiser_id -> (country, status)
ADVERTISERS = {
    1: ("US", "active"), 2: ("US", "active"), 3: ("US", "suspended"),
    4: ("US", "active"), 5: ("US", "active"), 6: ("US", "suspended"),
    7: ("US", "active"), 8: ("US", "active"),
    9: ("IN", "active"), 10: ("IN", "suspended"), 11: ("IN", "suspended"),
    12: ("IN", "active"), 13: ("IN", "active"), 14: ("IN", "suspended"),
    15: ("IN", "active"),
    16: ("DE", "active"), 17: ("DE", "active"), 18: ("DE", "active"),
    19: ("DE", "suspended"), 20: ("DE", "active"),
    21: ("GB", "active"), 22: ("GB", "suspended"), 23: ("GB", "active"),
    24: ("BR", "active"), 25: ("BR", "suspended"),
}

# advertiser_id -> number of ads. Missing advertisers (8, 24, 25) have none.
ADS_PER_ADVERTISER = {
    1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 3, 7: 2,
    9: 3, 10: 4, 11: 3, 12: 2, 13: 3, 14: 2, 15: 2,
    16: 3, 17: 3, 18: 2, 19: 3, 20: 3,
    21: 3, 22: 2, 23: 2,
}

# advertiser_id -> (flags in the last 30 days, flags older than 30 days).
# Recent flags land 1-27 days ago and old ones 35-95 days ago, so the answer
# doesn't depend on how the 30-day boundary is written.
FLAG_PLAN = {
    1: (12, 3), 2: (9, 2), 3: (7, 4), 4: (7, 1), 5: (4, 2), 6: (2, 5), 7: (0, 0),
    9: (8, 2), 10: (8, 3), 11: (6, 4), 12: (3, 1), 13: (3, 2), 14: (1, 3), 15: (0, 2),
    16: (6, 1), 17: (5, 2), 18: (3, 1), 19: (2, 8), 20: (0, 10),
    21: (4, 2), 22: (1, 3), 23: (0, 3),
}
RECENT_DAYS = (1, 27)
OLD_DAYS = (35, 95)

REASONS = ["malware", "phishing", "counterfeit", "misleading_claims",
           "unapproved_pharma", "cloaking"]
DOMAINS = ["shop-deals-now.example", "cheap-meds-online.example", "luxwatch-outlet.example",
           "crypto-double.example", "best-vpn-review.example", "free-giftcards.example",
           "loan-approved-fast.example", "weightloss-miracle.example", "ticket-resale.example",
           "travel-bargains.example", "garden-supply.example", "pet-food-direct.example"]

CLICK_DAYS = 14
# (advertiser_id, day index 0..13) -> multiplier on the previous day's clicks.
CLICK_SPIKES = {(2, 5): 4.0, (10, 9): 3.5, (17, 3): 5.0, (21, 11): 4.2}
CLICK_EXACT_3X = (4, 7)  # exactly 3x the prior day: NOT a spike (strictly greater)

# Shared payment methods: name -> advertisers who added it.
SHARED_PAYMENT_METHODS = {
    "susp_lt_active": [3, 12],       # suspended 3 + active 12 (suspended id < active id)
    "susp_gt_active": [14, 5],       # suspended 14 + active 5 (suspended id > active id)
    "three_way": [19, 17, 21],       # suspended 19 + active 17, 21; 17+21 is active+active
    "both_suspended": [6, 11],       # suspended + suspended: not a pair
    "both_active": [1, 2],           # active + active: not a pair
    "no_ads_suspended": [25, 23],    # suspended 25 (no ads, BR) + active 23
}
DUPLICATE_PAYMENT_ROW = ("susp_lt_active", 12)  # advertiser 12 re-added the same card


def _ts(rng, day):
    """Random timestamp within `day`."""
    return datetime(day.year, day.month, day.day) + timedelta(seconds=rng.randrange(86400))


def generate(today: date) -> dict:
    rng = random.Random(SEED)

    advertisers = []
    for adv_id, (country, status) in ADVERTISERS.items():
        created = today - timedelta(days=rng.randint(200, 900))
        advertisers.append((adv_id, country, created, status))
    adv_created = {a[0]: a[2] for a in advertisers}

    ads = []
    ads_by_adv = {}
    ad_id = 100
    for adv_id, n_ads in ADS_PER_ADVERTISER.items():
        for _ in range(n_ads):
            ad_id += 1
            earliest = adv_created[adv_id] + timedelta(days=1)
            latest = today - timedelta(days=100)  # before any flag or click
            created = earliest + timedelta(days=rng.randint(0, (latest - earliest).days))
            ads.append([ad_id, adv_id, created, rng.choice(DOMAINS)])
            ads_by_adv.setdefault(adv_id, []).append(ad_id)
    ads[ads_by_adv[9][1] - 101][3] = None  # the NULL landing_domain

    # Flags: per advertiser, spread over a subset of their ads. The first ad
    # always gets the most weight; usually at least one ad stays unflagged.
    flags = []  # [ad_id, ts, reason]
    for adv_id, (n_recent, n_old) in FLAG_PLAN.items():
        if n_recent + n_old == 0:
            continue
        adv_ads = ads_by_adv[adv_id]
        k = len(adv_ads) if adv_id == 10 else max(1, len(adv_ads) - 1)
        targets = adv_ads[:k]
        weights = [4] + [1] * (k - 1)
        for n, (lo, hi) in ((n_recent, RECENT_DAYS), (n_old, OLD_DAYS)):
            for _ in range(n):
                target = rng.choices(targets, weights)[0]
                day = today - timedelta(days=rng.randint(lo, hi))
                flags.append([target, _ts(rng, day), rng.choice(REASONS)])

    # Same-timestamp pair: copy the latest timestamp of advertiser 1's most
    # flagged ad onto another of its recent flags, with a different reason.
    heavy_ad = ads_by_adv[1][0]
    heavy = sorted((f for f in flags if f[0] == heavy_ad), key=lambda f: f[1])
    latest, twin = heavy[-1], heavy[-2]
    twin[1] = latest[1]
    twin[2] = next(r for r in REASONS if r != latest[2])

    # flag_id is assigned at ingestion (up to 3 days after the flag), so it is
    # not strictly time-ordered: MAX(flag_id) is not "latest flag".
    ingest = [(f[1] + timedelta(hours=rng.uniform(0, 72)), i) for i, f in enumerate(flags)]
    order = [i for _, i in sorted(ingest)]
    flag_rows = [(1000 + n, flags[i][0], flags[i][1], flags[i][2]) for n, i in enumerate(order, 1)]

    # Clicks: 6-14 per advertiser per day (max day-over-day ratio 14/6 < 3),
    # with planted spikes and one exact 3x day.
    clicks = []
    for adv_id, adv_ads in ads_by_adv.items():
        prev = None
        for d in range(CLICK_DAYS):
            n = rng.randint(6, 14)
            if (adv_id, d) in CLICK_SPIKES:
                n = int(prev * CLICK_SPIKES[(adv_id, d)]) + 1
            elif (adv_id, d) == CLICK_EXACT_3X:
                n = prev * 3
            day = today - timedelta(days=CLICK_DAYS - d)
            for _ in range(n):
                clicks.append((rng.choice(adv_ads), _ts(rng, day)))
            prev = n
    clicks.sort(key=lambda c: c[1])
    click_rows = [(5000 + n, ad, ts) for n, (ad, ts) in enumerate(clicks, 1)]

    def pm_id():
        return "pm_" + "".join(rng.choice("0123456789abcdef") for _ in range(8))

    def added(adv_id):
        start = adv_created[adv_id]
        return start + timedelta(days=rng.randint(0, (today - start).days - 1))

    payments = []
    for adv_id in ADVERTISERS:
        for _ in range(rng.choice([1, 1, 2])):
            payments.append((adv_id, pm_id(), added(adv_id)))
    shared_ids = {name: pm_id() for name in SHARED_PAYMENT_METHODS}
    for name, members in SHARED_PAYMENT_METHODS.items():
        for adv_id in members:
            payments.append((adv_id, shared_ids[name], added(adv_id)))
    name, adv_id = DUPLICATE_PAYMENT_ROW
    payments.append((adv_id, shared_ids[name], added(adv_id)))
    payments.sort(key=lambda p: (p[2], p[0]))

    cols = {t: [c for c, _, _ in spec] for t, spec in TABLES.items()}
    return {
        "advertisers": pd.DataFrame(advertisers, columns=cols["advertisers"]),
        "ads": pd.DataFrame(ads, columns=cols["ads"]),
        "policy_flags": pd.DataFrame(flag_rows, columns=cols["policy_flags"]),
        "clicks": pd.DataFrame(click_rows, columns=cols["clicks"]),
        "payment_methods": pd.DataFrame(payments, columns=cols["payment_methods"]),
    }


def add_compat_macros(con):
    """A couple of BigQuery-isms so GoogleSQL habits don't error out."""
    con.execute("CREATE OR REPLACE MACRO safe_divide(a, b) AS CASE WHEN b = 0 THEN NULL ELSE a / b END")
    con.execute("CREATE OR REPLACE MACRO date_sub(d, i) AS d - i")


def create_connection() -> duckdb.DuckDBPyConnection:
    """New in-memory DuckDB connection with every table created and seeded."""
    con = duckdb.connect(":memory:")
    today = con.execute("SELECT CURRENT_DATE").fetchone()[0]
    data = generate(today)
    for table, spec in TABLES.items():
        ddl = ", ".join(f"{col} {typ}" for col, typ, _ in spec)
        con.execute(f"CREATE TABLE {table} ({ddl})")
        con.register("_seed", data[table])
        con.execute(f"INSERT INTO {table} SELECT * FROM _seed")
        con.unregister("_seed")
    add_compat_macros(con)
    return con


if __name__ == "__main__":
    con = create_connection()
    print("CURRENT_DATE:", con.execute("SELECT CURRENT_DATE").fetchone()[0])
    for table in TABLES:
        print(f"{table:16} {con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>6} rows")
