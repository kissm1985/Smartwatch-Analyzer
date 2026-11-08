"""Plotly figure → static PNG via matplotlib (offline, no browser / kaleido needed).

Covers the trace types VitalMart produces (report + Explorer): Scatter (lines,
markers, fill bands), Bar (grouped / stacked / horizontal with base – timelines),
Histogram, Box, Violin, Heatmap, Scatterpolar, horizontal reference lines,
subplots (axis domains) and secondary y axes. Unknown trace types are skipped
with a note on the image instead of failing the export.
"""

from __future__ import annotations

import io
from collections import defaultdict
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from matplotlib import font_manager

DPI = 200
PALETTE = ["#2563eb", "#f59e0b", "#16a34a", "#dc2626", "#7c3aed", "#0891b2", "#db2777",
           "#65a30d", "#ea580c", "#475569"]

matplotlib.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
    "legend.fontsize": 7, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#e5e7eb", "grid.linewidth": 0.6,
    "axes.formatter.use_locale": False, "svg.hashsalt": "vitalmart",
})
_ = font_manager  # DejaVu Sans ships with matplotlib (Hungarian accents)


def _hu_number(x: float, _pos: int | None = None) -> str:
    s = f"{x:,.0f}" if abs(x - round(x)) < 1e-09 else f"{x:,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ").replace(".", ",")


def _is_dates(values: Any) -> bool:
    if values is None or len(values) == 0:
        return False
    v = values[0]
    if isinstance(v, np.datetime64 | pd.Timestamp):
        return True
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        try:
            pd.Timestamp(v)
            return True
        except ValueError:
            return False
    return hasattr(v, "year") and hasattr(v, "month")


def _x(values: Any) -> Any:
    if values is None:
        return []
    vals = list(values)
    return pd.to_datetime(vals).to_pydatetime() if _is_dates(vals) else vals


def _y(values: Any) -> np.ndarray:
    if values is None:
        return np.array([], dtype=float)
    return np.array([np.nan if v is None else v for v in list(values)], dtype=float)


def _color(trace: Any, i: int) -> str:
    for path in ("line.color", "marker.color", "fillcolor"):
        obj = trace
        for part in path.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if isinstance(obj, str) and not obj.startswith("rgba(0,0,0,0"):
            return obj if not obj.startswith("rgba") else _rgba(obj)
    return PALETTE[i % len(PALETTE)]


def _rgba(s: str) -> Any:
    parts = [float(p) for p in s[s.index("(") + 1:s.index(")")].split(",")]
    return (parts[0] / 255, parts[1] / 255, parts[2] / 255, parts[3] if len(parts) > 3 else 1)


def _axes_for(fig: go.Figure, mfig: plt.Figure) -> dict[tuple[str, str], Any]:
    """Create a matplotlib axes per (xaxis, yaxis) pair using plotly's domains."""
    pairs = []
    for tr in fig.data:
        if tr.type == "scatterpolar":
            pairs.append(("polar", "polar"))
        else:
            pairs.append((getattr(tr, "xaxis", None) or "x", getattr(tr, "yaxis", None) or "y"))
    lay = fig.layout
    out: dict[tuple[str, str], Any] = {}
    primary: dict[str, Any] = {}
    for xa, ya in dict.fromkeys(pairs):
        if xa == "polar":
            out[(xa, ya)] = mfig.add_axes((0.25, 0.14, 0.5, 0.66), projection="polar")
            continue
        xl = lay[f"xaxis{xa[1:]}"] if len(xa) > 1 else lay.xaxis
        yl = lay[f"yaxis{ya[1:]}"] if len(ya) > 1 else lay.yaxis
        overlay = getattr(yl, "overlaying", None)
        if overlay and f"x/{overlay}" in primary:
            ax = primary[f"x/{overlay}"].twinx()
            ax.grid(False)
            ax.spines["right"].set_visible(True)
            out[(xa, ya)] = ax
            continue
        xd = list(xl.domain) if getattr(xl, "domain", None) else [0, 1]
        yd = list(yl.domain) if getattr(yl, "domain", None) else [0, 1]
        left, bottom = 0.09 + xd[0] * 0.86, 0.16 + yd[0] * 0.68
        w, h = max((xd[1] - xd[0]) * 0.86 - 0.03, 0.05), max((yd[1] - yd[0]) * 0.68 - 0.06, 0.05)
        ax = mfig.add_axes((left, bottom, w, h))
        out[(xa, ya)] = ax
        primary[f"x/{ya}"] = ax
    if not out:
        out[("x", "y")] = mfig.add_axes((0.09, 0.16, 0.86, 0.68))
    return out


def _draw_trace(ax: Any, tr: Any, i: int, state: dict[str, Any]) -> bool:
    name = getattr(tr, "name", None) or None
    color = _color(tr, i)
    typ = tr.type
    if typ in ("scatter", "scattergl"):
        x, y = _x(tr.x), _y(tr.y)
        if len(x) == 0:
            return True
        mode = tr.mode or "lines"
        fill = getattr(tr, "fill", None)
        if fill == "tonexty" and state.get("prev_line") is not None:
            px_, py_ = state["prev_line"]
            if len(px_) == len(x):
                fc = _rgba(tr.fillcolor) if (tr.fillcolor or "").startswith("rgba") else color
                ax.fill_between(x, py_, y, color=fc, alpha=0.35 if not isinstance(fc, tuple)
                                else None, linewidth=0, label=name)
                state["prev_line"] = (x, y)
                return True
        if fill == "tozeroy":
            ax.fill_between(x, 0, y, color=color, alpha=0.3, linewidth=0)
        lw = (getattr(tr.line, "width", None) or 1.5) * 0.7
        if lw == 0:
            state["prev_line"] = (x, y)
            return True
        ls = {"dash": "--", "dot": ":", "dashdot": "-."}.get(getattr(tr.line, "dash", None) or "",
                                                              "-")
        if "lines" in mode:
            ax.plot(x, y, color=color, linewidth=lw, linestyle=ls,
                    marker="o" if "markers" in mode else None, markersize=2, label=name)
        else:
            size = getattr(tr.marker, "size", None)
            s = (np.asarray(size, dtype=float) if isinstance(size, list | tuple | np.ndarray)
                 else np.full(len(y), float(size or 4))) ** 2 * 0.25
            ax.scatter(x, y, s=s, color=color, label=name, alpha=0.8, linewidths=0)
        state["prev_line"] = (x, y)
        return True
    if typ == "bar":
        horiz = tr.orientation == "h"
        cats, vals = (tr.y, tr.x) if horiz else (tr.x, tr.y)
        cats = _x(cats)
        base = getattr(tr, "base", None)
        if horiz and base is not None:          # timeline (px.timeline): x = duration ms
            starts = pd.to_datetime(list(base))
            ends = starts + pd.to_timedelta(np.asarray(list(vals), dtype=float), unit="ms")
            ypos = [state["ycats"].setdefault(c, len(state["ycats"])) for c in cats]
            ax.barh(ypos, mdates.date2num(ends) - mdates.date2num(starts),
                    left=mdates.date2num(starts), color=color, height=0.6, label=name,
                    edgecolor=color, linewidth=1.5)
            ax.set_yticks(list(state["ycats"].values()), list(state["ycats"].keys()))
            ax.xaxis_date()
            state["cat_y"].add(id(ax))
            return True
        vals = _y(vals)
        key = id(ax)
        n_series = state["bars"][key]
        state["bars"][key] += 1
        stacked = state["barmode"] in ("stack", "relative")
        if _is_dates(list(cats)):
            pos = mdates.date2num(cats)
            width = (np.median(np.diff(pos)) if len(pos) > 1 else 1.0) * 0.8
        else:
            labels = [str(c) for c in cats]
            for c in labels:
                state["xcats"].setdefault(c, len(state["xcats"]))
            pos = np.array([state["xcats"][c] for c in labels], dtype=float)
            width = 0.8
            ax.set_xticks(list(state["xcats"].values()), list(state["xcats"].keys()),
                          rotation=30 if len(state["xcats"]) > 6 else 0, ha="right"
                          if len(state["xcats"]) > 6 else "center")
            state["cat_x"].add(id(ax))
        if stacked:
            bottom = np.array([state["stack"][(key, p)] for p in pos])
            (ax.barh if horiz else ax.bar)(pos, vals, width, bottom if not horiz else None,
                                           color=color, label=name,
                                           **({"left": bottom} if horiz else {}))
            for p, v in zip(pos, vals, strict=True):
                state["stack"][(key, p)] += 0 if np.isnan(v) else v
        else:
            k = max(state["n_bar_traces"].get(key, 1), 1)
            off = (n_series - (k - 1) / 2) * width / k
            (ax.barh if horiz else ax.bar)(pos + off, vals, width / k, color=color, label=name)
        if _is_dates(list(cats)):
            ax.xaxis_date()
        return True
    if typ == "histogram":
        ax.hist(_y(tr.x if tr.x is not None else tr.y), bins=30, color=color, alpha=0.7,
                label=name)
        return True
    if typ in ("box", "violin"):
        groups: dict[str, list[float]] = defaultdict(list)
        xs = list(tr.x) if tr.x is not None else [name or ""] * len(tr.y)
        for g, v in zip(xs, tr.y, strict=False):
            if v is not None:
                groups[str(g)].append(float(v))
        labels = list(groups)
        start = state["box_pos"].get(id(ax), 0)
        pos = [start + j for j in range(len(labels))]
        state["box_pos"][id(ax)] = start + len(labels)
        bp = ax.boxplot([groups[k] for k in labels], positions=pos, widths=0.6,
                        patch_artist=True, showfliers=False)
        for patch in bp["boxes"]:
            patch.set_facecolor(color)
            patch.set_alpha(0.5)
        ax.set_xticks(pos, labels, rotation=30 if len(labels) > 5 else 0)
        state["cat_x"].add(id(ax))
        return True
    if typ == "heatmap":
        z = np.array([[np.nan if v is None else v for v in row] for row in tr.z], dtype=float)
        scale = str(tr.colorscale or state.get("coloraxis_scale") or "")
        cmap = ("RdBu" if "RdBu" in scale or "rgb(103,0,31)" in scale else
                "Blues" if "Blues" in scale or "rgb(247,251,255)" in scale else "viridis")
        vmin = vmax = None
        if cmap == "RdBu":
            vmin, vmax = -1, 1
        im = ax.imshow(z, aspect="auto", cmap=cmap, interpolation="nearest", origin="lower",
                       vmin=vmin, vmax=vmax)
        if getattr(tr, "texttemplate", None) or state.get("text_auto"):
            for (r_, c_), v in np.ndenumerate(z):
                if not np.isnan(v):
                    norm = im.norm(v)
                    dark = norm > 0.75 or (cmap == "RdBu" and norm < 0.2)
                    ax.text(c_, r_, _hu_number(round(v, 2) + 0.0), ha="center", va="center",
                            fontsize=7, color="white" if dark else "black")
        state["cat_x"].add(id(ax))
        state["cat_y"].add(id(ax))
        if tr.x is not None:
            xs = [str(v)[:18] for v in tr.x]
            step = max(1, len(xs) // 12)
            ax.set_xticks(range(0, len(xs), step), xs[::step], rotation=45, ha="right")
        if tr.y is not None:
            ax.set_yticks(range(len(tr.y)), [str(v) for v in tr.y])
        ax.grid(False)
        ax.figure.colorbar(im, ax=ax, fraction=0.04)
        return True
    if typ == "scatterpolar":
        theta = list(tr.theta) if tr.theta is not None else []
        r = _y(tr.r)
        ang: np.ndarray = np.linspace(0, 2 * np.pi, len(theta), endpoint=False)
        if len(ang):
            ang = np.r_[ang[:-1], ang[0]] if theta[0] == theta[-1] else ang
            ax.plot(ang, r[:len(ang)], color=color, label=name)
            ax.fill(ang, r[:len(ang)], color=color, alpha=0.15)
            uniq = list(dict.fromkeys(theta))
            ax.set_xticks(np.linspace(0, 2 * np.pi, len(uniq), endpoint=False), uniq,
                          fontsize=7)
            ax.set_yticklabels([])
        return True
    return False


def figure_png(fig: go.Figure, *, width_in: float = 7.2, height_in: float | None = None,
               title: str | None = None) -> bytes:
    """Render a plotly figure to PNG bytes (DPI 200)."""
    h = height_in or max(2.2, min(6.0, (fig.layout.height or 320) / 100 * 0.85))
    mfig = plt.figure(figsize=(width_in, h), dpi=DPI)
    try:
        axes = _axes_for(fig, mfig)
        coloraxis = getattr(fig.layout, "coloraxis", None)
        state: dict[str, Any] = {"barmode": fig.layout.barmode or "group", "prev_line": None,
                                 "bars": defaultdict(int), "stack": defaultdict(float),
                                 "xcats": {}, "ycats": {}, "box_pos": {}, "n_bar_traces": {},
                                 "cat_x": set(), "cat_y": set(),
                                 "coloraxis_scale": coloraxis.colorscale if coloraxis else None,
                                 "text_auto": any(getattr(tr, "texttemplate", None)
                                                  for tr in fig.data)}
        for tr in fig.data:
            if tr.type == "bar":
                key = (getattr(tr, "xaxis", None) or "x", getattr(tr, "yaxis", None) or "y")
                ax = axes.get(key)
                if ax is not None:
                    state["n_bar_traces"][id(ax)] = state["n_bar_traces"].get(id(ax), 0) + 1
        skipped = 0
        for i, tr in enumerate(fig.data):
            key = (("polar", "polar") if tr.type == "scatterpolar" else
                   (getattr(tr, "xaxis", None) or "x", getattr(tr, "yaxis", None) or "y"))
            ax = axes.get(key) or next(iter(axes.values()))
            if not _draw_trace(ax, tr, i, state):
                skipped += 1
        for shape in fig.layout.shapes or []:
            if shape.type == "rect" and shape.xref and str(shape.xref).startswith("x") \
                    and "domain" in str(shape.yref or ""):
                key = (str(shape.xref), str(shape.yref).replace(" domain", ""))
                bax = axes.get(key) or next(iter(axes.values()))
                if getattr(bax, "name", "") != "polar":
                    x0, x1 = (mdates.date2num(pd.Timestamp(v)) for v in (shape.x0, shape.x1))
                    bax.axvspan(x0, x1, color=shape.fillcolor or "#f59e0b",
                                alpha=float(shape.opacity or 0.15), linewidth=0, zorder=0)
                    if shape.name:
                        bax.text(x0, 0.98, f" {shape.name}", transform=bax.get_xaxis_transform(),
                                 fontsize=6.5, va="top", ha="left", color="#92400e", clip_on=True)
                continue
            if (shape.type == "line" and shape.y0 == shape.y1
                    and shape.xref in ("x domain", "paper")):
                ax = next(iter(axes.values()))
                dash = {"dash": "--", "dot": ":"}.get(getattr(shape.line, "dash", None) or "", "-")
                ax.axhline(shape.y0, color=shape.line.color or "#6b7280", linestyle=dash,
                           linewidth=1)
        for (xa, ya), ax in axes.items():
            if xa == "polar":
                continue
            yl = fig.layout[f"yaxis{ya[1:]}"] if len(ya) > 1 else fig.layout.yaxis
            xl = fig.layout[f"xaxis{xa[1:]}"] if len(xa) > 1 else fig.layout.xaxis
            if yl.title and yl.title.text:
                ax.set_ylabel(yl.title.text)
            if xl.title and xl.title.text:
                ax.set_xlabel(xl.title.text)
            if yl.autorange == "reversed":
                ax.invert_yaxis()
            if id(ax) in state["cat_x"]:
                pass
            elif ax.xaxis.get_converter() is not None:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y.%m.%d."))
                for lab in ax.get_xticklabels():
                    lab.set_rotation(30)
                    lab.set_ha("right")
            else:
                ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_hu_number))
            if getattr(yl, "tickvals", None) and getattr(yl, "ticktext", None):
                ax.set_yticks(list(yl.tickvals), list(yl.ticktext))
            elif id(ax) not in state["cat_y"]:
                ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_hu_number))
        handles, labels = [], []
        for ax in axes.values():
            h_, l_ = ax.get_legend_handles_labels()
            for hh, ll in zip(h_, l_, strict=True):
                if ll and not ll.startswith("_") and ll not in labels:
                    handles.append(hh)
                    labels.append(ll)
        if handles and fig.layout.showlegend is not False and len(labels) <= 12:
            polar = ("polar", "polar") in axes
            mfig.legend(handles, labels, loc="lower center" if polar else "upper center",
                        ncol=min(len(labels), 4), frameon=False, fontsize=7,
                        bbox_to_anchor=(0.5, 0.0) if polar else (0.5, 0.94))
        t = title or (fig.layout.title.text if fig.layout.title else None)
        if t:
            mfig.suptitle(t, fontsize=8.5, y=0.995)
        if skipped:
            mfig.text(0.5, 0.5, "Ennek az ábrának egy része PDF-ben nem jeleníthető meg.",
                      ha="center", color="#6b7280")
        buf = io.BytesIO()
        mfig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                     metadata={"Software": None})
        return buf.getvalue()
    finally:
        plt.close(mfig)
