"""Report sections (SPEC 7.2): facts, charts and findings per section."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from vitalmart.fmt import fmt_clock, fmt_date, fmt_hm, fmt_num, fmt_signed
from vitalmart.i18n import has, t
from vitalmart.report.data import ReportData
from vitalmart.report.rules import Catalog, Correlation, Finding, social_jetlag

COLORS = {"main": "#2563eb", "soft": "#93c5fd", "band": "rgba(37,99,235,0.12)",
          "goal": "#16a34a", "warn": "#dc2626", "grey": "#6b7280"}


@dataclass
class Chart:
    chart_id: str
    title: str
    fig: go.Figure


@dataclass
class Section:
    key: str
    title: str
    available: bool = True
    reason: str = ""
    facts: list[tuple[str, str]] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    table: list[list[str]] = field(default_factory=list)
    table_head: list[str] = field(default_factory=list)


def _r(v: Any) -> Any:
    """Round chart data → byte-identical re-renders (float noise in sums)."""
    if isinstance(v, pd.Series | np.ndarray | list):
        return [None if x is None or (isinstance(x, float) and np.isnan(x)) else
                round(float(x), 4) for x in v]
    return v


def _fig(height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template="plotly_white", height=height, separators=", ",
                      margin={"l": 50, "r": 20, "t": 30, "b": 40}, showlegend=True,
                      legend={"orientation": "h", "y": -0.2},
                      font={"family": "DejaVu Sans, Noto Sans, Arial, sans-serif", "size": 12})
    fig.update_xaxes(tickformat="%Y.%m.%d.", hoverformat="%Y.%m.%d.")
    return fig


def _dates(s: pd.Series) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in s.index]


def _trend_chart(cid: str, title: str, s: pd.Series, *, unit: str, stats: pd.DataFrame | None,
                 goal: float | None = None, goal_label: str = "") -> Chart:
    fig = _fig()
    if stats is not None and len(stats) and stats["bl_mean"].notna().any():
        st = stats.reindex(s.index)
        lo = st["bl_mean"] - 1.5 * st["bl_std"]
        hi = st["bl_mean"] + 1.5 * st["bl_std"]
        fig.add_trace(go.Scatter(x=_dates(s), y=_r(hi), mode="lines", line={"width": 0},
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=_dates(s), y=_r(lo), mode="lines", line={"width": 0},
                                 fill="tonexty", fillcolor=COLORS["band"],
                                 name="alapvonal-sáv (±1,5 szórás)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=_dates(s), y=_r(s.values), mode="markers+lines",
                             line={"color": COLORS["soft"], "width": 1}, marker={"size": 4},
                             name="napi érték"))
    ma = s.rolling("7D").mean()
    fig.add_trace(go.Scatter(x=_dates(ma), y=_r(ma.values), mode="lines",
                             line={"color": COLORS["main"], "width": 2.5},
                             name="7 napos mozgóátlag"))
    if goal is not None:
        fig.add_hline(y=goal, line={"color": COLORS["goal"], "dash": "dash"},
                      annotation_text=goal_label, annotation_position="top left")
    fig.update_yaxes(title_text=unit)
    return Chart(cid, title, fig)


def metric_name(key: str) -> str:
    if has(f"metric.{key}"):
        return t(f"metric.{key}")
    return t(f"report.metric.{key}") if has(f"report.metric.{key}") else key


def _not_enough(key: str, title: str, n: int, need: int) -> Section:
    return Section(key, title, available=False,
                   reason=t("report.not_enough", n=n, need=need))


# ---------------------------------------------------------------- sleep

def sleep_section(d: ReportData, cat: Catalog, goal_h: float) -> Section:
    title = t("report.section.sleep")
    s = d.series("sleep_min")
    need = int(cat.params["min_valid_days"])
    if len(s) < need:
        return _not_enough("sleep", title, len(s), need)
    sec = Section("sleep", title)
    min_h = cat.params["sleep_min_hours"]
    debt = d.regularity["debt7_min"].dropna() if len(d.regularity) else pd.Series(dtype=float)
    bed_std = d.regularity["bedtime_std7"].dropna() if len(d.regularity) else pd.Series(dtype=float)
    jet, _ = social_jetlag(d)
    weekend = s.index.day_of_week >= 5
    sec.facts = [
        (t("report.f.nights"), f"{len(s)} / {d.period.days}"),
        (t("report.f.sleep_mean"), fmt_hm(s.mean())),
        (t("report.f.sleep_median_range"),
         f"{fmt_hm(s.median())} ({fmt_hm(s.quantile(0.1))} – {fmt_hm(s.quantile(0.9))})"),
        (t("report.f.sleep_goal_share", h=fmt_num(min_h, 1 if min_h % 1 else 0)),
         f"{fmt_num(100 * (s >= min_h * 60).mean(), 0)} %"),
        (t("report.f.sleep_weekday_weekend"),
         f"{fmt_hm(s[~weekend].mean())} / {fmt_hm(s[weekend].mean()) if weekend.any() else '–'}"),
        (t("report.f.bedtime"), fmt_clock((d.series('bedtime_min').mean() / 60 + 12)
                                          if len(d.series('bedtime_min')) else None)),
        (t("report.f.waketime"), fmt_clock(d.series('waketime_min').mean() / 60
                                           if len(d.series('waketime_min')) else None)),
        (t("report.f.bedtime_std"), f"{fmt_num(bed_std.mean(), 0)} perc" if len(bed_std) else "–"),
        (t("report.f.social_jetlag"), f"{fmt_signed(jet, 0)} perc" if jet is not None else "–"),
        (t("report.f.sleep_debt7", h=fmt_num(goal_h, 1)),
         fmt_hm(debt.iloc[-1]) if len(debt) and debt.iloc[-1] > 0 else "0 ó 00 p"),
    ]
    eff = d.series("sleep_efficiency")
    if len(eff):
        sec.facts.append((t("report.f.efficiency"), f"{fmt_num(eff.mean(), 0)} %"))
    awake = d.series("sleep_awake_min")
    if len(awake):
        sec.facts.append((t("report.f.awake"), f"{fmt_num(awake.mean(), 0)} perc"))
    prev = d.series("sleep_min", prev=True)
    if len(prev) >= need:
        sec.facts.append((t("report.f.vs_prev"), f"{fmt_signed(s.mean() - prev.mean(), 0)} perc"))
    sec.charts.append(_trend_chart("sleep_duration", t("report.c.sleep_duration"), s / 60,
                                   unit="óra", stats=None, goal=min_h,
                                   goal_label=f"{fmt_num(min_h, 1)} óra"))
    hist = _fig(260)
    hist.add_trace(go.Histogram(x=_r((s / 60).values), xbins={"size": 0.5},
                                marker_color=COLORS["main"], name="éjszakák"))
    hist.update_xaxes(title_text="óra", tickformat=None)
    hist.update_layout(showlegend=False)
    sec.charts.append(Chart("sleep_hist", t("report.c.sleep_hist"), hist))
    # stages (source-dependent labelling)
    deep, light, rem = (d.series(m) for m in ("sleep_deep_min", "sleep_light_min", "sleep_rem_min"))
    if len(deep) >= need:
        both = pd.concat([deep, light, rem, awake], axis=1,
                         keys=["deep", "light", "rem", "awake"]).dropna(subset=["deep"])
        tot = both[["deep", "light", "rem"]].sum(axis=1)
        share = (both[["deep", "light", "rem"]].div(tot, axis=0) * 100).mean()
        src = d.daily_source.get("sleep_deep_min", "")
        sec.facts.append((t("report.f.stages", source=src.split(":")[0].capitalize()),
                          f"{fmt_num(share['deep'], 0)} / {fmt_num(share['light'], 0)} / "
                          f"{fmt_num(share['rem'], 0)} %"))
        sec.paragraphs.append(t("report.p.stage_note"))
        wk = both.resample("W-MON", label="left", closed="left").mean()
        fig = _fig(280)
        for col, name, color in (("deep", "mély", "#1e3a8a"), ("light", "könnyű/core", "#60a5fa"),
                                 ("rem", "REM", "#a78bfa"), ("awake", "ébren", "#fca5a5")):
            fig.add_trace(go.Bar(x=[d_.strftime("%Y-%m-%d") for d_ in wk.index],
                                 y=_r(wk[col].values), name=name, marker_color=color))
        fig.update_layout(barmode="stack")
        fig.update_yaxes(title_text="perc / éjszaka (heti átlag)")
        sec.charts.append(Chart("sleep_stages", t("report.c.sleep_stages"), fig))
    bed, wake = d.series("bedtime_min"), d.series("waketime_min")
    if len(bed) >= need:
        fig = _fig(260)
        fig.add_trace(go.Scatter(x=_dates(bed), y=_r((bed / 60 + 12).values), mode="markers",
                                 name="lefekvés", marker={"color": "#1e3a8a", "size": 5}))
        fig.add_trace(go.Scatter(x=_dates(wake), y=_r((wake / 60 + 24).values), mode="markers",
                                 name="ébredés", marker={"color": "#f59e0b", "size": 5}))
        fig.update_yaxes(title_text="óra", tickvals=list(range(18, 35, 2)),
                         ticktext=[f"{h % 24:02d}:00" for h in range(18, 35, 2)])
        sec.charts.append(Chart("sleep_timing", t("report.c.sleep_timing"), fig))
    sec.charts.append(_calendar("sleep_calendar", t("report.c.sleep_calendar"), s / 60, "óra"))
    return sec


def _calendar(cid: str, title: str, s: pd.Series, unit: str) -> Chart:
    df = pd.DataFrame({"v": s})
    iso = df.index.isocalendar()
    df["wk"] = [f"{y}/{w:02d}" for y, w in zip(iso.year, iso.week, strict=True)]
    df["wd"] = df.index.day_of_week
    piv = df.pivot_table(index="wd", columns="wk", values="v", aggfunc="mean").reindex(range(7))
    fig = _fig(230)
    labels = [f"{int(c.split('/')[1])}. hét" for c in piv.columns]
    fig.add_trace(go.Heatmap(z=[_r(row) for row in piv.values], x=labels,
                             y=["H", "K", "Sze", "Cs", "P", "Szo", "V"], colorscale="Blues",
                             colorbar={"title": unit}))
    fig.update_xaxes(tickformat=None, type="category", title_text="ISO-hét")
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(showlegend=False)
    return Chart(cid, title, fig)


# ---------------------------------------------------------------- heart / HRV

def heart_section(d: ReportData, cat: Catalog) -> Section:
    title = t("report.section.heart")
    rhr = d.series("resting_hr")
    need = int(cat.params["min_valid_days"])
    hrvs = d.hrv_metrics()
    if len(rhr) < need and not any(len(d.series(m)) >= need for m in hrvs):
        return _not_enough("heart", title, len(rhr), need)
    sec = Section("heart", title)
    if len(rhr) >= need:
        st = d.metric_stats("resting_hr")
        sec.facts += [
            (t("report.f.rhr_mean"), f"{fmt_num(rhr.mean(), 1)} bpm"),
            (t("report.f.rhr_range"), f"{fmt_num(rhr.min(), 0)} – {fmt_num(rhr.max(), 0)} bpm"),
        ]
        if len(st) and st["bl_mean"].notna().any():
            sec.facts.append((t("report.f.rhr_baseline"),
                              f"{fmt_num(st['bl_mean'].dropna().iloc[-1], 1)} bpm"))
            sec.facts.append((t("report.f.outside_band"),
                              f"{int(st['outside_band'].fillna(False).sum())} nap"))
        prev = d.series("resting_hr", prev=True)
        if len(prev) >= need:
            sec.facts.append((t("report.f.vs_prev"),
                              f"{fmt_signed(rhr.mean() - prev.mean(), 1)} bpm"))
        sec.charts.append(_trend_chart("rhr_trend", t("report.c.rhr"), rhr, unit="bpm", stats=st))
    for m in hrvs:
        s = d.series(m)
        if len(s) < need:
            continue
        method = m.replace("hrv_", "").upper()
        st = d.metric_stats(m)
        sec.facts.append((t("report.f.hrv_mean", method=method), f"{fmt_num(s.mean(), 0)} ms"))
        ln = d.metric_stats(f"ln_{m}")
        if len(ln) and ln["trend_7v28"].notna().any():
            last = ln["trend_7v28"].dropna().iloc[-1]
            sec.facts.append((t("report.f.hrv_trend", method=method),
                              f"{fmt_signed(100 * (np.exp(last) - 1), 0)} %"))
        sec.charts.append(_trend_chart("hrv_trend" if m == hrvs[0] else f"hrv_trend_{method}",
                                       t("report.c.hrv", method=method), s, unit="ms", stats=st))
    if len(hrvs) > 1:
        sec.paragraphs.append(t("report.p.hrv_methods"))
    if not hrvs:
        sec.paragraphs.append(t("report.p.no_hrv"))
    rec = d.activities["hr_recovery_1min"].dropna() if len(d.activities) else pd.Series(dtype=float)
    if len(rec):
        sec.facts.append((t("report.f.hr_recovery"),
                          f"{fmt_num(rec.mean(), 0)} bpm (n = {len(rec)})"))
    if len(d.activities) and d.activities["hr_max"].notna().any():
        sec.facts.append((t("report.f.workout_hr"),
                          f"{fmt_num(d.activities['hr_avg'].mean(), 0)} / "
                          f"{fmt_num(d.activities['hr_max'].max(), 0)} bpm"))
    return sec


# ---------------------------------------------------------------- stress

def stress_section(d: ReportData, cat: Catalog) -> Section:
    title = t("report.section.stress")
    s = d.series("stress_avg")
    need = int(cat.params["min_valid_days"])
    if len(s) < need:
        sec = _not_enough("stress", title, len(s), need)
        if "Garmin" not in d.vendors:
            sec.reason = t("report.p.no_stress_device")
        return sec
    sec = Section("stress", title)
    high = d.series("stress_high_min")
    meas = d.series("stress_measured_min")
    sec.facts = [(t("report.f.stress_mean"), fmt_num(s.mean(), 0)),
                 (t("report.f.stress_range"), f"{fmt_num(s.min(), 0)} – {fmt_num(s.max(), 0)}")]
    if len(high) and len(meas):
        j = pd.concat([high, meas], axis=1, keys=["h", "m"], sort=True).dropna()
        if len(j) and j["m"].sum():
            sec.facts.append((t("report.f.stress_high_share"),
                              f"{fmt_num(100 * j['h'].sum() / j['m'].sum(), 0)} %"))
    bb_s, bb_e = d.series("body_battery_sleep_start"), d.series("body_battery_sleep_end")
    if len(bb_s) and len(bb_e):
        ch = (bb_e - bb_s).dropna()
        if len(ch):
            sec.facts.append((t("report.f.bb_night"), f"{fmt_signed(ch.mean(), 0)} pont"))
    bbmax = d.series("body_battery_max")
    if len(bbmax):
        sec.facts.append((t("report.f.bb_max"), fmt_num(bbmax.mean(), 0)))
    fig = _fig()
    fig.add_trace(go.Bar(x=_dates(s), y=_r(s.values), name="napi átlagos stressz",
                         marker_color=COLORS["soft"]))
    if len(bbmax):
        fig.add_trace(go.Scatter(x=_dates(bbmax), y=_r(bbmax.values), mode="lines",
                                 name="Body Battery maximum", line={"color": COLORS["goal"]}))
    fig.update_yaxes(title_text="pont (0–100)")
    sec.charts.append(Chart("stress_daily", t("report.c.stress"), fig))
    sec.paragraphs.append(t("report.p.stress_note"))
    return sec


# ---------------------------------------------------------------- activity

ZONE_NAMES = {0: "1. zóna alatt", 1: "1. zóna", 2: "2. zóna", 3: "3. zóna", 4: "4. zóna",
              5: "5. zóna", 6: "5. zóna felett"}


def activity_section(d: ReportData, cat: Catalog) -> Section:
    title = t("report.section.activity")
    steps = d.series("steps")
    need = int(cat.params["min_valid_days"])
    w = d.weekly[d.weekly["days_with_data"] >= 4] if len(d.weekly) else d.weekly
    days_with = max(len(steps), int(d.weekly["days_with_data"].sum()) if len(d.weekly) else 0)
    if days_with < need:
        return _not_enough("activity", title, days_with, need)
    sec = Section("activity", title)
    lo, hi = cat.params["who_moderate_min_week"], cat.params["who_moderate_target_week"]
    if len(w):
        sec.facts += [
            (t("report.f.who_mean"), f"{fmt_num(w['who_equiv_min'].mean(), 0)} perc / hét"),
            (t("report.f.who_weeks_met", lo=fmt_num(lo)),
             f"{int((w['who_equiv_min'] >= lo).sum())} / {len(w)} hét"),
            (t("report.f.strength_days"), f"{fmt_num(w['strength_days'].mean(), 1)} nap / hét"),
            (t("report.f.rest_days"), f"{fmt_num(w['rest_days'].mean(), 1)} nap / hét"),
        ]
        method = set(w["who_method"].dropna())
        if "exercise_as_moderate" in method:
            sec.paragraphs.append(t("report.p.who_apple"))
        fig = _fig()
        fig.add_trace(go.Bar(x=[x.strftime("%Y-%m-%d") for x in pd.to_datetime(w["week_start"])],
                             y=_r(w["who_equiv_min"].values), name="WHO-egyenértékes perc",
                             marker_color=COLORS["main"]))
        fig.add_hline(y=lo, line={"color": COLORS["goal"], "dash": "dash"},
                      annotation_text=f"{fmt_num(lo)} perc")
        fig.add_hline(y=hi, line={"color": COLORS["grey"], "dash": "dot"},
                      annotation_text=f"{fmt_num(hi)} perc")
        fig.update_yaxes(title_text="perc / hét")
        sec.charts.append(Chart("who_weekly", t("report.c.who"), fig))
    if len(steps):
        sec.facts.append((t("report.f.steps_mean"), f"{fmt_num(steps.mean(), 0)} lépés / nap"))
        prev = d.series("steps", prev=True)
        if len(prev) >= need:
            sec.facts.append((t("report.f.vs_prev"),
                              f"{fmt_signed(100 * (steps.mean() / prev.mean() - 1), 0)} %"))
        sec.charts.append(_trend_chart("steps", t("report.c.steps"), steps, unit="lépés",
                                       stats=None))
    acts = d.activities
    if len(acts):
        by = acts.groupby("unified_name_hu").agg(n=("activity_id", "count"),
                                                  minutes=("duration_min", "sum"))
        by = by.sort_values(["n", "minutes"], ascending=False)
        sec.table_head = [t("report.t.type"), t("report.t.count"), t("report.t.minutes")]
        sec.table = [[str(k), str(int(r.n)), fmt_num(r.minutes, 0)] for k, r in by.iterrows()]
        weeks = max(d.period.days / 7, 1)
        sec.facts.append((t("report.f.workouts_per_week"), fmt_num(len(acts) / weeks, 1)))
        cats = acts.groupby("type_category")["duration_min"].sum()
        total = cats.sum()
        if total:
            sec.facts.append((t("report.f.aerobic_share"),
                              f"{fmt_num(100 * cats.get('aerobic', 0) / total, 0)} % / "
                              f"{fmt_num(100 * cats.get('anaerobic', 0) / total, 0)} %"))
    if len(d.zones) and d.zones["seconds"].sum() > 0:
        z = d.zones.set_index("zone")["seconds"]
        share = z / z.sum() * 100
        low = share[share.index <= 2].sum()
        mid = share.get(3, 0)
        high = share[share.index >= 4].sum()
        profile = ("polarizált" if low >= 70 and high >= 10 and mid < 20 else
                   "küszöb-jellegű" if mid >= 35 else
                   "alacsony intenzitású" if low >= 80 else "vegyes")
        sec.facts.append((t("report.f.zones"), f"{fmt_num(low, 0)} / {fmt_num(mid, 0)} / "
                                               f"{fmt_num(high, 0)} % – {profile}"))
        fig = _fig(240)
        fig.add_trace(go.Bar(x=[ZONE_NAMES.get(int(i), str(i)) for i in share.index],
                             y=_r(share.values), marker_color=COLORS["main"]))
        fig.update_xaxes(tickformat=None)
        fig.update_yaxes(title_text="% az edzésidőből")
        fig.update_layout(showlegend=False)
        sec.charts.append(Chart("zones", t("report.c.zones"), fig))
    vo2 = d.series("vo2max")
    if len(vo2) >= 2:
        sec.facts.append((t("report.f.vo2max"), f"{fmt_num(vo2.iloc[0], 0)} → "
                                                f"{fmt_num(vo2.iloc[-1], 0)} mL/kg/perc"))
    ld = d.load
    if len(ld) and ld["acwr_valid"].any():
        fig = _fig(260)
        v = ld[ld["acwr_valid"]]
        fig.add_trace(go.Scatter(x=[x.strftime("%Y-%m-%d") for x in pd.to_datetime(v["date"])],
                                 y=_r(v["acwr"].values), mode="lines", name="akut:krónikus arány",
                                 line={"color": COLORS["main"]}))
        fig.add_hline(y=cat.params["acwr_high"], line={"color": COLORS["warn"], "dash": "dash"})
        sec.charts.append(Chart("acwr", t("report.c.acwr"), fig))
        sec.paragraphs.append(t("report.p.acwr_note"))
    return sec


# ---------------------------------------------------------------- respiration / body

def respiration_section(d: ReportData, cat: Catalog) -> Section:
    title = t("report.section.respiration")
    spo2 = d.series("spo2_avg")
    rr = d.series("respiratory_rate_avg")
    need = int(cat.params["min_valid_days"])
    if len(spo2) < need and len(rr) < need:
        return _not_enough("respiration", title, max(len(spo2), len(rr)), need)
    sec = Section("respiration", title)
    fig = _fig(260)
    if len(spo2):
        sec.facts.append((t("report.f.spo2_mean"), f"{fmt_num(spo2.mean(), 1)} %"))
        fig.add_trace(go.Scatter(x=_dates(spo2), y=_r(spo2.values), mode="lines+markers",
                                 name="SpO₂ átlag", marker={"size": 4}))
    low = d.series("spo2_sleep_min")
    if len(low):
        sec.facts.append((t("report.f.spo2_min"), f"{fmt_num(low.min(), 0)} %"))
        fig.add_trace(go.Scatter(x=_dates(low), y=_r(low.values), mode="markers",
                                 name="alvás alatti minimum", marker={"size": 4}))
    if len(rr):
        sec.facts.append((t("report.f.resp_mean"), f"{fmt_num(rr.mean(), 1)} / perc"))
    fig.update_yaxes(title_text="%")
    if fig.data:
        sec.charts.append(Chart("spo2", t("report.c.spo2"), fig))
    sec.paragraphs.append(t("report.p.spo2_note"))
    return sec


def body_section(d: ReportData, cat: Catalog) -> Section:
    title = t("report.section.body")
    w = d.series("body_mass")
    if len(w) < 2:
        return Section("body", title, available=False, reason=t("report.no_body"))
    sec = Section("body", title)
    sec.facts = [(t("report.f.weight"), f"{fmt_num(w.iloc[0], 1)} → {fmt_num(w.iloc[-1], 1)} kg "
                                        f"({len(w)} mérés)")]
    fat = d.series("body_fat_pct")
    if len(fat) >= 2:
        sec.facts.append((t("report.f.fat"),
                          f"{fmt_num(fat.iloc[0], 1)} → {fmt_num(fat.iloc[-1], 1)} %"))
    fig = _fig(240)
    fig.add_trace(go.Scatter(x=_dates(w), y=_r(w.values), mode="lines+markers", name="testsúly"))
    fig.update_yaxes(title_text="kg")
    sec.charts.append(Chart("body", t("report.c.body"), fig))
    return sec


# ---------------------------------------------------------------- correlations / anomalies

def correlation_section(d: ReportData, cors: list[Correlation],
                        series: dict[str, pd.Series]) -> Section:
    sec = Section("correlations", t("report.section.correlations"))
    sec.table_head = [t("report.t.pair"), "n", "Spearman ρ", "p", t("report.t.strength")]
    for c in cors:
        sec.table.append([c.label, str(c.n), fmt_num(c.rho, 2) if c.rho is not None else "–",
                          (fmt_num(c.p, 3) if c.p is not None and c.p >= 0.001 else "< 0,001")
                          if c.p is not None else "–", c.strength])
    usable = [c for c in cors if c.rho is not None]
    if not usable:
        sec.available = False
        sec.reason = t("report.no_correlations")
        return sec
    for c in sorted(usable, key=lambda c: -abs(c.rho or 0))[:2]:
        x, y = series.get(c.x), series.get(c.y)
        if x is None or y is None:
            continue
        ys = y.copy()
        ys.index = ys.index - pd.Timedelta(days=c.lag)
        j = pd.concat([x, ys], axis=1, keys=["x", "y"], sort=True).dropna()
        fig = _fig(260)
        fig.add_trace(go.Scatter(x=_r(j["x"].values), y=_r(j["y"].values), mode="markers",
                                 marker={"size": 5, "color": COLORS["main"]}, name="napok"))
        k, b = np.polyfit(j["x"], j["y"], 1)
        xs = np.linspace(j["x"].min(), j["x"].max(), 20)
        fig.add_trace(go.Scatter(x=_r(xs), y=_r(k * xs + b), mode="lines",
                                 name="illesztett egyenes",
                                 line={"dash": "dash", "color": COLORS["grey"]}))
        fig.update_xaxes(tickformat=None, title_text=metric_name(c.x))
        fig.update_yaxes(title_text=metric_name(c.y))
        sec.charts.append(Chart(f"corr_{c.corr_id}_{c.y}", c.label, fig))
    sec.paragraphs.append(t("report.p.corr_note"))
    return sec


KEY_METRICS = ["resting_hr", "hrv_sdnn", "hrv_rmssd", "sleep_min", "steps", "stress_avg"]


def anomaly_section(d: ReportData, cat: Catalog) -> Section:
    sec = Section("anomalies", t("report.section.anomalies"))
    thr = cat.params["outlier_robust_z"]
    st = d.stats[d.stats["metric_key"].isin(KEY_METRICS)] if len(d.stats) else d.stats
    rows: list[list[str]] = []
    if len(st):
        out = st[st["robust_z"].abs() >= thr].sort_values("robust_z", key=abs, ascending=False)
        for r in out.head(10).itertuples():
            rows.append([fmt_date(r.date), metric_name(r.metric_key), fmt_num(r.value, 1),
                         fmt_num(r.bl_median, 1), fmt_signed(r.robust_z, 1)])
    sec.table_head = [t("report.t.day"), t("report.t.metric"), t("report.t.value"),
                      t("report.t.baseline"), "robusztus z"]
    sec.table = rows
    if not rows:
        sec.paragraphs.append(t("report.p.no_outliers", z=fmt_num(thr, 1)))
    if len(d.gaps):
        g = d.gaps.head(5)
        sec.paragraphs.append(t("report.p.gaps") + " " + "; ".join(
            f"{metric_name(r.metric_key)}: {fmt_date(r.gap_start)} – {fmt_date(r.gap_end)} "
            f"({r.gap_days} nap)"
            for r in g.itertuples()))
    return sec
