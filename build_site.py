"""Build the data file for the static GitHub Pages version in docs/.

    python build_site.py

The browser version runs the same problems on DuckDB-WASM. Everything it needs
comes from seed.py, problems.py and app.py, exported to docs/site-data.json.
Seed rows are stored as SQL with dates relative to CURRENT_DATE, so the site
gets the same data as the Streamlit app on any day. The build replays that SQL
in DuckDB and fails if it doesn't reproduce seed.create_connection() exactly.
"""

import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

import seed
from app import DIALECT_NOTES, FLASHCARDS, MAX_ROWS, QUERY_TIMEOUT_S, SHOW_ROWS, TIMER_MINUTES
from problems import PROBLEMS

OUT = Path(__file__).parent / "docs" / "site-data.json"
ANCHOR = date(2000, 1, 1)  # any date works: the data only depends on offsets from "today"
ANCHOR_TS = datetime(ANCHOR.year, ANCHOR.month, ANCHOR.day)


def _literal(value, typ):
    """SQL literal for one cell; dates and timestamps as offsets from the anchor."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "NULL"
    if typ == "INTEGER":
        return str(int(value))
    if typ == "TEXT":
        return "'" + str(value).replace("'", "''") + "'"
    if typ == "DATE":
        return str((value - ANCHOR).days)
    if typ == "TIMESTAMP":
        return str(int((value - ANCHOR_TS).total_seconds()))
    raise ValueError(typ)


def _expr(col, typ):
    """Turn an offset column back into a date or timestamp relative to CURRENT_DATE."""
    if typ == "DATE":
        return f"CAST(CURRENT_DATE + {col} AS DATE)"
    if typ == "TIMESTAMP":
        return f"CAST(CURRENT_DATE AS TIMESTAMP) + to_seconds({col})"
    return col


def seed_statements():
    data = seed.generate(ANCHOR)
    statements = []
    for table, spec in seed.TABLES.items():
        ddl = ", ".join(f"{col} {typ}" for col, typ, _ in spec)
        statements.append(f"CREATE TABLE {table} ({ddl})")
        names = [f"c{i}" for i in range(len(spec))]
        select = ", ".join(_expr(n, typ) for n, (_, typ, _) in zip(names, spec))
        rows = data[table].astype(object).itertuples(index=False, name=None)
        values = ",\n".join(
            "(" + ", ".join(_literal(v, typ) for v, (_, typ, _) in zip(row, spec)) + ")"
            for row in rows
        )
        statements.append(
            f"INSERT INTO {table} SELECT {select} FROM (VALUES\n{values}\n) AS v({', '.join(names)})"
        )
    return statements + seed.COMPAT_MACROS


def check_round_trip(statements):
    """The exported SQL must rebuild exactly the tables the Streamlit app uses."""
    expected = seed.create_connection()
    con = duckdb.connect(":memory:")
    for sql in statements:
        con.execute(sql)
    for table in seed.TABLES:
        q = f"SELECT * FROM {table}"
        assert con.execute(q).fetchall() == expected.execute(q).fetchall(), table
        assert con.execute(f"DESCRIBE {table}").fetchall() == \
            expected.execute(f"DESCRIBE {table}").fetchall(), table


def build():
    statements = seed_statements()
    check_round_trip(statements)
    site = {
        "settings": {
            "timer_minutes": TIMER_MINUTES,
            "max_rows": MAX_ROWS,
            "query_timeout_s": QUERY_TIMEOUT_S,
            "show_rows": SHOW_ROWS,
        },
        "tables": [
            {"name": t, "note": seed.TABLE_NOTES[t]} for t in seed.TABLES
        ],
        "schema_text": seed.schema_text(),
        "seed_sql": statements,
        "problems": PROBLEMS,
        "flashcards": [{"q": q, "a": a.strip()} for q, a in FLASHCARDS],
        "dialect_notes": DIALECT_NOTES.strip(),
    }
    return json.dumps(site, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(Path(__file__).parent)} ({OUT.stat().st_size // 1024} KB)")
