"""Shared staging helpers: NULL-safe SQL hashing and new/updated/skipped classification."""

from __future__ import annotations

import duckdb

from vitalmart.datamart.sqlutil import fetch_row
from vitalmart.importers.base import LoadCounts


def sql_hash(*parts: str) -> str:
    """SQL md5 over NULL-safe, '|'-joined parts."""
    return "md5(concat_ws('|', " + ", ".join(f"coalesce(CAST({p} AS VARCHAR), '∅')"
                                               for p in parts) + "))"


def classify_insert(
    con: duckdb.DuckDBPyConnection,
    src: str,
    target: str,
    batch_id: int,
    select_cols: str,
    after_insert: str | None = None,
    error_condition: str = "start_utc IS NULL",
) -> LoadCounts:
    """Insert rows of ``src`` whose row_hash is not yet in ``target``.

    new      = distinct new row_hash whose natural key was unknown
    updated  = distinct new row_hash whose natural key already existed (newer batch wins)
    skipped  = everything else read (exact duplicates, also within the same file)
    errors   = new rows without a parseable start timestamp (kept in staging, not in core)
    """
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _new AS
        SELECT DISTINCT ON (l.row_hash) l.*
        FROM {src} l ANTI JOIN {target} s ON s.row_hash = l.row_hash
        ORDER BY l.row_hash
    """)
    row = fetch_row(con, f"""
        SELECT (SELECT count(*) FROM {src}),
               count(*),
               count(*) FILTER (WHERE nk_hash IN (SELECT nk_hash FROM {target})),
               count(*) FILTER (WHERE {error_condition})
        FROM _new
    """)
    assert row is not None
    read, n_new_rows, updated, errors = (int(x) for x in row)
    matched = {int(r[0]) for r in con.execute(f"""
        SELECT DISTINCT s.import_batch_id FROM {src} l
        JOIN {target} s ON s.row_hash = l.row_hash OR s.nk_hash = l.nk_hash
    """).fetchall()}
    con.execute(
        f"INSERT INTO {target} SELECT {select_cols.format(batch=batch_id)} FROM _new"
    )
    if after_insert:
        con.execute(after_insert)
    con.execute("DROP TABLE _new")
    return LoadCounts(read=read, new=n_new_rows - updated, updated=updated,
                      skipped=read - n_new_rows, errors=errors, matched_batches=matched)
