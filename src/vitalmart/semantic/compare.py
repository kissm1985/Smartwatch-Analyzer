"""Comparison mode (SPEC 8.2/9): the same analysis on two periods side by side with a
difference indicator (absolute, %, standardised difference on daily values)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from vitalmart.i18n import t
from vitalmart.semantic.charts import HU_SEPARATORS
from vitalmart.semantic.engine import run
from vitalmart.semantic.model import SemanticModel
from vitalmart.semantic.spec import AnalysisSpec

PRESETS = ("previous", "month", "year", "custom")


@dataclass(frozen=True)
class PeriodDef:
    label: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass
class Comparison:
    a: PeriodDef
    b: PeriodDef
    table: pd.DataFrame                  # dims…, measure, unit, a, b, diff, diff_pct, n_a, n_b, d
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)   # day, measure, a, b
    dim_labels: list[str] = field(default_factory=list)


def preset(kind: str, end: date, days: int = 30) -> tuple[PeriodDef, PeriodDef]:
    """(A = reference, B = current). ``end`` is the last day of the current period."""
    if kind == "month":
        b0 = end.replace(day=1)
        a1 = b0 - timedelta(days=1)
        a0 = a1.replace(day=1)
        return (PeriodDef(t("compare.prev_month"), a0, a1),
                PeriodDef(t("compare.this_month"), b0, end))
    if kind == "year":
        b0 = end - timedelta(days=days - 1)
        try:
            a0, a1 = b0.replace(year=b0.year - 1), end.replace(year=end.year - 1)
        except ValueError:                       # 29 February
            a0, a1 = b0 - timedelta(days=365), end - timedelta(days=365)
        return (PeriodDef(t("compare.prev_year"), a0, a1),
                PeriodDef(t("compare.current"), b0, end))
    b0 = end - timedelta(days=days - 1)
    a1 = b0 - timedelta(days=1)
    return (PeriodDef(t("compare.previous", n=days), a1 - timedelta(days=days - 1), a1),
            PeriodDef(t("compare.current_n", n=days), b0, end))


def _period_spec(spec: AnalysisSpec, p: PeriodDef, grain: str, agg: str | None = None
                 ) -> AnalysisSpec:
    upd: dict = {"period": "custom", "date_from": p.start, "date_to": p.end,
                 "time_grain": grain, "transform": "none", "chart": "table",
                 "show_events": False, "facet": None, "secondary_measure": None}
    s = spec.model_copy(update=upd, deep=True)
    if agg:
        for sel in s.measures:
            sel.agg = agg
    return s


def _int(v: object) -> int:
    return 0 if v is None or pd.isna(v) else int(v)  # type: ignore[call-overload]


def _cohen_d(x: np.ndarray, y: np.ndarray) -> float | None:
    x, y = x[~np.isnan(x)], y[~np.isnan(y)]
    if len(x) < 3 or len(y) < 3:
        return None
    sp = math.sqrt(((len(x) - 1) * x.var(ddof=1) + (len(y) - 1) * y.var(ddof=1))
                   / (len(x) + len(y) - 2))
    return None if sp == 0 else float((y.mean() - x.mean()) / sp)


def compare(con: duckdb.DuckDBPyConnection, spec: AnalysisSpec, model: SemanticModel,
            a: PeriodDef, b: PeriodDef) -> Comparison:
    if spec.time_grain in ("raw", "hour") or any(spec.agg_of(m, model) == "none"
                                                 for m in spec.measures):
        spec = spec.model_copy(update={"time_grain": "day"}, deep=True)
        for sel in spec.measures:
            if sel.agg == "none":
                sel.agg = None
    topic = model.topics[spec.topic]
    dims = [f"d{i}" for i in range(len(spec.dimensions))]
    frames = {}
    for key, p in (("a", a), ("b", b)):
        val = run(con, _period_spec(spec, p, "none"), model).df
        cnt = run(con, _period_spec(spec, p, "none", agg="count"), model).df
        frames[key] = val.merge(cnt, on=dims, how="outer", suffixes=("", "_n")) if dims \
            else pd.concat([val, cnt.add_suffix("_n")], axis=1)
    merged = frames["a"].merge(frames["b"], on=dims, how="outer", suffixes=("_a", "_b")) \
        if dims else pd.concat([frames["a"].add_suffix("_a"), frames["b"].add_suffix("_b")],
                               axis=1)

    daily_vals: dict[str, dict[str, pd.DataFrame]] = {}
    if not dims:
        for key, p in (("a", a), ("b", b)):
            d = run(con, _period_spec(spec, p, "day"), model).df
            daily_vals[key] = {"df": d, "start": pd.Timestamp(p.start)}

    rows = []
    for i, sel in enumerate(spec.measures):
        m = topic.measures[sel.key]
        for _, r in merged.iterrows():
            va, vb = r.get(f"m{i}_a"), r.get(f"m{i}_b")
            va = None if pd.isna(va) else float(va)
            vb = None if pd.isna(vb) else float(vb)
            diff = vb - va if va is not None and vb is not None else None
            pct = diff / abs(va) * 100 if diff is not None and va else None
            dval = None
            if daily_vals:
                dval = _cohen_d(daily_vals["a"]["df"][f"m{i}"].astype(float).to_numpy(),
                                daily_vals["b"]["df"][f"m{i}"].astype(float).to_numpy())
            rows.append({**{c: r[c] for c in dims}, "measure": m.label_hu, "unit": m.unit,
                         "a": va, "b": vb, "diff": diff, "diff_pct": pct,
                         "n_a": _int(r.get(f"m{i}_n_a")), "n_b": _int(r.get(f"m{i}_n_b")),
                         "d": dval})
    table = pd.DataFrame(rows)

    daily = pd.DataFrame()
    if daily_vals:
        parts = []
        for key in ("a", "b"):
            d = daily_vals[key]["df"].copy()
            if d.empty:
                continue
            d["day"] = (pd.to_datetime(d["period"]) - daily_vals[key]["start"]).dt.days + 1
            for i, ms in enumerate(spec.measures):
                parts.append(pd.DataFrame({"day": d["day"], "date": pd.to_datetime(d["period"]),
                                           "measure": topic.measures[ms.key].label_hu,
                                           "period": a.label if key == "a" else b.label,
                                           "value": d[f"m{i}"].astype(float)}))
        daily = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return Comparison(a, b, table, daily,
                      [model.dimensions[dk].label_hu for dk in spec.dimensions])


# ---------------------------------------------------------------- presentation

def display_table(c: Comparison) -> pd.DataFrame:
    from vitalmart.fmt import fmt_num, fmt_signed

    df = c.table.copy()
    out = pd.DataFrame()
    for i, lab in enumerate(c.dim_labels):
        out[lab] = df[f"d{i}"].astype(str)
    out[t("compare.col.measure")] = [f"{m} [{u}]" if u else m
                                     for m, u in zip(df["measure"], df["unit"], strict=True)]
    out[c.a.label] = df["a"].map(lambda v: fmt_num(v, 2))
    out[c.b.label] = df["b"].map(lambda v: fmt_num(v, 2))
    out[t("compare.col.diff")] = df["diff"].map(lambda v: fmt_signed(v, 2))
    out[t("compare.col.diff_pct")] = df["diff_pct"].map(
        lambda v: "–" if v is None or pd.isna(v) else fmt_signed(v, 1) + " %")
    out[t("compare.col.n")] = [f"{x} / {y}" for x, y in zip(df["n_a"], df["n_b"], strict=True)]
    if df["d"].notna().any():
        out[t("compare.col.d")] = df["d"].map(lambda v: fmt_signed(v, 2))
    return out


def bar_figure(c: Comparison) -> go.Figure:
    df = c.table.copy()
    long = pd.concat([df.assign(period=c.a.label, value=df["a"]),
                      df.assign(period=c.b.label, value=df["b"])], ignore_index=True)
    long["value"] = long["value"].astype(float).round(4)
    x = "d0" if "d0" in long.columns else "measure"
    if "d0" in long.columns:
        long["d0"] = long["d0"].astype(str)
    multi = df["measure"].nunique() > 1 and x != "measure"
    fig = px.bar(long, x=x, y="value", color="period", barmode="group",
                 facet_col="measure" if multi else None, facet_col_wrap=2,
                 labels={"value": "", x: c.dim_labels[0] if x == "d0" else "",
                         "period": "", "measure": ""},
                 color_discrete_sequence=["#94a3b8", "#2563eb"])
    if multi:
        fig.update_yaxes(matches=None, showticklabels=True)
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    fig.update_layout(separators=HU_SEPARATORS, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      legend_title_text="")
    return fig


def overlay_figure(c: Comparison) -> go.Figure | None:
    if c.daily.empty:
        return None
    d = c.daily.copy()
    d["value"] = d["value"].round(4)
    multi = d["measure"].nunique() > 1
    fig = px.line(d, x="day", y="value", color="period", facet_row="measure" if multi else None,
                  labels={"day": t("compare.day_index"), "value": "", "period": "",
                          "measure": ""},
                  color_discrete_sequence=["#94a3b8", "#2563eb"], markers=True)
    if multi:
        fig.update_yaxes(matches=None)
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    fig.update_layout(separators=HU_SEPARATORS, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      legend_title_text="")
    return fig

