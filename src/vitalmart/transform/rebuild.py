"""Core rebuild: staging → dim_* / fact_*.

Steps
1. load mapping tables (``map_*``, ``tz_offset``, ``dim_metric``) from config/*.yaml,
2. run vendor transforms (``sql/transform/<vendor>/*.sql``, in file-name order),
3. run cross-vendor transforms (``sql/transform/common/*.sql``).

Every vendor transform deletes and re-inserts only its own rows
(``source_key LIKE '<vendor>:%'``), so the core layer is always fully
reproducible from staging (SPEC 3). The caller controls the transaction.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import pyarrow as pa

from vitalmart.config import AppConfig
from vitalmart.datamart.seed import insert_rows
from vitalmart.paths import TRANSFORM_DIR
from vitalmart.timeutil import offset_transitions

log = logging.getLogger(__name__)

# (directory, optional Python pre-step) in execution order
STEPS: tuple[str, ...] = ("_pre", "apple", "garmin", "common")


def load_mappings(con: duckdb.DuckDBPyConnection, cfg: AppConfig) -> None:
    apple = cfg.load_yaml("apple_mapping.yaml")
    metrics = cfg.load_yaml("metrics.yaml").get("metrics", {})

    con.execute("DELETE FROM map_apple_type")
    rows = [
        (hk, m["metric"], m.get("target", "measurement"), m.get("scale_if_fraction"))
        for hk, m in (apple.get("records") or {}).items()
    ]
    insert_rows(con, "map_apple_type", ["hk_type", "metric_key", "target", "scale_if_fraction"],
                rows)

    con.execute("DELETE FROM map_sleep_value WHERE vendor = 'Apple'")
    rows2 = [("Apple", k, v) for k, v in (apple.get("sleep_values") or {}).items()]
    insert_rows(con, "map_sleep_value", ["vendor", "value_text", "stage_key"], rows2)

    con.execute("DELETE FROM map_unit")
    units = {(u["from"], u["to"]): float(u["factor"]) for u in apple.get("units") or []}
    insert_rows(con, "map_unit", ["from_unit", "to_unit", "factor"],
                [(f, t, v) for (f, t), v in units.items()])

    con.execute("DELETE FROM map_apple_workout_type")
    rows3 = [
        (hk, w["unified"], w["category"]) for hk, w in (apple.get("workout_types") or {}).items()
    ]
    insert_rows(con, "map_apple_workout_type", ["hk_type", "unified_type", "category"], rows3)

    # Configured metric catalogue; auto metrics are re-derived by the transforms.
    con.execute("DELETE FROM dim_metric")
    insert_rows(
        con, "dim_metric",
        ["metric_key", "name_hu", "name_en", "category", "unit", "agg_rule", "better_direction",
         "valid_min", "valid_max", "note", "is_auto"],
        [
            (k, m.get("name_hu"), m.get("name_en"), m.get("category"), m.get("unit"),
             m.get("agg", "AVG"), m.get("better", "neutral"),
             None if m.get("valid_min") is None else float(m["valid_min"]),
             None if m.get("valid_max") is None else float(m["valid_max"]), m.get("note"), False)
            for k, m in metrics.items()
        ],
    )

    con.execute("DELETE FROM tz_offset")
    trans = offset_transitions(cfg.timezone)
    tbl = pa.table({
        "valid_from_utc": pa.array([t for t, _ in trans], pa.timestamp("us")),
        "offset_min": pa.array([o for _, o in trans], pa.int32()),
    })
    con.register("_tz", tbl)
    try:
        con.execute("INSERT INTO tz_offset SELECT * FROM _tz")
    finally:
        con.unregister("_tz")


def _set_variables(con: duckdb.DuckDBPyConnection, cfg: AppConfig) -> None:
    con.execute("SET VARIABLE sleep_gap_min = ?", [cfg.sleep.session_gap_minutes])
    con.execute("SET VARIABLE sleep_day_convention = ?", [cfg.sleep.day_convention])


def transform_files(directory: Path = TRANSFORM_DIR) -> list[Path]:
    files: list[Path] = []
    for step in STEPS:
        files += sorted((directory / step).glob("*.sql"))
    return files


def _pre_step(step: str) -> Callable[[duckdb.DuckDBPyConnection, AppConfig], None] | None:
    if step == "garmin":
        from vitalmart.transform import garmin

        return garmin.prepare
    return None


def rebuild_core(con: duckdb.DuckDBPyConnection, cfg: AppConfig) -> float:
    """Rebuild the core layer from staging. Returns elapsed seconds."""
    t0 = time.perf_counter()
    load_mappings(con, cfg)
    _set_variables(con, cfg)
    for step in STEPS:
        pre = _pre_step(step)
        if pre is not None:
            pre(con, cfg)
        for path in sorted((TRANSFORM_DIR / step).glob("*.sql")):
            log.debug("transform %s/%s", step, path.name)
            con.execute(path.read_text(encoding="utf-8"))
    elapsed = time.perf_counter() - t0
    log.info("core rebuild finished in %.2fs", elapsed)
    return elapsed


def rebuild(con: duckdb.DuckDBPyConnection, cfg: AppConfig) -> float:
    """Transactional full rebuild (``vitalmart rebuild``)."""
    con.execute("BEGIN TRANSACTION")
    try:
        elapsed = rebuild_core(con, cfg)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return elapsed
