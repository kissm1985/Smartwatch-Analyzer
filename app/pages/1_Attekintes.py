"""Áttekintés – key indicators vs. personal baseline (SPEC 6.2)."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from vitalmart.i18n import t
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con, person_picker

cfg = get_cfg()
con = get_con(cfg)
st.title(t("ui.overview.title"))

if not q.has_data(con):
    empty_state()
    st.stop()

person = person_picker(con)
days = st.segmented_control(t("ui.overview.period"), [7, 30, 90], default=30,
                            format_func=lambda d: t("ui.days", n=d), key="ov_days") or 30
last = q.last_data_date(con, person)
st.caption(t("ui.overview.last_data", date=q.fmt_date(last)))

labels = {k: t(k) for _, k, *_ in q.CARD_DEFS}
cards = q.overview_cards(con, person, days, baseline_days=cfg.derived.baseline_window_days,
                         labels=labels)
if not cards:
    st.info(t("ui.overview.no_cards"))
help_txt = t("ui.overview.vs_baseline", days=cfg.derived.baseline_window_days, min=14)


def _digits(c: q.Card) -> int:
    return 1 if c.metric_key in ("resting_hr", "stress_avg") or c.metric_key.startswith(
        "hrv") else 0


def _value(c: q.Card) -> str:
    if c.metric_key == "sleep_min":
        return q.fmt_hm((c.value or 0) * 60)
    digits = _digits(c)
    return f"{q.fmt_num(c.value, digits)} {c.unit}"


def _delta(c: q.Card) -> str | None:
    if c.delta is None:
        return None
    sign = "+" if c.delta > 0 else ""
    pct = f" ({sign}{q.fmt_num(c.delta_pct, 0)} %)" if c.delta_pct is not None else ""
    if c.metric_key == "sleep_min":          # value is in hours, show the change in minutes
        return f"{sign}{q.fmt_num(c.delta * 60, 0)} p{pct}"
    return f"{sign}{q.fmt_num(c.delta, _digits(c))} {c.unit}{pct}"


cols = st.columns(3)
for i, c in enumerate(cards):
    with cols[i % 3], st.container(border=True):
        color = {"higher": "normal", "lower": "inverse"}.get(c.better, "off")
        st.metric(c.label, _value(c), delta=_delta(c), delta_color=color, help=help_txt)
        if c.delta is None:
            st.caption(t("ui.overview.no_baseline"))
        if c.note:
            st.caption(t("ui.overview.hrv_note", method=c.note))
        if len(c.series) > 1:
            fig = go.Figure(go.Scatter(x=[d for d, _ in c.series], y=[v for _, v in c.series],
                                       mode="lines", line={"width": 2},
                                       hovertemplate="%{x|%Y.%m.%d.}: %{y:.1f}<extra></extra>"))
            fig.update_layout(height=70, margin={"l": 0, "r": 0, "t": 0, "b": 0},
                              xaxis={"visible": False}, yaxis={"visible": False},
                              showlegend=False)
            st.plotly_chart(fig, config={"displayModeBar": False, "staticPlot": False},
                            key=f"spark_{c.metric_key}")

with st.expander(t("ui.overview.sources")):
    st.dataframe(q.labeled(q.freshness(con, person)), hide_index=True, width="stretch")
disclaimer()
