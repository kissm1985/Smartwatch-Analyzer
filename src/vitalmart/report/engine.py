"""Report engine: data → rules → sections → HTML; snapshots for reproducibility (SPEC 7)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from vitalmart import __version__
from vitalmart.config import AppConfig
from vitalmart.fmt import fmt_date, fmt_num, fmt_signed
from vitalmart.i18n import t
from vitalmart.report import data as rdata
from vitalmart.report import sections as sx
from vitalmart.report.render import render_html
from vitalmart.report.rules import Catalog, Finding, correlations, evaluate, load_catalog
from vitalmart.report.sections import metric_name

SECTION_ORDER = ["sleep", "heart", "stress", "activity", "respiration", "body", "correlations",
                 "anomalies"]


@dataclass
class Report:
    person: str
    person_name: str
    period: rdata.Period
    cmp: rdata.Period
    generated_at: datetime
    data_version: str
    rules_version: int
    params: dict[str, Any]
    coverage_pct: float
    sources: list[dict[str, str]]
    summary: list[Finding]
    changes: list[list[str]]
    sections: list[sx.Section]
    focus: list[str]
    methodology: list[str]
    thresholds: list[list[str]]
    review_note: str
    disclaimer: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def section_map(self) -> dict[str, sx.Section]:
        return {s.key: s for s in self.sections}


def data_version(con: duckdb.DuckDBPyConnection, person: str, start: date, end: date) -> str:
    """Content hash of everything the report reads (rounded → stable across rebuilds)."""
    h = hashlib.sha256()
    for sql in (
        """SELECT string_agg(metric_key || '|' || date || '|' || round(value, 6)::VARCHAR, ';'
                             ORDER BY metric_key, date)
           FROM derived_person_daily WHERE person_key = ? AND date BETWEEN ? AND ?""",
        """SELECT string_agg(a.activity_id || '|' || coalesce(round(a.duration_min, 3), 0)::VARCHAR,
                             ';' ORDER BY a.activity_id)
           FROM fact_activity a JOIN dim_source s USING (source_key)
           WHERE s.person_key = ? AND CAST(a.start_local AS DATE) BETWEEN ? AND ?""",
    ):
        row = con.execute(sql, [person, start, end]).fetchall()
        h.update((row[0][0] or "").encode())
    return h.hexdigest()[:16]


def _changes(d: rdata.ReportData, need: int) -> list[list[str]]:
    rows: list[list[str]] = []
    specs = [("resting_hr", "bpm", 1), *[(m, "ms", 0) for m in d.hrv_metrics()],
             ("sleep_min", "perc", 0), ("steps", "lépés", 0), ("who_equiv_min", "perc/nap", 0),
             ("stress_avg", "pont", 0)]
    for m, unit, digits in specs:
        cur, prev = d.series(m), d.series(m, prev=True)
        if len(cur) < need:
            continue
        rows.append([metric_name(m), f"{fmt_num(cur.mean(), digits)} {unit}",
                     f"{fmt_num(prev.mean(), digits)} {unit}" if len(prev) >= need else "–",
                     fmt_signed(cur.mean() - prev.mean(), digits) if len(prev) >= need else "–"])
    return rows


def _methodology(d: rdata.ReportData, cfg: AppConfig, cat: Catalog) -> list[str]:
    hrv = ", ".join(m.replace("hrv_", "").upper() for m in d.hrv_metrics()) or "–"
    return [
        t("report.m.sources", sources=", ".join(sorted(set(d.sources["vendor"])))
          if len(d.sources) else "–"),
        t("report.m.primary", order=" > ".join(cfg.derived.source_priority)),
        t("report.m.baseline", days=cfg.derived.baseline_window_days,
          min=cfg.derived.baseline_min_days, z=fmt_num(cfg.derived.band_z, 1)),
        t("report.m.hrv", methods=hrv),
        t("report.m.sleep", conv=t(f"ui.settings.conv_{cfg.sleep.day_convention}")),
        t("report.m.who"),
        t("report.m.confidence"),
        t("report.m.correlation"),
        t("report.m.limits"),
        t("report.m.deterministic"),
    ]


def build(con: duckdb.DuckDBPyConnection, cfg: AppConfig, person: str, start: date, end: date,
          *, generated_at: datetime | None = None) -> Report:
    cat = load_catalog(cfg.config_dir, cfg.data_path)
    d = rdata.load(con, person, start, end)
    need = int(cat.params["min_valid_days"])
    findings = evaluate(d, cat)

    load_series = (pd.Series(d.load["load"].values, index=pd.to_datetime(d.load["date"]))
                   if len(d.load) else pd.Series(dtype=float))
    cors = correlations(d, cat, extra=lambda _m: load_series)
    series = {c: d.series(c) for c in d.daily.columns}
    series["training_load"] = load_series

    sections = [
        sx.sleep_section(d, cat, cfg.sleep.goal_hours),
        sx.heart_section(d, cat),
        sx.stress_section(d, cat),
        sx.activity_section(d, cat),
        sx.respiration_section(d, cat),
        sx.body_section(d, cat),
        sx.correlation_section(d, cors, series),
        sx.anomaly_section(d, cat),
    ]
    by_key = {s.key: s for s in sections}
    chart_ids = {c.chart_id for s in sections for c in s.charts}
    for f in findings:
        if f.chart_id not in chart_ids:
            f.chart_id = None
        if f.section in by_key and by_key[f.section].available:
            by_key[f.section].findings.append(f)
    findings = [f for f in findings if f.section in by_key and by_key[f.section].available]

    key_metrics = ["sleep_min", "resting_hr", "steps", *d.hrv_metrics(), "stress_avg"]
    days_with = d.daily.loc[pd.Timestamp(start):pd.Timestamp(end), [
        m for m in key_metrics if m in d.daily.columns]].notna().any(axis=1).sum() \
        if len(d.daily) else 0
    coverage = 100.0 * float(days_with) / d.period.days

    focus = [f.focus for f in findings if f.severity in ("attention", "consult") and f.focus][:5]
    thresholds = [[p.get("label_hu", k), f"{fmt_num(cat.params[k], 1 if cat.params[k] % 1 else 0)} "
                   f"{p.get('unit', '')}".strip()] for k, p in cat.param_labels.items()]
    review = (t("report.review.done", by=cat.meta["reviewed_by"], at=cat.meta.get("reviewed_at"))
              if cat.meta.get("reviewed_by") else t("report.review.not_requested")
              if cat.meta.get("review_status") == "not_requested" else t("report.review.missing"))
    used_sources = set(d.daily_source.values())
    srcs = [{"name": str(r.source_name), "vendor": str(r.vendor), "type": str(r.device_type)}
            for r in d.sources.itertuples() if r.source_key in used_sources]
    return Report(
        person=person, person_name=d.person_name, period=d.period, cmp=d.cmp,
        generated_at=generated_at or datetime.now().replace(microsecond=0),
        data_version=data_version(con, person, d.cmp.start, end), rules_version=cat.version,
        params={k: cat.params[k] for k in sorted(cat.params)}, coverage_pct=coverage,
        sources=srcs, summary=findings[:8], changes=_changes(d, need), sections=sections,
        focus=focus, methodology=_methodology(d, cfg, cat), thresholds=thresholds,
        review_note=review, disclaimer=t("app.disclaimer"), findings=findings)


# ---------------------------------------------------------------- snapshots / files

def _snapshot_params(person: str, start: date, end: date) -> dict[str, Any]:
    return {"person": person, "start": start.isoformat(), "end": end.isoformat()}


def generate(con: duckdb.DuckDBPyConnection, cfg: AppConfig, person: str, start: date,
             end: date, *, out: Path | None = None, generated_at: datetime | None = None,
             save_snapshot: bool = True) -> tuple[Report, str, Path]:
    rep = build(con, cfg, person, start, end, generated_at=generated_at)
    html = render_html(rep)
    out = out or (cfg.exports_path / "reports" /
                  f"riport_{person}_{start:%Y%m%d}_{end:%Y%m%d}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    if save_snapshot:
        con.execute("""INSERT INTO report_snapshot (snapshot_id, created_at, person_key, date_from,
                       date_to, cmp_from, cmp_to, params, data_version, rules_version,
                       app_version, html_path, html_sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [hashlib.sha256(f"{person}{start}{end}{rep.generated_at}".encode())
                     .hexdigest()[:12], rep.generated_at, person, start, end, rep.cmp.start,
                     rep.cmp.end, json.dumps({**_snapshot_params(person, start, end),
                                              "rule_params": rep.params}, sort_keys=True),
                     rep.data_version, rep.rules_version, __version__, str(out),
                     hashlib.sha256(html.encode()).hexdigest()])
    return rep, html, out


def regenerate(con: duckdb.DuckDBPyConnection, cfg: AppConfig, snapshot_id: str,
               out: Path | None = None) -> tuple[Report, str, bool]:
    """Re-render a snapshot with its parameters and timestamp. Returns (report, html,
    identical) – identical is True when the data version and rule version still match."""
    row = con.execute("""SELECT person_key, date_from, date_to, created_at, data_version,
                                rules_version, html_sha256
                         FROM report_snapshot WHERE snapshot_id = ?""", [snapshot_id]).fetchall()
    if not row:
        raise KeyError(snapshot_id)
    person, start, end, created, dv, rv, sha = row[0]
    rep = build(con, cfg, person, start, end, generated_at=created)
    html = render_html(rep)
    if out:
        out.write_text(html, encoding="utf-8")
    same = (rep.data_version == dv and rep.rules_version == rv
            and hashlib.sha256(html.encode()).hexdigest() == sha)
    return rep, html, same


def list_snapshots(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""SELECT snapshot_id, created_at, person_key, date_from, date_to,
                                 data_version, rules_version, html_path
                          FROM report_snapshot ORDER BY created_at DESC""").df()


def default_period(con: duckdb.DuckDBPyConnection, person: str, days: int = 90
                   ) -> tuple[date, date] | None:
    end = rdata.last_data_day(con, person)
    if end is None:
        return None
    return end - timedelta(days=days - 1), end


def finding_label(f: Finding) -> str:
    return t(f"report.severity.{'positive' if f.positive else f.severity}")


def period_text(rep: Report) -> str:
    return f"{fmt_date(rep.period.start)} – {fmt_date(rep.period.end)} ({rep.period.days} nap)"
