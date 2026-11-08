"""Self-check on a real export (M9): run every stage of the pipeline in a throw-away data
directory and produce a Markdown report that is SAFE TO SHARE.

The report contains only technical information: record-type names, row counts, numbers of
days, quality-flag counts, timings, section availability and error types. It never contains
measured values, dates, device / source names, serial numbers or free text; error messages
are masked (numbers and quoted strings replaced). The user's own database is not touched.
"""

from __future__ import annotations

import platform
import re
import resource
import shutil
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import duckdb

from vitalmart import __version__
from vitalmart.config import AppConfig, load_config

CORE_TABLES = ["fact_measurement", "fact_heart_rate_sample", "fact_stress_sample",
               "fact_daily_summary", "fact_sleep_session", "fact_sleep_stage_interval",
               "fact_hrv_nightly", "fact_activity", "fact_activity_hr_zone",
               "fact_body_measure", "fact_vo2max", "derived_person_daily",
               "derived_daily_stats", "derived_weekly_activity", "derived_sleep_regularity",
               "derived_training_load", "dim_source", "dim_metric", "dim_activity_type"]


def mask(text: str) -> str:
    """Remove anything value-like from an error message."""
    text = re.sub(r"'[^']*'", "'…'", text)
    text = re.sub(r'"[^"]*"', '"…"', text)
    text = re.sub(r"(?<![A-Za-z_])\d+([.,:\-/]\d+)*", "#", text)
    return text[:300]


@dataclass
class Stage:
    name: str
    ok: bool = True
    seconds: float = 0.0
    detail: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class Diagnosis:
    stages: list[Stage] = field(default_factory=list)
    tables: list[tuple[str, int]] = field(default_factory=list)
    sections: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.stages)


@contextmanager
def _stage(diag: Diagnosis, name: str) -> Iterator[Stage]:
    st = Stage(name)
    diag.stages.append(st)
    t0 = time.perf_counter()
    try:
        yield st
    except Exception as exc:
        st.ok = False
        frames = [f"{Path(f.filename).name}:{f.lineno}"
                  for f in traceback.extract_tb(exc.__traceback__)
                  if "vitalmart" in f.filename][-4:]
        st.error = f"{type(exc).__name__}: {mask(str(exc).splitlines()[0] if str(exc) else '')}" \
                   + (f" ({' ← '.join(reversed(frames))})" if frames else "")
    finally:
        st.seconds = time.perf_counter() - t0


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
          ) -> list[tuple[Any, ...]]:
    return con.execute(sql, params or []).fetchall()


def run(path: Path, *, person: str = "me", work_dir: Path | None = None,
        progress: Callable[[str], None] | None = None, quick: bool = False) -> Diagnosis:
    """Run the pipeline on ``path`` in ``work_dir`` (a new temp dir by default)."""
    from vitalmart.datamart import db
    from vitalmart.importers.service import preview, run_import

    say = progress or (lambda _m: None)
    diag = Diagnosis()
    tmp = Path(work_dir or tempfile.mkdtemp(prefix="vitalmart-diag-"))
    cfg: AppConfig = load_config(data_dir=str(tmp / "data"))
    con = db.connect(cfg)
    try:
        say("preview")
        with _stage(diag, "preview") as st:
            pv = preview(path)
            st.detail.append(f"adapter: {pv.discovery.adapter}")
            kinds: dict[str, int] = {}
            for f in pv.discovery.files:
                kinds[f.kind] = kinds.get(f.kind, 0) + 1
            st.detail += [f"files `{k}`: {n}" for k, n in sorted(kinds.items())]
            st.detail += [f"warning: {mask(w)}" for w in pv.discovery.warnings]
            st.detail.append(f"records: {pv.total_records}")
            for typ, n in sorted(pv.type_counts.items(), key=lambda kv: -kv[1])[:60]:
                st.detail.append(f"type `{typ}`: {n}")

        say("import")
        with _stage(diag, "import + rebuild") as st:
            res = run_import(con, cfg, path, person=person)
            c = res.counts
            st.detail += [f"status: {res.status}", f"read: {c.read}", f"new: {c.new}",
                          f"skipped: {c.skipped}", f"errors: {c.errors}"]
            if res.date_from and res.date_to:
                st.detail.append(f"span: {(res.date_to - res.date_from).days + 1} days")
            if res.status != "success":
                st.ok = False
                st.error = mask(res.message)
        if not diag.stages[-1].ok:
            return diag

        say("reimport")
        with _stage(diag, "idempotent re-import") as st:
            before = {t: _rows(con, f"SELECT count(*) FROM {t}")[0][0]
                      for t in ("fact_measurement", "fact_sleep_session", "fact_activity")}
            r2 = run_import(con, cfg, path, person=person, force=True)
            after = {t: _rows(con, f"SELECT count(*) FROM {t}")[0][0] for t in before}
            st.detail.append(f"status: {r2.status}")
            st.ok = before == after and r2.status == "success"
            if before != after:
                st.error = "row counts changed on re-import"

        with _stage(diag, "tables") as st:
            for tname in CORE_TABLES:
                diag.tables.append((tname, _rows(con, f"SELECT count(*) FROM {tname}")[0][0]))
            for tname, flag, n in _rows(con, """
                    SELECT fact_table, quality_flag, sum(n) FROM dq_flag_summary
                    GROUP BY ALL ORDER BY ALL"""):
                st.detail.append(f"quality `{tname}` {flag}: {n}")
            for (metric,) in _rows(con, """SELECT metric_key FROM dim_metric
                                           WHERE is_auto ORDER BY 1"""):
                st.detail.append(f"auto metric (unknown type kept): `{metric}`")
            for vendor, typ, n in _rows(con, """SELECT vendor, device_type, count(*)
                                                FROM dim_source GROUP BY ALL ORDER BY ALL"""):
                st.detail.append(f"sources {vendor}/{typ}: {n}")
            for method, n in _rows(con, """SELECT hrv_method, count(*) FROM fact_hrv_nightly
                                           GROUP BY ALL ORDER BY ALL"""):
                st.detail.append(f"HRV method {method}: {n} nights")
            for metric, days in _rows(con, """SELECT metric_key, count(*) FROM derived_person_daily
                                              WHERE person_key = ? GROUP BY ALL ORDER BY 1""",
                                      [person]):
                st.detail.append(f"daily `{metric}`: {days} days")
            gaps = _rows(con, "SELECT count(*) FROM dq_gap")[0][0]
            nonwear = _rows(con, "SELECT count(*) FROM dq_nonwear")[0][0]
            st.detail += [f"gaps: {gaps}", f"non-wear periods: {nonwear}"]

        from vitalmart.report.engine import build, default_period
        rng = default_period(con, person, 90)
        rep = None
        say("report")
        with _stage(diag, "report (HTML, last 90 days)") as st:
            if rng is None:
                raise RuntimeError("no derived data for the person")
            from vitalmart.report.render import render_html
            rep = build(con, cfg, person, *rng)
            html = render_html(rep)
            st.detail.append(f"html: {len(html) // 1024} kB")
            sev: dict[str, int] = {}
            for fnd in rep.findings:
                k = "positive" if fnd.positive else fnd.severity
                sev[k] = sev.get(k, 0) + 1
            st.detail.append("findings: " + ", ".join(f"{k} {v}" for k, v in sorted(sev.items()))
                             if sev else "findings: 0")
            st.detail.append(f"coverage: {rep.coverage_pct:.0f} %")
            for s in rep.sections:
                diag.sections.append((s.key, "ok" if s.available else "n/a"))

        if rep is not None and not quick:
            say("pdf")
            with _stage(diag, "report PDF") as st:
                from vitalmart.export.pdf import report_pdf
                pdf = report_pdf(rep)
                st.detail.append(f"pdf: {len(pdf) // 1024} kB")

        say("explorer")
        with _stage(diag, "explorer: built-in analyses") as st:
            from vitalmart.semantic import store
            from vitalmart.semantic.charts import build_figure
            from vitalmart.semantic.engine import run as run_spec
            failed = []
            for a in store.list_analyses(con):
                t0 = time.perf_counter()
                try:
                    spec = a.spec.model_copy(update={"persons": [person]}) \
                        if "person" not in a.spec.dimensions else a.spec
                    r = run_spec(con, spec)
                    build_figure(r)
                    st.detail.append(f"`{a.analysis_id}`: {r.n_rows} rows, "
                                     f"{time.perf_counter() - t0:.2f} s")
                except Exception as exc:
                    failed.append(a.analysis_id)
                    st.detail.append(f"`{a.analysis_id}`: FAILED {type(exc).__name__}: "
                                     f"{mask(str(exc).splitlines()[0] if str(exc) else '')}")
            if failed:
                st.ok = False
                st.error = f"{len(failed)} analyses failed"

        if not quick:
            say("export")
            with _stage(diag, "exports (XLSX, CSV, Parquet)") as st:
                from vitalmart.export import service, tabular
                reg = tabular.load_registry()
                end = rng[1] if rng else None
                sel = tabular.Selection(tables=reg.default_tables, persons=[person],
                                        date_from=end - timedelta(days=89) if end else None,
                                        date_to=end)
                for fmt in ("xlsx", "csv", "parquet"):
                    t0 = time.perf_counter()
                    out = service.export_raw(con, cfg, sel, fmt)
                    st.detail.append(f"{fmt}: {out.rows} rows, {out.path.stat().st_size // 1024}"
                                     f" kB, {time.perf_counter() - t0:.2f} s")

        say("advanced")
        with _stage(diag, "advanced (lag, comparison, SQL console)") as st:
            from vitalmart import sqlconsole
            from vitalmart.semantic import compare, lag
            from vitalmart.semantic.model import load_model
            from vitalmart.semantic.spec import AnalysisSpec
            model = load_model()
            x = lag.daily_series(con, model, lag.SeriesRef("daily", "steps"), person)
            y = lag.daily_series(con, model, lag.SeriesRef("daily", "sleep_hours"), person)
            prof = lag.lag_profile(x, y, range(-7, 8))
            st.detail.append(f"lag profile: {int(prof['spearman'].notna().sum())}/15 lags "
                             "computable")
            if rng:
                pa, pb = compare.preset("previous", rng[1], 30)
                spec = AnalysisSpec.model_validate({"topic": "daily", "persons": [person],
                                                    "measures": [{"key": "steps"},
                                                                 {"key": "resting_hr"}]})
                cmp = compare.compare(con, spec, model, pa, pb)
                st.detail.append(f"comparison rows: {len(cmp.table)}")
            for key, sql in sqlconsole.EXAMPLES.items():
                sq = sqlconsole.execute(con, sql)
                st.detail.append(f"sql `{key}`: {len(sq.df)} rows")
    finally:
        con.close()
        if work_dir is None:
            shutil.rmtree(tmp, ignore_errors=True)
    return diag


def to_markdown(diag: Diagnosis, path: Path | None = None) -> str:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / 1024 / 1024 if sys.platform == "darwin" else rss / 1024
    size = f"{path.stat().st_size / 1024 / 1024:.1f} MB" if path and path.exists() \
        and path.is_file() else "folder"
    lines = ["# VitalMart diagnosztika", "",
             "_Értékeket, dátumokat, eszköz- és forrásneveket nem tartalmaz – megosztható._", "",
             f"- VitalMart {__version__}, Python {platform.python_version()}, "
             f"{platform.system()} {platform.machine()}, DuckDB {duckdb.__version__}",
             f"- bemenet: {size}; csúcsmemória: {rss_mb:.0f} MB",
             f"- eredmény: {'**rendben**' if diag.ok else '**HIBA**'}", "",
             "| Lépés | Állapot | Idő (s) |", "|---|---|---|"]
    for s in diag.stages:
        lines.append(f"| {s.name} | {'✅' if s.ok else '❌'} | {s.seconds:.1f} |")
    for s in diag.stages:
        lines += ["", f"## {s.name}"]
        if s.error:
            lines.append(f"**Hiba:** `{s.error}`")
        lines += [f"- {d}" for d in s.detail]
    if diag.tables:
        lines += ["", "## Táblák (sorok száma)", "", "| Tábla | Sor |", "|---|---|"]
        lines += [f"| {tname} | {n} |" for tname, n in diag.tables]
    if diag.sections:
        lines += ["", "## Riportszekciók", ""]
        lines += [f"- {k}: {v}" for k, v in diag.sections]
    return "\n".join(lines) + "\n"
