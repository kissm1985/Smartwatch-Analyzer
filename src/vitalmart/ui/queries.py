"""UI data access (no Streamlit dependency → unit-testable)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import duckdb
import pandas as pd

ZONE_PCT = [50, 60, 70, 80, 90]          # default zone lower bounds, % of max HR


# ---------------------------------------------------------------- formatting (SPEC 10.5)
from vitalmart.fmt import fmt_date, fmt_datetime, fmt_hm, fmt_num  # noqa: E402,F401 - re-export


def labeled(df: pd.DataFrame) -> pd.DataFrame:
    """Localised column headers, Hungarian dates/times and status names for display."""
    from vitalmart.i18n import has, t

    out = df.copy()
    for col in out.columns:
        if col in ("first_date", "last_date", "date_from", "date_to", "gap_start", "gap_end",
                   "date", "month"):
            out[col] = out[col].map(fmt_date)
        elif col in ("started_at", "start_local", "end_local"):
            out[col] = out[col].map(fmt_datetime)
        elif col == "status":
            out[col] = out[col].map(
                lambda v: t(f"batch_status.{v}") if has(f"batch_status.{v}") else v)
    return out.rename(columns={c: t(f"col.{c}") for c in out.columns if has(f"col.{c}")})


def metric_label(con: duckdb.DuckDBPyConnection | None, key: str) -> str:
    from vitalmart.i18n import has, t

    if has(f"metric.{key}"):
        return t(f"metric.{key}")
    if con is not None:
        row = con.execute("SELECT name_hu FROM dim_metric WHERE metric_key = ?",
                          [key]).fetchall()
        if row and row[0][0]:
            return str(row[0][0])
    return key


# ---------------------------------------------------------------- persons / freshness

def persons(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    rows = con.execute("""
        SELECT p.person_key, coalesce(nullif(p.display_name, ''), p.person_key)
        FROM dim_person p
        WHERE EXISTS (SELECT 1 FROM dim_source s WHERE s.person_key = p.person_key)
        ORDER BY 1""").fetchall()
    return [(r[0], r[1]) for r in rows]


def has_data(con: duckdb.DuckDBPyConnection) -> bool:
    return bool(con.execute("SELECT count(*) FROM dim_source").fetchall()[0][0])


def freshness(con: duckdb.DuckDBPyConnection, person: str) -> pd.DataFrame:
    return con.execute("""
        SELECT source_key, vendor, device_type, first_date, last_date
        FROM dim_source WHERE person_key = ? ORDER BY last_date DESC NULLS LAST""",
        [person]).df()


def last_data_date(con: duckdb.DuckDBPyConnection, person: str) -> date | None:
    row = con.execute("SELECT max(date) FROM derived_person_daily WHERE person_key = ?",
                      [person]).fetchall()
    return row[0][0] if row else None


# ---------------------------------------------------------------- overview cards

@dataclass
class Card:
    metric_key: str
    label: str
    unit: str
    value: float | None
    baseline: float | None
    better: str                      # higher | lower | neutral
    n_days: int
    series: list[tuple[date, float]] = field(default_factory=list)
    note: str = ""

    @property
    def delta(self) -> float | None:
        if self.value is None or self.baseline is None:
            return None
        return self.value - self.baseline

    @property
    def delta_pct(self) -> float | None:
        if self.delta is None or not self.baseline:
            return None
        return 100.0 * self.delta / self.baseline


CARD_DEFS = [
    # metric, label key, unit, aggregation, better
    ("resting_hr", "ui.card.resting_hr", "bpm", "mean", "lower"),
    ("hrv", "ui.card.hrv", "ms", "mean", "higher"),
    ("sleep_min", "ui.card.sleep", "h", "mean", "higher"),
    ("who_equiv_min", "ui.card.who", "perc/hét", "weekly", "higher"),
    ("steps", "ui.card.steps", "lépés/nap", "mean", "higher"),
    ("stress_avg", "ui.card.stress", "pont", "mean", "lower"),
]


def _values(con: duckdb.DuckDBPyConnection, person: str, metric: str, d0: date,
            d1: date) -> pd.DataFrame:
    return con.execute("""
        SELECT date, value FROM derived_person_daily
        WHERE person_key = ? AND metric_key = ? AND date BETWEEN ? AND ? ORDER BY date""",
        [person, metric, d0, d1]).df()


def _hrv_metric(con: duckdb.DuckDBPyConnection, person: str, d0: date, d1: date) -> str:
    """HRV methods are never mixed: show the one with more nights in the period."""
    rows = con.execute("""
        SELECT metric_key, count(*) FROM derived_person_daily
        WHERE person_key = ? AND metric_key IN ('hrv_sdnn', 'hrv_rmssd')
          AND date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC, 1""", [person, d0, d1]).fetchall()
    return rows[0][0] if rows else "hrv_sdnn"


def overview_cards(
    con: duckdb.DuckDBPyConnection, person: str, days: int, *, end: date | None = None,
    baseline_days: int = 60, baseline_min_days: int = 14, labels: dict[str, str] | None = None,
) -> list[Card]:
    """Key indicators for the last ``days`` days (ending at the person's last data day)
    compared with the mean of the preceding ``baseline_days`` days."""
    end = end or last_data_date(con, person)
    if end is None:
        return []
    d0 = end - timedelta(days=days - 1)
    b1, b0 = d0 - timedelta(days=1), d0 - timedelta(days=baseline_days)
    labels = labels or {}
    cards: list[Card] = []
    for metric, label_key, unit, agg, better in CARD_DEFS:
        m = _hrv_metric(con, person, d0, end) if metric == "hrv" else metric
        cur = _values(con, person, m, d0, end)
        base = _values(con, person, m, b0, b1)
        if cur.empty:
            continue
        scale = 1 / 60 if metric == "sleep_min" else 1.0
        if agg == "weekly":
            value = float(cur["value"].sum()) / days * 7
            base_val = (float(base["value"].sum()) / baseline_days * 7
                        if len(base) >= baseline_min_days else None)
        else:
            value = float(cur["value"].mean()) * scale
            base_val = (float(base["value"].mean()) * scale
                        if len(base) >= baseline_min_days else None)
        note = m.replace("hrv_", "").upper() if metric == "hrv" else ""
        cards.append(Card(m, labels.get(label_key, label_key), unit, value, base_val, better,
                          len(cur), [(r.date, r.value * scale) for r in cur.itertuples()], note))
    return cards


# ---------------------------------------------------------------- import log / quality

def import_log(con: duckdb.DuckDBPyConnection, limit: int = 50) -> pd.DataFrame:
    return con.execute("""
        SELECT import_batch_id AS batch, started_at, status, vendor, person_key, file_name,
               records_read, records_new, records_updated, records_skipped, records_error,
               date_from, date_to, message
        FROM dim_import_batch ORDER BY import_batch_id DESC LIMIT ?""", [limit]).df()


def coverage(con: duckdb.DuckDBPyConnection, person: str) -> pd.DataFrame:
    return con.execute("""
        SELECT c.source_key, c.metric_key, coalesce(m.name_hu, c.metric_key) AS metric,
               c.first_date, c.last_date, c.days_with_data, c.days_in_span, c.coverage_pct
        FROM dq_coverage c LEFT JOIN dim_metric m USING (metric_key)
        WHERE c.person_key = ? ORDER BY c.source_key, c.metric_key""", [person]).df()


def monthly_coverage(con: duckdb.DuckDBPyConnection, person: str) -> pd.DataFrame:
    """Days with data per month × metric (primary source) for the heatmap."""
    return con.execute("""
        SELECT date_trunc('month', date)::DATE AS month, metric_key, count(*) AS days
        FROM derived_person_daily WHERE person_key = ?
        GROUP BY 1, 2 ORDER BY 1, 2""", [person]).df()


def gaps(con: duckdb.DuckDBPyConnection, person: str) -> pd.DataFrame:
    return con.execute("""SELECT source_key, metric_key, gap_start, gap_end, gap_days
                          FROM dq_gap WHERE person_key = ? ORDER BY gap_days DESC, gap_start""",
                       [person]).df()


def flags(con: duckdb.DuckDBPyConnection, person: str) -> pd.DataFrame:
    return con.execute("""SELECT source_key, fact_table, metric_key, quality_flag, n, total,
                                 round(100.0 * n / total, 2) AS pct
                          FROM dq_flag_summary WHERE person_key = ? ORDER BY n DESC""",
                       [person]).df()


def nonwear(con: duckdb.DuckDBPyConnection, person: str) -> pd.DataFrame:
    return con.execute("""SELECT source_key, kind, start_local, end_local,
                                 round(minutes / 60.0, 1) AS hours, samples
                          FROM dq_nonwear WHERE person_key = ? ORDER BY start_local DESC""",
                       [person]).df()


def switches(con: duckdb.DuckDBPyConnection, person: str) -> pd.DataFrame:
    return con.execute("""SELECT date, metric_key, from_source, to_source FROM dq_source_switch
                          WHERE person_key = ? ORDER BY date""", [person]).df()


# ---------------------------------------------------------------- settings helpers

def multi_source_metrics(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Metrics for which some person has more than one candidate source."""
    return con.execute("""
        SELECT c.metric_key, coalesce(any_value(m.name_hu), c.metric_key) AS name_hu,
               list(DISTINCT c.source_key ORDER BY c.source_key) AS sources
        FROM dq_coverage c LEFT JOIN dim_metric m USING (metric_key)
        GROUP BY c.metric_key
        HAVING count(DISTINCT c.source_key) > 1
        ORDER BY 1""").df()


def zone_bounds(max_hr: float) -> list[int]:
    return [round(max_hr * p / 100) for p in ZONE_PCT]


def current_profile(con: duckdb.DuckDBPyConnection, person: str) -> dict[str, Any] | None:
    df = con.execute("""SELECT * FROM dim_user_profile WHERE person_key = ? AND valid_to IS NULL
                        ORDER BY valid_from DESC LIMIT 1""", [person]).df()
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    row["zone_bounds"] = json.loads(row["zone_bounds"]) if row.get("zone_bounds") else None
    return row


def save_profile(
    con: duckdb.DuckDBPyConnection, person: str, *, birth_year: int | None, sex: str | None,
    height_cm: float | None, max_hr: float | None, max_hr_source: str | None,
    zones: list[int] | None, today: date | None = None,
) -> None:
    """SCD2: close the current version and open a new one valid from ``today``
    (a change on the same day overwrites that day's version)."""
    today = today or date.today()
    zjson = json.dumps(zones) if zones else None
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM dim_user_profile WHERE person_key = ? AND valid_from = ?",
                    [person, today])
        con.execute("""UPDATE dim_user_profile SET valid_to = ?
                       WHERE person_key = ? AND valid_to IS NULL""",
                    [today - timedelta(days=1), person])
        con.execute("""INSERT INTO dim_user_profile (person_key, valid_from, valid_to, birth_year,
                       sex, height_cm, max_hr, max_hr_source, zone_bounds)
                       VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                    [person, today, birth_year, sex, height_cm, max_hr, max_hr_source, zjson])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def set_display_name(con: duckdb.DuckDBPyConnection, person: str, name: str) -> None:
    con.execute("UPDATE dim_person SET display_name = ? WHERE person_key = ?",
                [name.strip() or None, person])


def db_size_mb(path: Any) -> float:
    try:
        return path.stat().st_size / 1e6
    except OSError:
        return 0.0
