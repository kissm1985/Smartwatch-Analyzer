"""HTML rendering (Jinja2). Plotly is embedded inline once – no CDN (SPEC 10.1).
Chart div ids are deterministic so a snapshot re-renders byte-identically (SPEC 7.5)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import plotly.io as pio
from jinja2 import Environment, FileSystemLoader, select_autoescape
from plotly.offline import get_plotlyjs

from vitalmart import __version__
from vitalmart.fmt import fmt_date, fmt_datetime, fmt_num
from vitalmart.i18n import t

if TYPE_CHECKING:
    from vitalmart.report.engine import Report

TEMPLATES = Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES),
                      autoescape=select_autoescape(["html", "j2"]), trim_blocks=True,
                      lstrip_blocks=True)
    env.filters["date"] = fmt_date
    env.filters["datetime"] = fmt_datetime
    env.filters["num"] = fmt_num
    env.globals["t"] = t
    return env


def chart_html(fig, div_id: str) -> str:
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id=div_id,
                       config={"displayModeBar": False, "responsive": True},
                       validate=False)


def render_html(rep: Report, *, embed_plotly: bool = True) -> str:
    from vitalmart.report.engine import finding_label, period_text

    charts = {c.chart_id: chart_html(c.fig, f"chart-{c.chart_id}")
              for s in rep.sections for c in s.charts}
    tpl = _env().get_template("report.html.j2")
    return tpl.render(rep=rep, charts=charts, plotly_js=get_plotlyjs() if embed_plotly else "",
                      finding_label=finding_label, period_text=period_text(rep),
                      app_version=__version__)
