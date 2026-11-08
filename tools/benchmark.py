"""Reproducible performance benchmark on SYNTHETIC data (SPEC 10.3 / 8.5, M9).

    uv run python tools/benchmark.py --days 1700 --work /tmp/vm-bench     # ≈ 1 GB export.xml

Steps: generate export → first import (+ core + derived) → forced re-import of the same file
→ full rebuild → report (90 days) HTML + PDF → Explorer queries (all days as weekly × month;
one year of minute-level heart rate as hour × weekday heatmap) → XLSX export.
Prints a Markdown table.
"""

from __future__ import annotations

import argparse
import platform
import resource
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path


def peak_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1024 / 1024 if sys.platform == "darwin" else rss / 1024


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=1700, help="napok száma (1700 ≈ 1 GB)")
    p.add_argument("--hr-interval", type=int, default=1, help="pulzusminta percben")
    p.add_argument("--work", type=Path, required=True, help="munkamappa (törlődik!)")
    p.add_argument("--keep", action="store_true", help="a munkamappa megtartása")
    a = p.parse_args()

    from vitalmart import __version__
    from vitalmart.config import load_config
    from vitalmart.datamart import db
    from vitalmart.export import service, tabular
    from vitalmart.export.pdf import report_pdf
    from vitalmart.importers.service import run_import
    from vitalmart.report.engine import build, default_period
    from vitalmart.report.render import render_html
    from vitalmart.sample_data import SampleSpec, write_export_dir
    from vitalmart.semantic.engine import run
    from vitalmart.semantic.spec import AnalysisSpec
    from vitalmart.transform.rebuild import rebuild_core

    if a.work.exists():
        shutil.rmtree(a.work)
    rows: list[tuple[str, float, str]] = []

    def step(name: str, fn, note=lambda _r: ""):  # type: ignore[no-untyped-def]
        t0 = time.perf_counter()
        r = fn()
        rows.append((name, time.perf_counter() - t0, note(r)))
        print(f"{name}: {rows[-1][1]:.1f} s {rows[-1][2]}", file=sys.stderr, flush=True)
        return r

    spec = SampleSpec(start=date(2020, 1, 1), days=a.days, hr_interval_min=a.hr_interval)
    folder = step("generate", lambda: write_export_dir(a.work / "src", spec))
    xml = folder / "export.xml"
    size_mb = xml.stat().st_size / 1024 / 1024
    cfg = load_config(data_dir=str(a.work / "data"))
    con = db.connect(cfg)
    step("first import (+ core + derived)", lambda: run_import(con, cfg, folder),
         lambda r: f"{r.counts.read:,} records".replace(",", " "))
    step("forced re-import (same file)", lambda: run_import(con, cfg, folder, force=True),
         lambda r: f"new {r.counts.new}, skipped {r.counts.skipped:,}".replace(",", " "))
    step("full rebuild only (core + derived + quality)", lambda: rebuild_core(con, cfg))
    start, end = default_period(con, cfg.default_person, 90) or (None, None)
    assert start and end
    rep = step("report build (90 days)", lambda: build(con, cfg, cfg.default_person, start, end))
    step("report HTML render", lambda: render_html(rep), lambda h: f"{len(h) // 1024} kB")
    step("report PDF", lambda: report_pdf(rep), lambda b: f"{len(b) // 1024} kB")
    daily = AnalysisSpec.model_validate({"topic": "daily", "measures": [{"key": "steps"},
                                         {"key": "resting_hr"}], "dimensions": ["date.month"],
                                         "time_grain": "week", "period": "all", "chart": "line"})
    step("explorer: all days, weekly × month", lambda: run(con, daily),
         lambda r: f"{r.n_rows} rows")
    hr = AnalysisSpec.model_validate({"topic": "heart_rate", "measures": [{"key": "bpm"}],
                                      "dimensions": ["time.hour", "date.day_of_week"],
                                      "time_grain": "none", "period": "last_365",
                                      "chart": "heatmap"})
    step("explorer: 1 year minute HR heatmap", lambda: run(con, hr),
         lambda r: f"{r.n_rows} cells")
    sel = tabular.Selection(tables=["fact_daily_summary", "derived_person_daily"],
                            date_from=end - timedelta(days=364), date_to=end)
    step("XLSX export (1 year, 2 tables)", lambda: service.export_raw(con, cfg, sel, "xlsx"),
         lambda r: f"{r.rows} rows")
    con.close()
    db_mb = (a.work / "data" / "vitalmart.duckdb").stat().st_size / 1024 / 1024

    print(f"# VitalMart benchmark ({__version__})\n")
    print(f"- {platform.system()} {platform.machine()}, Python {platform.python_version()}")
    print(f"- export.xml: {size_mb:,.0f} MB, {a.days} days; database: {db_mb:,.0f} MB; "
          f"peak memory: {peak_mb():,.0f} MB\n".replace(",", " "))
    print("| Step | Time (s) | Note |\n|---|---|---|")
    for name, sec, note in rows:
        print(f"| {name} | {sec:.1f} | {note} |")
    if not a.keep:
        shutil.rmtree(a.work, ignore_errors=True)


if __name__ == "__main__":
    main()
