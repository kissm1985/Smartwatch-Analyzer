"""Read-only SQL console against the star schema (SPEC 8.4, MAY).

Safety layers:
1. exactly one statement, and DuckDB must classify it as SELECT (DDL/DML/COPY/ATTACH/PRAGMA
   are rejected by ``json_serialize_sql``);
2. the parsed AST may only reference star-schema tables (dim_*, fact_*, derived_*, dq_*,
   event_day) or its own CTEs – no staging tables, no files, no other databases – and only
   harmless table functions (range, generate_series, unnest); side-effecting functions are
   rejected;
3. it runs on a separate cursor inside a transaction that is always rolled back, with a time
   limit (interrupt) and a row limit.
Query text and results are never logged.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd

from vitalmart.i18n import t

ALLOWED_PREFIXES = ("dim_", "fact_", "derived_", "dq_")
ALLOWED_TABLES = {"event_day"}
ALLOWED_TABLE_FUNCTIONS = {"range", "generate_series", "unnest"}
DENIED_FUNCTIONS = {"nextval", "currval", "setseed", "getenv", "read_text", "read_blob",
                    "query", "query_table", "sql_auto_complete"}
MAX_ROWS = 10_000
TIMEOUT_S = 15.0
MAX_SQL_LEN = 20_000


class SqlError(ValueError):
    def __init__(self, key: str, **kwargs: Any) -> None:
        super().__init__(t(key, **kwargs))
        self.key = key
        self.kwargs = kwargs


@dataclass
class SqlResult:
    df: pd.DataFrame
    truncated: bool
    elapsed_s: float


def allowed_table(name: str) -> bool:
    n = name.lower()
    return n in ALLOWED_TABLES or n.startswith(ALLOWED_PREFIXES)


def _walk(node: Any, acc: dict[str, set[str]]) -> None:
    if isinstance(node, dict):
        cte = node.get("cte_map")
        if isinstance(cte, dict):
            acc["ctes"] |= {str(e.get("key", "")).lower() for e in cte.get("map", [])}
        typ = node.get("type")
        if typ == "BASE_TABLE":
            if node.get("catalog_name") or node.get("schema_name") not in ("", "main", None):
                acc["foreign"].add(f"{node.get('schema_name')}.{node.get('table_name')}")
            acc["tables"].add(str(node.get("table_name", "")))
        elif typ == "TABLE_FUNCTION":
            fn = node.get("function", {})
            acc["table_functions"].add(str(fn.get("function_name", "")).lower())
        if node.get("class") == "FUNCTION":
            acc["functions"].add(str(node.get("function_name", "")).lower())
        for v in node.values():
            _walk(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk(v, acc)


def validate(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SqlError("sql.err.empty")
    if len(sql) > MAX_SQL_LEN:
        raise SqlError("sql.err.too_long", max=MAX_SQL_LEN)
    try:
        stmts = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise SqlError("sql.err.parse", msg=str(exc).splitlines()[0]) from None
    if len(stmts) != 1:
        raise SqlError("sql.err.single")
    if stmts[0].type != duckdb.StatementType.SELECT:
        raise SqlError("sql.err.select_only")
    ast = json.loads(con.execute("SELECT json_serialize_sql(?)", [sql]).fetchall()[0][0])
    if ast.get("error"):
        raise SqlError("sql.err.select_only")
    acc: dict[str, set[str]] = {k: set() for k in ("ctes", "tables", "table_functions",
                                                  "functions", "foreign")}
    _walk(ast, acc)
    if acc["foreign"]:
        raise SqlError("sql.err.table", table=", ".join(sorted(acc["foreign"])))
    bad = sorted(x for x in acc["tables"] if x.lower() not in acc["ctes"]
                 and not allowed_table(x))
    if bad:
        raise SqlError("sql.err.table", table=", ".join(bad))
    bad_fn = sorted((acc["table_functions"] - ALLOWED_TABLE_FUNCTIONS)
                    | (acc["functions"] & DENIED_FUNCTIONS))
    if bad_fn:
        raise SqlError("sql.err.function", fn=", ".join(bad_fn))


def execute(con: duckdb.DuckDBPyConnection, sql: str, *, max_rows: int = MAX_ROWS,
            timeout_s: float = TIMEOUT_S) -> SqlResult:
    validate(con, sql)
    sql = sql.strip().rstrip(";")
    cur = con.cursor()
    timer = threading.Timer(timeout_s, cur.interrupt)
    started = time.perf_counter()
    try:
        cur.execute("BEGIN TRANSACTION")
        timer.start()
        cur.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        cols = [d[0] for d in cur.description or []]
    except duckdb.InterruptException:
        raise SqlError("sql.err.timeout", s=int(timeout_s)) from None
    except duckdb.Error as exc:
        raise SqlError("sql.err.exec", msg=str(exc).splitlines()[0]) from None
    finally:
        timer.cancel()
        with contextlib.suppress(duckdb.Error):
            cur.execute("ROLLBACK")
        cur.close()
    truncated = len(rows) > max_rows
    df = pd.DataFrame(rows[:max_rows], columns=cols)
    return SqlResult(df, truncated, time.perf_counter() - started)


def schema(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Browsable schema of the allowed tables with column descriptions (export dictionary)."""
    from vitalmart.export.tabular import load_registry

    reg = load_registry()
    rows = con.execute("""SELECT table_name, column_name, data_type
                          FROM information_schema.columns WHERE table_schema = 'main'
                          ORDER BY table_name, ordinal_position""").fetchall()
    out = []
    for table, col, typ in rows:
        if not allowed_table(table):
            continue
        doc = reg.columns.get(col)
        out.append({"table": table, "column": col, "type": typ,
                    "unit": doc.unit if doc else "", "description": doc.desc if doc else ""})
    return pd.DataFrame(out, columns=["table", "column", "type", "unit", "description"])


EXAMPLES = {
    "sql.ex.daily": """SELECT date, metric_key, value
FROM derived_person_daily
WHERE person_key = 'me' AND metric_key IN ('resting_hr', 'steps')
ORDER BY date DESC
LIMIT 50""",
    "sql.ex.sleep_weekday": """SELECT d.day_name_hu AS nap,
       round(avg(s.asleep_min) / 60, 2) AS alvas_ora, count(*) AS ejszakak
FROM fact_sleep_session s
JOIN dim_date d ON d.date_key = s.sleep_date_key
WHERE s.is_main
GROUP BY d.day_of_week, d.day_name_hu
ORDER BY d.day_of_week""",
    "sql.ex.events": """WITH napok AS (
  SELECT p.person_key, p.date, p.value,
         e.labels IS NOT NULL AS esemeny_alatt
  FROM derived_person_daily p
  LEFT JOIN event_day e ON e.person_key = p.person_key AND e.date = p.date
  WHERE p.metric_key = 'resting_hr'
)
SELECT person_key, esemeny_alatt, round(avg(value), 1) AS nyugalmi_pulzus, count(*) AS napok
FROM napok GROUP BY ALL ORDER BY ALL""",
}
