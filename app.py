"""SQL interview practice: write a query by hand, paste it in, check it
against a hidden reference solution. Run with: streamlit run app.py"""

import math
import threading
import uuid
from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal

import duckdb
import pandas as pd
import streamlit as st

import seed
from problems import PROBLEMS, PROBLEMS_BY_ID

TIMER_MINUTES = 12
MAX_ROWS = 100_000
QUERY_TIMEOUT_S = 10
SHOW_ROWS = 10
STATUS_ICON = {"not tried": "⬜", "wrong": "❌", "correct": "✅"}


# --------------------------------------------------------------------------
# Running and grading queries
# --------------------------------------------------------------------------

class QueryError(Exception):
    pass


def new_connection():
    """Seeded connection, locked down because it runs whatever SQL is pasted in."""
    con = seed.create_connection()
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def run_query(con, sql):
    """Run sql inside a transaction that is always rolled back, so a stray
    DELETE or DROP can't damage the seeded data. Returns (columns, rows)."""
    timer = threading.Timer(QUERY_TIMEOUT_S, con.interrupt)
    con.begin()
    timer.start()
    try:
        con.execute(sql)
        if con.description is None:
            raise QueryError("The statement ran but returned no result set. Write a SELECT.")
        cols = [d[0] for d in con.description]
        rows = con.fetchmany(MAX_ROWS + 1)
        return cols, rows
    except duckdb.InterruptException:
        raise QueryError(f"Query cancelled after {QUERY_TIMEOUT_S}s. Is a join exploding?")
    finally:
        timer.cancel()
        try:
            con.rollback()
        except duckdb.Error:
            pass  # the query itself ended the transaction


def normalize(v):
    """Comparable form of one cell: NULL/NaN -> None, numbers rounded to 4
    decimals (ints stay ints), midnight timestamps -> dates, dates -> ISO."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, Decimal)):
        x = round(float(v), 4)
        return int(x) if x.is_integer() else x
    if isinstance(v, datetime):
        if v.tzinfo is None and v.time() == time(0):
            return v.date().isoformat()
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return v
    return repr(v)


def normalize_row(row):
    return tuple(normalize(v) for v in row)


def _sort_key(row):
    return tuple((v is None, v if v is not None else 0) for v in row)


def compare(cols, rows, ref_cols, ref_rows):
    """Compare two results by column position, ignoring row order and column
    names. Rows are compared as multisets, so duplicates matter."""
    res = {
        "user_cols": cols,
        "ref_cols": ref_cols,
        "n_user": len(rows),
        "n_expected": len(ref_rows),
        "preview": [normalize_row(r) for r in rows[:50]],
        "missing": [], "extra": [], "n_missing": 0, "n_extra": 0,
    }
    if len(rows) > MAX_ROWS:
        res.update(correct=False, too_many=True,
                   problem=f"Your query returned more than {MAX_ROWS:,} rows. "
                           "That's usually a join fan-out.")
        return res
    if len(cols) != len(ref_cols):
        res.update(correct=False,
                   problem=f"You returned {len(cols)} columns; expected {len(ref_cols)}: "
                           f"{', '.join(ref_cols)}.")
        return res

    got = Counter(normalize_row(r) for r in rows)
    want = Counter(normalize_row(r) for r in ref_rows)
    missing = sorted((want - got).elements(), key=_sort_key)
    extra = sorted((got - want).elements(), key=_sort_key)
    res.update(correct=not missing and not extra,
               missing=missing[:SHOW_ROWS], extra=extra[:SHOW_ROWS],
               n_missing=len(missing), n_extra=len(extra))

    if (missing or extra) and set(got) == set(want):
        res["hint"] = ("Same distinct rows, different duplicates. "
                       "Look for a join fan-out or a missing DISTINCT.")
    elif (missing or extra) and [c.lower() for c in cols] != [c.lower() for c in ref_cols] \
            and sorted(c.lower() for c in cols) == sorted(c.lower() for c in ref_cols):
        res["hint"] = ("Columns are compared by position. Expected order: "
                       f"{', '.join(ref_cols)}.")
    return res


def grade(con, sql, problem):
    try:
        cols, rows = run_query(con, sql)
    except Exception as exc:  # duckdb errors and QueryError alike
        return {"error": str(exc)}
    ref_cols, ref_rows = run_query(con, problem["solution"])
    return compare(cols, rows, ref_cols, ref_rows)


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

CSS = """
<style>
[class*="st-key-sql_"] textarea {
  font-family: "Source Code Pro", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
  font-size: 14px !important;
  line-height: 1.5 !important;
}
[class*="st-key-nav_"] button { justify-content: flex-start; }
[class*="st-key-nav_"] button div { justify-content: flex-start; text-align: left; }
</style>
"""

# Timer lives in the browser so it ticks without reruns. It watches this
# problem's text area and starts when its contents first change. State is
# keyed by Streamlit session, so a page reload resets timers along with drafts.
TIMER_HTML = """
<div id="wrap">
  <span id="clock">__LIMIT__:00</span>
  <span id="note">starts on first keystroke</span>
  <a id="reset" href="#" title="Restart the timer on your next keystroke">reset</a>
  <div id="bar"><div id="fill"></div></div>
</div>
<style>
  html, body { margin: 0; background: transparent; overflow: hidden; }
  #wrap { font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 6px 2px 0; }
  #clock { font: 600 20px ui-monospace, SFMono-Regular, Menlo, monospace; margin-right: 6px; }
  #note { opacity: .7; }
  #reset { margin-left: 8px; font-size: 12px; opacity: .6; color: inherit; }
  #bar { height: 3px; margin-top: 4px; background: rgba(128,128,128,.25); border-radius: 2px; }
  #fill { height: 100%; width: 0; background: currentColor; border-radius: 2px; opacity: .6; }
  .over #clock, .over #note { color: #e5484d; }
  .over #fill { background: #e5484d; opacity: 1; }
</style>
<script>
  const PID = "__PID__", LIMIT = __LIMIT__ * 60, KEY = "sql-practice-timer-__SESSION__-" + PID;
  const $ = id => document.getElementById(id);
  let doc = null, store = null;
  try { doc = window.parent.document; store = window.parent.sessionStorage; } catch (e) {}
  const mem = {};
  const get = k => store ? store.getItem(k) : mem[k];
  const set = (k, v) => store ? store.setItem(k, v) : (mem[k] = v);
  const del = k => store ? store.removeItem(k) : delete mem[k];
  let baseline = "";
  const area = () => doc && doc.querySelector(".st-key-sql_" + PID + " textarea");
  const fmt = s => Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");

  function tick() {
    try {
      const app = doc && doc.querySelector(".stApp");
      if (app) document.body.style.color = getComputedStyle(app).color;
    } catch (e) {}
    let start = get(KEY);
    const ta = area();
    if (!start && ta && ta.value.length && ta.value !== baseline) {
      start = String(Date.now());
      set(KEY, start);
    }
    const wrap = $("wrap");
    if (!start) {
      $("clock").textContent = fmt(LIMIT);
      $("note").textContent = "starts on first keystroke";
      $("fill").style.width = "0";
      wrap.classList.remove("over");
      return;
    }
    const elapsed = Math.floor((Date.now() - Number(start)) / 1000);
    const left = LIMIT - elapsed;
    $("clock").textContent = left >= 0 ? fmt(left) : "+" + fmt(-left);
    $("note").textContent = left >= 0 ? "left" : "over time";
    $("fill").style.width = Math.min(100, 100 * elapsed / LIMIT) + "%";
    wrap.classList.toggle("over", left < 0);
  }
  $("reset").onclick = e => {
    e.preventDefault();
    const ta = area();
    baseline = ta ? ta.value : "";
    del(KEY);
    tick();
  };
  tick();
  setInterval(tick, 250);
</script>
"""

DIALECT_NOTES = """
This app runs **DuckDB**, which is close to Postgres and accepts many BigQuery habits:
`QUALIFY`, `COUNTIF`, `IF()`, `IFNULL`, `DATE(ts)`, `CURRENT_DATE()`, and (added here)
`SAFE_DIVIDE(a, b)` and `DATE_SUB(d, INTERVAL n DAY)`.

| | DuckDB (here) | BigQuery |
|---|---|---|
| Days between | `DATE_DIFF('day', start, end)` or `end_date - start_date` | `DATE_DIFF(end, start, DAY)` |
| 30 days ago | `CURRENT_DATE - INTERVAL 30 DAY` | `DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)` |
| Timestamp → date | `CAST(ts AS DATE)` or `DATE(ts)` | `DATE(ts)` |
| Division | `/` is float, `//` is integer | `/` is float, `DIV()` is integer |
| Seconds between | `DATE_DIFF('second', a, b)` | `TIMESTAMP_DIFF(b, a, SECOND)` |
"""

FLASHCARDS = [
    (
        "List vs tuple: what's the difference, and when do you use each?",
        """
- A **list** is mutable (append, sort in place). A **tuple** is immutable.
- Tuples are **hashable** when their contents are, so they can be dict keys or set members:
  `counts[(country, day)] += 1`. Lists can't.
- Tuples are slightly smaller and faster to create. Use them for fixed records (a row, a
  coordinate, multiple return values) and lists for collections that grow.
- Gotcha: a tuple containing a list is immutable at the top level, but the list inside can
  still change, and the tuple is then unhashable.
""",
    ),
    (
        "What does a dict lookup cost? And `x in list` vs `x in set`?",
        """
- A dict is a hash table: lookup, insert and delete are **O(1) on average** (key → hash →
  bucket). The worst case is O(n) with pathological collisions, which is rare.
- `x in list` is an **O(n)** scan; `x in set` and `x in dict` are O(1) on average.
- Classic fix for slow code: build the set once, outside the loop. Checking n items against
  a list of m goes from O(n·m) to O(n + m).
- Keys must be hashable (str, int, tuple), so not list or dict. Dicts keep insertion order
  (Python 3.7+).
""",
    ),
    (
        "pandas `merge` vs `concat` (vs `join`)?",
        """
- `pd.merge(left, right, on="ad_id", how="left")` is a **SQL JOIN**: it matches rows on
  key columns. `how` = inner / left / right / outer / cross.
- `pd.concat([df1, df2])` **stacks** frames: `axis=0` appends rows (UNION ALL, aligned by
  column name); `axis=1` puts columns side by side, aligned on the index.
- `df.join(other)` is a merge on the index.
- Gotchas: duplicate keys on both sides multiply rows (the same fan-out as SQL). Guard with
  `validate="many_to_one"`. `indicator=True` adds a `_merge` column, so an anti-join is
  `_merge == "left_only"`. `concat` doesn't dedupe: UNION needs `.drop_duplicates()`.
- Unlike SQL, `merge` matches NaN keys to each other.
""",
    ),
    (
        "How does `groupby` work? How do you write COUNT(DISTINCT) and SUM() OVER (PARTITION BY)?",
        """
Split-apply-combine: split rows by key, apply an aggregation to each group, combine the
results.

```python
(df.groupby("country", as_index=False)
   .agg(n_ads=("ad_id", "nunique"), n_flags=("flag_id", "count")))
```

- `nunique` = COUNT(DISTINCT). `size()` counts rows including NaN; `count()` counts non-NaN
  values, like COUNT(col).
- `transform` returns a result the same length as the input, which makes it a window function:
  `df["adv_total"] = df.groupby("advertiser_id")["clicks"].transform("sum")`.
- NaN group keys are **dropped by default**: pass `dropna=False` to keep them (SQL keeps a
  NULL group).
- `.filter(lambda g: ...)` is roughly HAVING.
""",
    ),
    (
        "How does pandas handle missing values? Why does `df[df.x == np.nan]` return nothing?",
        """
- **NaN != NaN** (an IEEE float rule), so equality checks never match. Use `df.x.isna()` and
  `df.x.notna()`.
- Aggregations skip NaN: `mean()` ignores it (like SQL AVG ignores NULL), `count()` excludes
  it, and `sum()` of an all-NaN column is 0, not NaN.
- A NaN in an int column upcasts it to float unless you use a nullable dtype like `"Int64"`.
- Be explicit when cleaning: `fillna(0)` vs `dropna(subset=["col"])` answer different questions.
- Differences from SQL NULL: `merge` matches NaN keys to each other, and `groupby` drops NaN
  keys unless `dropna=False`.
""",
    ),
    (
        "`apply` vs vectorized operations: why is `apply` slow?",
        """
`df.apply(f, axis=1)` calls a Python function **once per row**: an interpreted loop that is
often 10–100× slower. Vectorized operations (`df.a * df.b`, `np.where`, `.str`, `.dt`,
`np.select`) run compiled loops over whole columns.

```python
df["is_spike"] = np.where(df.clicks > 3 * df.prev_clicks, 1, 0)                 # fast
df["is_spike"] = df.apply(lambda r: int(r.clicks > 3 * r.prev_clicks), axis=1)  # slow
```

Use `apply` only when the logic can't be vectorized or the data is small. `.map(some_dict)`
is fine for lookups.
""",
    ),
    (
        "What is a generator, and why use one?",
        """
A function with `yield` (or a generator expression `(x for x in ...)`) produces values
**lazily**, one at a time, and keeps only its current state in memory.

- O(1) memory: stream a 50 GB log line by line:
  `sum(1 for line in open(path) if "ERROR" in line)`.
- Single pass: once exhausted it's empty. Wrap it in `list()` if you need to iterate twice.
- Composable pipelines: read → filter → transform without building intermediate lists.
- `range`, `map`, `zip` and file objects are lazy iterators too.
""",
    ),
    (
        "The data doesn't fit in memory. What do you do?",
        """
1. **Push the work to where the data lives**: aggregate in SQL or BigQuery and pull only
   the result.
2. **Read less**: only the columns you need (`usecols`), filter rows early, and prefer Parquet
   (columnar, compressed) over CSV.
3. **Shrink dtypes**: `category` for low-cardinality strings, `int32` / `float32`.
4. **Chunk it**: `pd.read_csv(path, chunksize=1_000_000)` and combine partial aggregates
   (sum of sums; for an average keep sum and count, never average the averages).
5. **Out-of-core engines**: DuckDB or Polars (lazy / streaming) query files larger than RAM.
6. **Distribute**: Spark, Beam or Dask when one machine isn't enough.
7. **Sample** to explore, but compute final metrics on the full data.

Say which you'd try first and why. Usually: push it to SQL.
""",
    ),
    (
        "pandas equivalents of LAG, RANK and ROW_NUMBER?",
        """
```python
df = df.sort_values(["advertiser_id", "click_date"])
df["prev_clicks"] = df.groupby("advertiser_id")["clicks"].shift(1)          # LAG
df["rnk"] = (df.groupby("country")["flag_count"]
               .rank(method="min", ascending=False))                         # RANK
df["rn"] = df.groupby("ad_id").cumcount() + 1                                # ROW_NUMBER
```

`rank(method="dense")` is DENSE_RANK; `method="first"` behaves like ROW_NUMBER.
""",
    ),
]


def init_state():
    ss = st.session_state
    if "con" not in ss:
        ss.con = new_connection()
        ss.session_id = uuid.uuid4().hex[:8]  # timers reset with the session, like drafts
    ss.setdefault("current", PROBLEMS[0]["id"])
    ss.setdefault("status", {p["id"]: "not tried" for p in PROBLEMS})
    ss.setdefault("checks", {p["id"]: 0 for p in PROBLEMS})
    ss.setdefault("results", {})
    ss.setdefault("revealed", set())
    ss.setdefault("previews", [])
    # Streamlit drops the state of widgets that aren't rendered in a run;
    # re-assigning keeps drafts of the problems that aren't on screen.
    for p in PROBLEMS:
        key = f"sql_{p['id']}"
        if key in ss:
            ss[key] = ss[key]


def on_select(pid):
    st.session_state.current = pid


def on_check(pid):
    ss = st.session_state
    sql = ss.get(f"sql_{pid}", "").strip()
    if not sql:
        ss.results[pid] = {"empty": True}
        return
    res = grade(ss.con, sql, PROBLEMS_BY_ID[pid])
    ss.results[pid] = res
    ss.checks[pid] += 1
    ss.status[pid] = "correct" if res.get("correct") else "wrong"


def on_reveal(pid):
    st.session_state.revealed.add(pid)


def on_toggle_preview(table):
    previews = st.session_state.previews
    if table in previews:
        previews.remove(table)
    else:
        previews.append(table)


def on_reset_db():
    ss = st.session_state
    ss.con.close()
    ss.con = new_connection()


def frame(rows, cols):
    """DataFrame for display; tolerates duplicate column names."""
    seen = Counter()
    unique = []
    for c in cols:
        seen[c] += 1
        unique.append(c if seen[c] == 1 else f"{c} ({seen[c]})")
    return pd.DataFrame(rows, columns=unique)


def render_sidebar():
    ss = st.session_state
    with st.sidebar:
        st.header("Problems")
        for p in PROBLEMS:
            pid = p["id"]
            st.button(
                f"{STATUS_ICON[ss.status[pid]]}  {pid}. {p['title']}",
                key=f"nav_{pid}",
                on_click=on_select,
                args=(pid,),
                type="primary" if pid == ss.current else "secondary",
                width="stretch",
            )
        solved = sum(s == "correct" for s in ss.status.values())
        st.caption(f"{solved} / {len(PROBLEMS)} correct · ⬜ not tried · ❌ wrong · ✅ correct")
        st.divider()
        today = ss.con.execute("SELECT CURRENT_DATE").fetchone()[0]
        st.caption(f"Data is seeded relative to CURRENT_DATE ({today}). "
                   "Your queries run in a rolled-back transaction.")
        st.button("Reset database", on_click=on_reset_db,
                  help="Rebuild the seeded tables (only needed if a query COMMITted changes).")


def render_schema(con):
    with st.expander("Schema", expanded=False):
        st.code(seed.schema_text(), language="sql")

        st.caption("Preview 10 rows:")
        previews = st.session_state.previews
        cols = st.columns(len(seed.TABLES))
        for col, table in zip(cols, seed.TABLES):
            col.button(table, key=f"preview_{table}", on_click=on_toggle_preview, args=(table,),
                       type="primary" if table in previews else "secondary", width="stretch")
        for table in previews:
            st.markdown(f"**{table}**")
            st.dataframe(con.execute(f"SELECT * FROM {table} LIMIT 10").df(), hide_index=True)


def render_result(res, problem):
    if res.get("empty"):
        st.warning("Write a query first.")
        return
    if "error" in res:
        st.error("**SQL error.** Your query didn't run:", icon="⚠️")
        st.code(res["error"], language=None)
        return

    if res["correct"]:
        st.success("**Correct.** Your result matches the reference.", icon="✅")
    else:
        st.error("**Incorrect.**", icon="❌")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Your rows", f">{MAX_ROWS:,}" if res.get("too_many") else res["n_user"])
    m2.metric("Expected rows", res["n_expected"])
    m3.metric("Missing rows", res["n_missing"])
    m4.metric("Extra rows", res["n_extra"])
    if res.get("problem"):
        st.warning(res["problem"])
    if res.get("hint"):
        st.info(res["hint"])
    if res["missing"]:
        st.markdown(f"**Missing rows** (expected but not in yours), first "
                    f"{min(SHOW_ROWS, res['n_missing'])} of {res['n_missing']}:")
        st.dataframe(frame(res["missing"], res["ref_cols"]), hide_index=True)
    if res["extra"]:
        st.markdown(f"**Extra rows** (in yours but not expected), first "
                    f"{min(SHOW_ROWS, res['n_extra'])} of {res['n_extra']}:")
        st.dataframe(frame(res["extra"], res["user_cols"]), hide_index=True)
    with st.expander(f"Your result (first {min(50, len(res['preview']))} rows)"):
        st.dataframe(frame(res["preview"], res["user_cols"]), hide_index=True)


def render_problem(problem):
    ss = st.session_state
    pid = problem["id"]

    head, clock = st.columns([3, 1.3], vertical_alignment="center")
    head.subheader(f"{pid}. {problem['title']}")
    with clock:
        html = (TIMER_HTML.replace("__PID__", str(pid))
                          .replace("__SESSION__", ss.session_id)
                          .replace("__LIMIT__", str(TIMER_MINUTES)))
        st.iframe(html, height=58)

    st.markdown(problem["prompt"])
    st.markdown("**Return:** " + ", ".join(f"`{c}`" for c in problem["columns"]))

    render_schema(ss.con)
    with st.expander("Dialect notes: DuckDB vs BigQuery"):
        st.markdown(DIALECT_NOTES)

    st.text_area(
        f"Your SQL for problem {pid}",
        key=f"sql_{pid}",
        height=320,
        placeholder="Write it by hand first, then paste it here.\n\nSELECT ...",
        label_visibility="collapsed",
    )
    with st.container(horizontal=True):
        st.button("Check", key=f"check_{pid}", type="primary", on_click=on_check, args=(pid,))
        st.button("Show solution", key=f"reveal_{pid}", on_click=on_reveal, args=(pid,),
                  disabled=ss.checks[pid] == 0,
                  help="Available after your first Check." if ss.checks[pid] == 0 else None)

    res = ss.results.get(pid)
    if res:
        render_result(res, problem)

    revealed = pid in ss.revealed
    if ss.status[pid] == "correct" or revealed:
        st.markdown("#### Edge cases to say out loud")
        st.markdown("\n".join(f"- {e}" for e in problem["edge_cases"]))
    if revealed:
        st.markdown("#### Reference solution")
        st.code(problem["solution"], language="sql")


def render_flashcards():
    st.subheader("Python concepts")
    st.caption("Answer out loud first, then open the card.")
    for question, answer in FLASHCARDS:
        with st.expander(question):
            st.markdown(answer)


def main():
    st.set_page_config(page_title="SQL interview practice", page_icon="🧮", layout="wide")
    st.html(CSS)
    init_state()
    render_sidebar()

    st.title("SQL interview practice")
    st.caption("Google Ads abuse dataset · write the query by hand, paste it here, check it. "
               "Columns are compared by position, row order is ignored, floats are rounded to "
               "4 decimals, and NULL equals NULL.")
    tab_sql, tab_py = st.tabs(["SQL problems", "Python concepts"])
    with tab_sql:
        render_problem(PROBLEMS_BY_ID[st.session_state.current])
    with tab_py:
        render_flashcards()


if __name__ == "__main__":
    main()
