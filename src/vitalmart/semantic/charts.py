"""Plotly figures for Explorer results (SPEC 8.2/7). Hungarian number format."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from vitalmart.semantic.engine import Result

HU_SEPARATORS = ", "          # decimal comma, (narrow) space thousands
WEEKDAYS_HU = ["H", "K", "Sze", "Cs", "P", "Szo", "V"]


def _label(res: Result, col: str) -> str:
    unit = res.units.get(col)
    return f"{res.labels.get(col, col)} [{unit}]" if unit else res.labels.get(col, col)


def _finish(fig: go.Figure, title: str | None = None) -> go.Figure:
    fig.update_layout(separators=HU_SEPARATORS, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      legend_title_text="", title=title)
    fig.update_xaxes(tickformat=None)
    return fig


def _date_axis(fig: go.Figure, df: pd.DataFrame, x: str | None) -> go.Figure:
    """Hungarian date ticks (ÉÉÉÉ.HH.NN.) – plotly's own month names are English."""
    if x == "period" and x in df.columns and (
            pd.api.types.is_datetime64_any_dtype(df[x])
            or (len(df) and hasattr(df[x].iloc[0], "year"))):
        fig.update_xaxes(tickformat="%Y.%m.%d.", hoverformat="%Y.%m.%d. %H:%M")
    return fig


def _series_frame(res: Result) -> tuple[pd.DataFrame, str | None, str | None, str | None]:
    """Returns (df, x column, series column, facet column)."""
    df = res.df.copy()
    spec = res.spec
    dims = res.dim_cols
    facet = f"d{spec.dimensions.index(spec.facet)}" if spec.facet else None
    x = "period" if "period" in df.columns else (dims[0] if dims else None)
    rest = [d for d in dims if d != x and d != facet]
    series = None
    if rest:
        series = "_series"
        df[series] = df[rest].astype(str).agg(" · ".join, axis=1)
    return df, x, series, facet


def _melt(res: Result, df: pd.DataFrame, x: str | None, series: str | None,
          facet: str | None, measures: list[str]) -> pd.DataFrame:
    ids = [c for c in (x, series, facet) if c]
    long = df.melt(id_vars=ids, value_vars=measures, var_name="_m", value_name="_v")
    long["_m"] = long["_m"].map(lambda c: _label(res, c))
    return long


def build_figure(res: Result) -> go.Figure:
    spec = res.spec
    chart = spec.chart
    if res.df.empty:
        fig = go.Figure()
        fig.add_annotation(text="Nincs adat a kiválasztott beállításokkal.", showarrow=False)
        return _finish(fig)
    ms = res.measure_cols
    if chart == "timeline":
        df = res.df.copy()
        fig = px.timeline(df, x_start="start", x_end="finish", y="label", color="person")
        fig.update_yaxes(title="")
        fig.update_xaxes(tickformat="%Y.%m.%d.", hoverformat="%Y.%m.%d. %H:%M")
        return _finish(fig)
    df, x, series, facet = _series_frame(res)
    facet_kw: dict = {"facet_col": facet, "facet_col_wrap": 3} if facet else {}
    facet_kw["category_orders"] = dict(res.orders)

    if chart in ("line", "area", "bar", "bar_stacked"):
        if x is None:
            df["_all"] = "összes"
            x = "_all"
        sec = (f"m{[m.key for m in spec.measures].index(spec.secondary_measure)}"
               if spec.secondary_measure else None)
        if sec and not series and not facet and len(ms) > 1:
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            for c in ms:
                trace_cls = go.Bar if chart.startswith("bar") else go.Scatter
                kw = {} if chart.startswith("bar") else {"mode": "lines"}
                if chart == "area":
                    kw["fill"] = "tozeroy"
                fig.add_trace(trace_cls(x=df[x], y=df[c], name=_label(res, c), **kw),
                              secondary_y=(c == sec))
            fig.update_yaxes(title_text=" / ".join(_label(res, c) for c in ms if c != sec),
                             secondary_y=False)
            fig.update_yaxes(title_text=_label(res, sec), secondary_y=True)
            return _finish(_date_axis(fig, df, x))
        long = _melt(res, df, x, series, facet, ms)
        color = series or "_m"
        extra = {}
        if series and len(ms) > 1:
            extra = {"facet_row": "_m"} if not facet else {"line_dash": "_m"}
        fn = {"line": px.line, "area": px.area, "bar": px.bar, "bar_stacked": px.bar}[chart]
        kw = {"barmode": "stack" if chart == "bar_stacked" else "group"} \
            if chart.startswith("bar") else {}
        if "line_dash" in extra and chart != "line":
            extra = {}
        fig = fn(long, x=x, y="_v", color=color, labels={"_v": "", x: res.labels.get(x, "")},
                 **facet_kw, **extra, **kw)
        if chart in ("line", "area") and spec.time_grain in ("raw", "hour"):
            fig.update_traces(line={"width": 1})
        return _finish(_date_axis(fig, df, x))

    if chart in ("scatter", "bubble"):
        size = ms[2] if chart == "bubble" else None
        d = df.dropna(subset=ms[:2])
        if size:
            d = d[d[size] > 0]
        fig = px.scatter(d, x=ms[0], y=ms[1], color=series, size=size, **facet_kw,
                         labels={c: _label(res, c) for c in ms},
                         hover_data={"period": True} if "period" in d.columns else None)
        if len(d) >= 3 and not series and not facet:
            k, b = np.polyfit(d[ms[0]].astype(float), d[ms[1]].astype(float), 1)
            xs = np.linspace(d[ms[0]].min(), d[ms[0]].max(), 50)
            fig.add_trace(go.Scatter(x=xs, y=k * xs + b, mode="lines", name="lineáris illesztés",
                                     line={"dash": "dash"}))
        return _finish(fig)

    if chart == "histogram":
        long = _melt(res, df, None, series, facet, ms)
        fig = px.histogram(long, x="_v", color=series or "_m", barmode="overlay", opacity=0.7,
                           **facet_kw, labels={"_v": ""})
        return _finish(fig)

    if chart in ("box", "violin"):
        cat = series or (x if x and x != "period" else None)
        long = _melt(res, df, None if x == "period" else x, series, facet, ms)
        fn = px.box if chart == "box" else px.violin
        fig = fn(long, x=cat if cat in long.columns else "_m", y="_v",
                 color="_m" if len(ms) > 1 else None, **facet_kw,
                 labels={"_v": "", "_m": "", **{c: res.labels.get(c, c) for c in res.dim_cols}})
        return _finish(fig)

    if chart == "heatmap":
        axes = ([x] if x == "period" else []) + [d for d in res.dim_cols]
        a, b = axes[0], axes[1]
        piv = df.pivot_table(index=b, columns=a, values=ms[0], aggfunc="mean", sort=True)
        if b in res.orders:
            piv = piv.reindex([v for v in res.orders[b] if v in piv.index])
        if a in res.orders:
            piv = piv[[v for v in res.orders[a] if v in piv.columns]]
        fig = px.imshow(piv, aspect="auto", color_continuous_scale="Viridis",
                        labels={"color": _label(res, ms[0]), "x": res.labels.get(a, ""),
                                "y": res.labels.get(b, "")})
        return _finish(fig)

    if chart == "calendar":
        d = df.copy()
        d["period"] = pd.to_datetime(d["period"])
        d["year"] = d["period"].dt.isocalendar().year.astype(int)
        d["week"] = d["period"].dt.isocalendar().week.astype(int)
        d["wd"] = d["period"].dt.day_of_week
        years = sorted(d["year"].unique())
        fig = make_subplots(rows=len(years), cols=1, subplot_titles=[str(y) for y in years],
                            vertical_spacing=0.08)
        for i, y in enumerate(years, start=1):
            dy = d[d["year"] == y]
            piv = dy.pivot_table(index="wd", columns="week", values=ms[0], aggfunc="mean")
            piv = piv.reindex(range(7))
            fig.add_trace(go.Heatmap(z=piv.values, x=piv.columns, y=WEEKDAYS_HU,
                                     coloraxis="coloraxis",
                                     hovertemplate="hét %{x}, %{y}: %{z:.1f}<extra></extra>"),
                          row=i, col=1)
        fig.update_layout(coloraxis={"colorscale": "Viridis",
                                     "colorbar": {"title": _label(res, ms[0])}},
                          height=max(260, 200 * len(years)))
        fig.update_yaxes(autorange="reversed")
        return _finish(fig)

    if chart == "corr":
        c = df[ms].astype(float).corr(method=spec.corr_method)
        c.index = c.columns = [_label(res, m) for m in ms]
        n = int(df[ms].dropna().shape[0])
        fig = px.imshow(c, text_auto=".2f", zmin=-1, zmax=1, color_continuous_scale="RdBu",
                        title=None)
        name = "Spearman" if spec.corr_method == "spearman" else "Pearson"
        return _finish(fig, f"{name}-korreláció (n = {n}) – a korreláció nem ok-okozat")

    if chart == "radar":
        cat = series or (x if x != "period" else None) or res.dim_cols[0]
        agg = df.groupby(cat, sort=False)[ms].mean()
        if cat in res.orders:
            agg = agg.reindex([v for v in res.orders[cat] if v in agg.index])
        norm = (agg - agg.min()) / (agg.max() - agg.min()).replace(0, np.nan)
        fig = go.Figure()
        theta = [_label(res, m) for m in ms]
        for name, row in norm.fillna(0.5).iterrows():
            fig.add_trace(go.Scatterpolar(r=[*row.values, row.values[0]],
                                          theta=[*theta, theta[0]], fill="toself",
                                          name=str(name)))
        fig.update_layout(polar={"radialaxis": {"range": [0, 1], "showticklabels": False}})
        return _finish(fig, "Profilok (mérőszámonként 0–1 közé normálva)")

    return _finish(go.Figure())
