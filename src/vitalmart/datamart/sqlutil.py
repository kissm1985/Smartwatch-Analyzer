"""Small query helpers.

DuckDB 2.x keeps a result "unfinished" after ``fetchone()`` and refuses further
work (e.g. ``register``) on that connection, so results are always fully fetched.
"""

from __future__ import annotations

from typing import Any

import duckdb


def fetch_row(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> tuple[Any, ...] | None:
    rows = con.execute(sql, params or []).fetchall()
    return rows[0] if rows else None


def fetch_scalar(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> Any:
    row = fetch_row(con, sql, params)
    return row[0] if row else None
