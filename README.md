# SQL interview practice

A local practice app for SQL interview rounds where you write queries by hand (a shared doc,
no running code). Write the query on paper or in a doc first, then paste it here: the app runs
it against a seeded in-memory DuckDB database themed on Google Ads abuse, compares the result
with a hidden reference solution (columns by position, row order ignored, floats rounded to 4
decimals, NULL equals NULL) and shows which rows are missing or extra. There are 7 problems
covering anti-joins, top-N with ties, LAG, self-joins, two-grain ratios, dedup tie-breaks and
date math, each with the edge cases worth saying out loud. A 12-minute per-problem timer starts
on your first keystroke, and a second tab has Python and pandas flashcards.

## Schema

```sql
advertisers     (advertiser_id INT, country TEXT, created_at DATE, status TEXT)  -- 'active' | 'suspended'
ads             (ad_id INT, advertiser_id INT, created_at DATE, landing_domain TEXT)
policy_flags    (flag_id INT, ad_id INT, flag_ts TIMESTAMP, reason TEXT)         -- one row per flag
clicks          (click_id INT, ad_id INT, click_ts TIMESTAMP)                    -- one row per click
payment_methods (advertiser_id INT, payment_method_id TEXT, added_at DATE)
```

Data is generated with a fixed random seed relative to `CURRENT_DATE`, so "last 30 days" queries
always work: 25 advertisers in 5 countries, 60 ads, 155 flags and 14 days of clicks. Edge cases are
planted on purpose (advertisers and a whole country with no ads, a tie at 3rd place, flags older
than 30 days, a NULL domain, same-timestamp flags, shared payment methods, click spikes); see the
docstring in `seed.py`.

## Run locally

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

`python verify.py` checks that every planted edge case exists, that every reference solution
returns rows, and that the grader accepts alternate correct answers and rejects common mistakes.

## Files

| File | What it holds |
|---|---|
| `app.py` | Streamlit UI and the grader |
| `seed.py` | Schema and deterministic data generation |
| `problems.py` | Problems, reference solutions, edge cases |
| `verify.py` | Self-check for data, solutions, grader and UI |

## Deploy (Streamlit Community Cloud)

`requirements.txt` is at the repo root and `app.py` is the entry point. On
[share.streamlit.io](https://share.streamlit.io), click **Create app**, pick this repo, branch
`main`, main file `app.py`, and set Python 3.12 under **Advanced settings**.

## Screenshot

<!-- Save a screenshot as docs/screenshot.png and replace this comment with:
![App screenshot](docs/screenshot.png) -->
_Screenshot placeholder._
