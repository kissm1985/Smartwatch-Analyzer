"""Adatminőség – coverage, gaps, flagged values, non-wear, source switches (SPEC 4.5)."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from vitalmart.i18n import t
from vitalmart.ui import queries as q
from vitalmart.ui.common import empty_state, get_cfg, get_con, person_picker

cfg = get_cfg()
con = get_con(cfg)
st.title(t("ui.quality.title"))
if not q.has_data(con):
    empty_state()
    st.stop()

person = person_picker(con, key="dq_person")
cov, gp, fl, nw, sw = (q.coverage(con, person), q.gaps(con, person), q.flags(con, person),
                       q.nonwear(con, person), q.switches(con, person))

m = st.columns(5)
m[0].metric(t("ui.quality.sources"), q.fmt_num(cov["source_key"].nunique() if len(cov) else 0))
daily = cov[cov["coverage_pct"] >= 50] if len(cov) else cov
m[1].metric(t("ui.quality.coverage"),
            f"{q.fmt_num(daily['coverage_pct'].mean() if len(daily) else None, 1)} %")
m[2].metric(t("ui.quality.gaps"), q.fmt_num(len(gp)))
m[3].metric(t("ui.quality.flagged"), q.fmt_num(int(fl["n"].sum()) if len(fl) else 0))
m[4].metric(t("ui.quality.nonwear"), q.fmt_num(nw["hours"].sum() if len(nw) else 0, 1))


def table(df) -> None:
    if df.empty:
        st.caption(t("ui.quality.none"))
        return
    st.dataframe(q.labeled(df), hide_index=True, width="stretch")


tabs = st.tabs([t("ui.quality.tab_coverage"), t("ui.quality.tab_gaps"),
                t("ui.quality.tab_flags"), t("ui.quality.tab_nonwear"),
                t("ui.quality.tab_switch")])
with tabs[0]:
    mc = q.monthly_coverage(con, person)
    if len(mc):
        st.caption(t("ui.quality.heatmap"))
        mc["metric"] = [q.metric_label(con, k) for k in mc["metric_key"]]
        pivot = mc.pivot(index="metric", columns="month", values="days").fillna(0)
        fig = px.imshow(pivot, aspect="auto", color_continuous_scale="Blues",
                        labels={"color": "nap", "x": "", "y": ""})
        fig.update_xaxes(tickformat="%Y.%m.")
        fig.update_layout(height=max(300, 18 * len(pivot)), margin={"l": 0, "r": 0, "t": 10})
        st.plotly_chart(fig, key="dq_heatmap")
    table(cov)
with tabs[1]:
    table(gp)
with tabs[2]:
    st.caption(t("ui.quality.flags_note"))
    table(fl)
with tabs[3]:
    table(nw)
with tabs[4]:
    table(sw)
