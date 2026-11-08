"""Execute an analysis: resolve the period, run the SQL in DuckDB, apply transformations
per series and down-sample intraday lines (LTTB) – SPEC 8.2/8.5."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from vitalmart.semantic.model import SemanticModel, load_model
from vitalmart.semantic.spec import PERIOD_DAYS, AnalysisSpec, validate
from vitalmart.semantic.sqlgen import Query, anchor_sql, build_query, build_timeline_query

MAX_POINTS = 20_000


@dataclass
class Result:
    spec: AnalysisSpec
    df: pd.DataFrame                      # columns: period?, d0.., m0..
    query: Query
    date_from: date | None
    date_to: date | None
    labels: dict[str, str] = field(default_factory=dict)   # column -> display label
    units: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    downsampled_from: int | None = None
    orders: dict[str, list[Any]] = field(default_factory=dict)   # d_i -> category order
    n_rows: int = 0

    @property
    def measure_cols(self) -> list[str]:
        return [f"m{i}" for i in range(len(self.spec.measures))]

    @property
    def dim_cols(self) -> list[str]:
        return [f"d{i}" for i in range(len(self.spec.dimensions))]


def resolve_period(con: duckdb.DuckDBPyConnection, spec: AnalysisSpec,
                   model: SemanticModel) -> tuple[date | None, date | None]:
    if spec.period == "custom":
        return spec.date_from, spec.date_to
    if spec.period == "all":
        return None, None
    sql, params = anchor_sql(spec, model)
    end = con.execute(sql, params).fetchall()[0][0]
    if end is None:
        return None, None
    if hasattr(end, "date") and not isinstance(end, date):
        end = end.date()
    return end - timedelta(days=PERIOD_DAYS[spec.period] - 1), end


def lttb(x: np.ndarray, y: np.ndarray, n_out: int) -> np.ndarray:
    """Largest-Triangle-Three-Buckets down-sampling; returns the kept indices (sorted).
    Keeps first and last point; NaNs are not expected (drop them before)."""
    n = len(x)
    if n_out >= n or n_out < 3:
        return np.arange(n)
    xf = x.astype("float64")
    keep = np.empty(n_out, dtype=np.int64)
    keep[0], keep[-1] = 0, n - 1
    edges = np.linspace(1, n - 1, n_out - 1).astype(np.int64)
    a = 0
    for i in range(n_out - 2):
        s, e = edges[i], edges[i + 1]
        ns, ne = edges[i + 1], (edges[i + 2] if i + 2 < len(edges) else n)
        avg_x = xf[ns:ne].mean() if ne > ns else xf[-1]
        avg_y = y[ns:ne].mean() if ne > ns else y[-1]
        area = np.abs((xf[a] - avg_x) * (y[s:e] - y[a]) - (xf[a] - xf[s:e]) * (avg_y - y[a]))
        a = s + int(np.argmax(area))
        keep[i + 1] = a
    return keep


def _apply_transform(s: pd.Series, how: str, window: int) -> pd.Series:
    if how == "moving_avg":
        return s.rolling(window, min_periods=1).mean()
    if how == "cumsum":
        return s.cumsum()
    if how == "diff":
        return s.diff()
    if how == "pct_change":
        return s.pct_change(fill_method=None) * 100.0
    if how == "index100":
        first = s.dropna()
        return s / first.iloc[0] * 100.0 if len(first) and first.iloc[0] else s * np.nan
    if how == "zscore":
        sd = s.std(ddof=1)
        return (s - s.mean()) / sd if sd and sd == sd else s * np.nan
    if how == "minmax":
        lo, hi = s.min(), s.max()
        return (s - lo) / (hi - lo) if hi != lo else s * 0.0
    return s


def transform(df: pd.DataFrame, spec: AnalysisSpec, measure_cols: list[str],
              dim_cols: list[str]) -> pd.DataFrame:
    """Apply the transformation per series (= combination of dimensions), in time order."""
    if spec.transform == "none" or df.empty:
        return df
    out = df.copy()
    order = ["period"] if "period" in out.columns else []
    out = out.sort_values([*dim_cols, *order], kind="stable")
    groups = out.groupby(dim_cols, dropna=False, sort=False) if dim_cols else [(None, out)]
    for _, g in groups:
        for c in measure_cols:
            out.loc[g.index, c] = _apply_transform(g[c].astype("float64"), spec.transform,
                                                   spec.transform_window)
    return out.sort_values([*order, *dim_cols], kind="stable").reset_index(drop=True)


def _downsample(df: pd.DataFrame, measure_cols: list[str], dim_cols: list[str],
                limit: int) -> tuple[pd.DataFrame, int | None]:
    if len(df) <= limit or "period" not in df.columns:
        return df, None
    parts = []
    groups = df.groupby(dim_cols, dropna=False, sort=False) if dim_cols else [(None, df)]
    n_groups = max(1, len(groups) if dim_cols else 1)
    per = max(3, limit // n_groups)
    for _, g in groups:
        g = g.dropna(subset=[measure_cols[0]])
        x = pd.to_datetime(g["period"]).astype("int64").to_numpy()
        idx = lttb(x, g[measure_cols[0]].to_numpy(dtype="float64"), per)
        parts.append(g.iloc[idx])
    return pd.concat(parts).sort_values("period").reset_index(drop=True), len(df)


def run(con: duckdb.DuckDBPyConnection, spec: AnalysisSpec,
        model: SemanticModel | None = None) -> Result:
    model = model or load_model()
    validate(spec, model)
    topic = model.topics[spec.topic]
    d0, d1 = resolve_period(con, spec, model)
    q = (build_timeline_query(spec, model, d0, d1) if spec.chart == "timeline"
         else build_query(spec, model, d0, d1))
    df = con.execute(q.sql, q.params).df()
    truncated = len(df) > q.limit
    if truncated:
        df = df.iloc[: q.limit]
    res = Result(spec, df, q, d0, d1, truncated=truncated, n_rows=len(df))
    res.labels["period"] = "Időszak"
    for i, dk in enumerate(spec.dimensions):
        res.labels[f"d{i}"] = model.dimensions[dk].label_hu
    for i, ms in enumerate(spec.measures):
        m = topic.measures[ms.key]
        agg = spec.agg_of(ms, model)
        suffix = "" if agg in ("none", m.agg) else f" ({model.aggregations[agg]['label_hu']})"
        res.labels[f"m{i}"] = m.label_hu + suffix
        res.units[f"m{i}"] = m.unit
    if spec.chart == "timeline":
        return res
    for i in range(len(spec.dimensions)):
        if f"o{i}" in res.df.columns:
            od = res.df[[f"d{i}", f"o{i}"]].drop_duplicates(f"d{i}").sort_values(f"o{i}")
            res.orders[f"d{i}"] = od[f"d{i}"].tolist()
    df = res.df.drop(columns=[c for c in res.df.columns if c.startswith("o")], errors="ignore")
    df = transform(df, spec, res.measure_cols, res.dim_cols)
    if spec.chart in ("line", "area") and spec.time_grain in ("raw", "hour"):
        df, res.downsampled_from = _downsample(df, res.measure_cols, res.dim_cols, MAX_POINTS)
    res.df = df
    return res


def display_frame(res: Result) -> pd.DataFrame:
    """Result with readable column names (unit in the header) for tables / exports."""
    cols: dict[str, str] = {}
    for c in res.df.columns:
        label = res.labels.get(c, c)
        unit = res.units.get(c)
        cols[c] = f"{label} [{unit}]" if unit else label
    return res.df.rename(columns=cols)


def spec_from_json(data: dict[str, Any]) -> AnalysisSpec:
    return AnalysisSpec.model_validate(data)
