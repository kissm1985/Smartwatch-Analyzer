"""Lag / shift analysis (SPEC 8.2/10): measure X on day t vs measure Y on day t + lag.

Pearson (with exact Fisher-z CI from scipy) and Spearman (Fisher-z CI with the
Fieller–Hartley–Pearson variance 1.06 / (n − 3)); sample size and p-value are always shown,
and correlation is never presented as causation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from scipy import stats

from vitalmart.i18n import t
from vitalmart.report.rules import strength_label
from vitalmart.semantic.charts import HU_SEPARATORS
from vitalmart.semantic.engine import run
from vitalmart.semantic.model import SemanticModel
from vitalmart.semantic.spec import AnalysisSpec

MIN_N = 10
Z95 = 1.959963984540054


@dataclass(frozen=True)
class SeriesRef:
    """A daily series from the semantic layer: topic + measure (+ aggregation)."""

    topic: str
    measure: str
    agg: str | None = None

    @property
    def key(self) -> str:
        return f"{self.topic}.{self.measure}"


@dataclass
class LagStat:
    lag: int
    n: int
    pearson: float | None
    pearson_p: float | None
    pearson_lo: float | None
    pearson_hi: float | None
    spearman: float | None
    spearman_p: float | None
    spearman_lo: float | None
    spearman_hi: float | None

    def value(self, method: str) -> tuple[float | None, float | None, float | None, float | None]:
        if method == "pearson":
            return self.pearson, self.pearson_p, self.pearson_lo, self.pearson_hi
        return self.spearman, self.spearman_p, self.spearman_lo, self.spearman_hi


def lag_topics(model: SemanticModel) -> list[str]:
    """Topics usable for a single daily series (no mandatory split/filter, has a date)."""
    return [k for k, tp in model.topics.items()
            if not tp.required_dimensions and not tp.required_filters and k != "weekly"]


def label(model: SemanticModel, ref: SeriesRef) -> str:
    m = model.topics[ref.topic].measures[ref.measure]
    return f"{m.label_hu} [{m.unit}]" if m.unit else m.label_hu


def daily_series(con: duckdb.DuckDBPyConnection, model: SemanticModel, ref: SeriesRef,
                 person: str, date_from: date | None = None, date_to: date | None = None,
                 quality_ok_only: bool = True) -> pd.Series:
    spec = AnalysisSpec.model_validate({
        "topic": ref.topic, "measures": [{"key": ref.measure, "agg": ref.agg}],
        "time_grain": "day", "persons": [person], "quality_ok_only": quality_ok_only,
        "period": "custom" if date_from and date_to else "all",
        "date_from": date_from, "date_to": date_to, "chart": "table", "show_events": False})
    res = run(con, spec, model)
    if res.df.empty:
        return pd.Series(dtype=float, name=ref.key)
    s = pd.Series(res.df["m0"].astype(float).values,
                  index=pd.to_datetime(res.df["period"]).dt.normalize(), name=ref.key)
    return s[~s.index.duplicated()].dropna()


def pairs(x: pd.Series, y: pd.Series, lag: int) -> pd.DataFrame:
    """Rows (date t, x[t], y[t + lag]) where both exist."""
    ys = y.copy()
    ys.index = ys.index - pd.Timedelta(days=int(lag))
    return pd.concat([x, ys], axis=1, keys=["x", "y"], sort=True).dropna()


def _fisher_ci(r: float, n: int, var_factor: float = 1.0) -> tuple[float, float]:
    if n <= 3 or abs(r) >= 1:
        return (r, r)
    z = math.atanh(r)
    se = math.sqrt(var_factor / (n - 3))
    return math.tanh(z - Z95 * se), math.tanh(z + Z95 * se)


def lag_stat(x: pd.Series, y: pd.Series, lag: int, min_n: int = MIN_N) -> LagStat:
    j = pairs(x, y, lag)
    n = len(j)
    if n < min_n or j["x"].nunique() < 3 or j["y"].nunique() < 3:
        return LagStat(lag, n, *([None] * 8))
    pr = stats.pearsonr(j["x"], j["y"])
    ci = pr.confidence_interval(0.95)
    sr = stats.spearmanr(j["x"], j["y"])
    rho = float(sr.statistic)
    s_lo, s_hi = _fisher_ci(rho, n, 1.06)
    return LagStat(lag, n, float(pr.statistic), float(pr.pvalue), float(ci.low),
                   float(ci.high), rho, float(sr.pvalue), s_lo, s_hi)


def lag_profile(x: pd.Series, y: pd.Series, lags: range, min_n: int = MIN_N) -> pd.DataFrame:
    rows = [lag_stat(x, y, k, min_n).__dict__ for k in lags]
    df = pd.DataFrame(rows, columns=list(LagStat.__dataclass_fields__))
    # all-None columns would be object dtype (abs/plot fail) – keep them numeric
    return df.astype({c: float for c in df.columns if c not in ("lag", "n")})


def best_lag(profile: pd.DataFrame, method: str) -> int | None:
    col = profile[method].abs()
    if col.notna().sum() == 0:
        return None
    return int(profile.loc[col.idxmax(), "lag"])


# ---------------------------------------------------------------- presentation

def profile_figure(profile: pd.DataFrame, method: Literal["pearson", "spearman"],
                   selected: int | None = None) -> go.Figure:
    p = profile.dropna(subset=[method])
    colors = ["#d97706" if k == selected else "#2563eb" for k in p["lag"]]
    fig = go.Figure(go.Bar(
        x=p["lag"], y=p[method], marker_color=colors, name="r",
        error_y={"type": "data", "symmetric": False,
                 "array": (p[f"{method}_hi"] - p[method]).round(4),
                 "arrayminus": (p[method] - p[f"{method}_lo"]).round(4)},
        customdata=np.stack([p["n"], p[f"{method}_p"]], axis=-1),
        hovertemplate=t("lag.hover") + "<extra></extra>"))
    fig.add_hline(y=0, line_color="#6b7280", line_width=1)
    fig.update_layout(separators=HU_SEPARATORS, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      xaxis_title=t("lag.axis_lag"),
                      yaxis_title=t(f"lag.axis_{method}"), yaxis_range=[-1, 1],
                      showlegend=False)
    return fig


def scatter_figure(x: pd.Series, y: pd.Series, lag: int, x_label: str, y_label: str
                   ) -> go.Figure:
    j = pairs(x, y, lag)
    fig = go.Figure(go.Scatter(
        x=j["x"].round(4), y=j["y"].round(4), mode="markers", name=t("lag.days"),
        marker={"color": "#2563eb", "size": 6, "opacity": 0.7},
        text=[d.strftime("%Y.%m.%d.") for d in j.index],
        hovertemplate="%{text}<br>x = %{x}<br>y = %{y}<extra></extra>"))
    if len(j) >= 3 and j["x"].nunique() > 1:
        k, b = np.polyfit(j["x"], j["y"], 1)
        xs = np.linspace(j["x"].min(), j["x"].max(), 30)
        fig.add_trace(go.Scatter(x=xs.round(4), y=(k * xs + b).round(4), mode="lines",
                                 name=t("lag.fit"), line={"dash": "dash",
                                                                  "color": "#6b7280"}))
    fig.update_layout(separators=HU_SEPARATORS, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      xaxis_title=x_label, yaxis_title=f"{y_label} ({when(lag)})")
    return fig


def when(lag: int) -> str:
    return (t("lag.same_day") if lag == 0 else t("lag.later", n=lag) if lag > 0
            else t("lag.earlier", n=-lag))


def describe(st_: LagStat, method: str, x_label: str, y_label: str) -> str:
    """Neutral, descriptive Hungarian sentence (never causal)."""
    from vitalmart.fmt import fmt_num

    r, p, lo, hi = st_.value(method)
    if r is None or p is None:
        return t("lag.not_enough", n=st_.n, min=MIN_N)
    strength = strength_label(r)
    key = "lag.none" if strength == "elhanyagolható" else "lag.sentence"
    return t(key, x=x_label, y=y_label, when=when(st_.lag), strength=strength,
             sign=t("lag.positive") if r > 0 else t("lag.negative"),
             sym="r" if method == "pearson" else "ρ", r=fmt_num(r, 2), lo=fmt_num(lo, 2),
             hi=fmt_num(hi, 2), n=st_.n,
             p="< 0,001" if p < 0.001 else f"= {fmt_num(p, 3)}") + " " + t("explorer.corr_note")
