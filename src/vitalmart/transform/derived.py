"""Derived-layer pre-step: config → temp tables / SQL variables (SPEC 5.4, 4.5)."""

from __future__ import annotations

import duckdb

from vitalmart.config import AppConfig
from vitalmart.datamart.seed import insert_rows


def prepare(con: duckdb.DuckDBPyConnection, cfg: AppConfig) -> None:
    d, q = cfg.derived, cfg.quality
    con.execute("CREATE OR REPLACE TEMP TABLE _priority (device_type VARCHAR, prio INTEGER)")
    insert_rows(con, "_priority", ["device_type", "prio"],
                [(t, i) for i, t in enumerate(d.source_priority)])
    con.execute("CREATE OR REPLACE TEMP TABLE _primary_override "
                "(metric_key VARCHAR, source_key VARCHAR)")
    insert_rows(con, "_primary_override", ["metric_key", "source_key"],
                sorted(d.primary_source.items()))
    con.execute("DELETE FROM map_excluded_source")
    insert_rows(con, "map_excluded_source", ["source_key"],
                [(k,) for k in sorted(set(d.excluded_sources))])
    for name, value in (
        ("bl_window_days", d.baseline_window_days), ("bl_min_days", d.baseline_min_days),
        ("band_z", float(d.band_z)), ("reg_min_nights", d.regularity_min_nights),
        ("acute_days", d.acwr_acute_days), ("chronic_days", d.acwr_chronic_days),
        ("sleep_goal_min", float(cfg.sleep.goal_hours) * 60),
        ("gap_min_days", q.gap_min_days), ("nonwear_gap_hours", float(q.nonwear_gap_hours)),
        ("constant_run_samples", q.constant_run_samples),
        ("switch_min_days", q.switch_min_days),
    ):
        con.execute(f"SET VARIABLE {name} = ?", [value])
