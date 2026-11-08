"""Data access for the report: one person, the report period and the comparison
period (the preceding period of equal length) – all from the derived layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import pandas as pd


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        return Period(self.start - timedelta(days=self.days), self.start - timedelta(days=1))


@dataclass
class ReportData:
    person: str
    person_name: str
    period: Period
    cmp: Period
    daily: pd.DataFrame                  # index: date, columns: metric keys (cmp.start..end)
    daily_source: dict[str, str]         # metric -> primary source in the period (most days)
    stats: pd.DataFrame                  # derived_daily_stats rows in the period
    regularity: pd.DataFrame
    weekly: pd.DataFrame
    load: pd.DataFrame
    activities: pd.DataFrame
    zones: pd.DataFrame
    sources: pd.DataFrame
    gaps: pd.DataFrame
    vendors: set[str] = field(default_factory=set)

    def series(self, metric: str, *, prev: bool = False) -> pd.Series:
        p = self.cmp if prev else self.period
        if metric not in self.daily.columns:
            return pd.Series(dtype="float64")
        s = self.daily[metric]
        mask = (s.index >= pd.Timestamp(p.start)) & (s.index <= pd.Timestamp(p.end))
        return s[mask].dropna()

    def metric_stats(self, metric: str) -> pd.DataFrame:
        return self.stats[self.stats["metric_key"] == metric].set_index("date").sort_index()

    def hrv_metrics(self) -> list[str]:
        return [m for m in ("hrv_sdnn", "hrv_rmssd") if len(self.series(m))]


def load(con: duckdb.DuckDBPyConnection, person: str, start: date, end: date) -> ReportData:
    period = Period(start, end)
    cmp = period.previous()
    long = con.execute("""
        SELECT date, metric_key, value, source_key FROM derived_person_daily
        WHERE person_key = ? AND date BETWEEN ? AND ?""", [person, cmp.start, end]).df()
    if long.empty:
        daily = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
        daily_source: dict[str, str] = {}
    else:
        long["date"] = pd.to_datetime(long["date"])
        daily = long.pivot_table(index="date", columns="metric_key", values="value",
                                 aggfunc="first").sort_index()
        in_p = long[long["date"] >= pd.Timestamp(start)]
        daily_source = (in_p.groupby("metric_key")["source_key"]
                        .agg(lambda s: s.value_counts().index[0]).to_dict())
    stats = con.execute("""SELECT * FROM derived_daily_stats
                           WHERE person_key = ? AND date BETWEEN ? AND ?""",
                        [person, start, end]).df()
    if not stats.empty:
        stats["date"] = pd.to_datetime(stats["date"])
    regularity = con.execute("""SELECT * FROM derived_sleep_regularity
                                WHERE person_key = ? AND date BETWEEN ? AND ? ORDER BY date""",
                             [person, start, end]).df()
    weekly = con.execute("""SELECT * FROM derived_weekly_activity
                            WHERE person_key = ? AND week_start BETWEEN ? AND ?
                            ORDER BY week_start""",
                         [person, start - timedelta(days=6), end]).df()
    load_df = con.execute("""SELECT * FROM derived_training_load
                             WHERE person_key = ? AND date BETWEEN ? AND ? ORDER BY date""",
                          [person, start, end]).df()
    activities = con.execute("""
        SELECT a.*, t.unified_type, t.unified_name_hu, t.category AS type_category
        FROM fact_activity a
        JOIN dim_source s USING (source_key)
        LEFT JOIN dim_activity_type t USING (activity_type_key)
        WHERE s.person_key = ? AND a.quality_flag = 'ok'
          AND CAST(a.start_local AS DATE) BETWEEN ? AND ?
        ORDER BY a.start_utc""", [person, start, end]).df()
    zones = con.execute("""
        SELECT z.zone, sum(z.seconds_in_zone) AS seconds
        FROM fact_activity_hr_zone z
        JOIN fact_activity a USING (activity_id)
        JOIN dim_source s ON s.source_key = a.source_key
        WHERE s.person_key = ? AND CAST(a.start_local AS DATE) BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1""", [person, start, end]).df()
    sources = con.execute("""SELECT source_key, vendor, source_name, device_type, first_date,
                                    last_date FROM dim_source WHERE person_key = ?
                             ORDER BY vendor, source_key""", [person]).df()
    gaps = con.execute("""SELECT * FROM dq_gap WHERE person_key = ?
                          AND gap_end >= ? AND gap_start <= ? ORDER BY gap_days DESC""",
                       [person, start, end]).df()
    name_row = con.execute("SELECT coalesce(nullif(display_name, ''), person_key) "
                           "FROM dim_person WHERE person_key = ?", [person]).fetchall()
    used = set(daily_source.values())
    vendors = set(sources[sources["source_key"].isin(used)]["vendor"]) if len(sources) else set()
    return ReportData(person, name_row[0][0] if name_row else person, period, cmp, daily,
                      daily_source, stats, regularity, weekly, load_df, activities, zones,
                      sources, gaps, vendors)


def last_data_day(con: duckdb.DuckDBPyConnection, person: str) -> date | None:
    row = con.execute("SELECT max(date) FROM derived_person_daily WHERE person_key = ?",
                      [person]).fetchall()
    return row[0][0] if row else None
