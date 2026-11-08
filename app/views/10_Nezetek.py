"""Saját nézetek – several saved analyses on one page (SPEC 8.3 SHOULD)."""

from __future__ import annotations

import streamlit as st

from vitalmart.i18n import t
from vitalmart.semantic import store
from vitalmart.semantic import views as vw
from vitalmart.semantic.charts import build_figure
from vitalmart.semantic.engine import display_frame
from vitalmart.semantic.model import load_model
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con

cfg = get_cfg()
con = get_con(cfg)
model = load_model()
ss = st.session_state
st.title(t("ui.nav.views"))
st.caption(t("views.intro"))
if not q.has_data(con):
    empty_state()
    st.stop()

analyses = {a.analysis_id: a for a in store.list_analyses(con)}
people = q.persons(con)
NEW = ""
all_views = vw.list_views(con)
names = {v.view_id: v.name for v in all_views}
if "vw_pending" in ss:
    ss["vw_select"] = ss.pop("vw_pending")
if ss.get("vw_select") not in [NEW, *names]:
    ss["vw_select"] = next(iter(names), NEW)
choice = st.selectbox(t("views.select"), [NEW, *names], key="vw_select",
                      format_func=lambda k: names.get(k, t("views.new")))
current = vw.get_view(con, choice) if choice else None
if ss.get("vw_flash"):
    st.success(ss.pop("vw_flash"))

with st.expander(t("views.edit") if current else t("views.new"), expanded=current is None):
    key = choice or "new"
    name = st.text_input(t("explorer.name"), value=current.name if current else "",
                         key=f"vw_name_{key}")
    desc = st.text_area(t("explorer.description"), value=current.description if current
                        else "", key=f"vw_desc_{key}")
    items = st.multiselect(t("views.items"), list(analyses), max_selections=vw.MAX_ITEMS,
                           default=[i for i in (current.items if current else [])
                                    if i in analyses], key=f"vw_items_{key}",
                           format_func=lambda k: analyses[k].name)
    c1, c2, c3 = st.columns(3)
    cols = c1.radio(t("views.columns"), [1, 2, 3], horizontal=True, key=f"vw_cols_{key}",
                    index=(current.columns if current else 2) - 1)
    periods = ["", *vw.OVERRIDE_PERIODS]
    period = c2.selectbox(t("views.period"), periods, key=f"vw_period_{key}",
                          index=periods.index(current.period or "") if current else 0,
                          format_func=lambda p: t(f"explorer.period_opt.{p}") if p
                          else t("views.keep"))
    persons = c3.multiselect(t("views.persons"), [k for k, _ in people],
                             default=current.persons or [] if current else [],
                             key=f"vw_persons_{key}", format_func=dict(people).get,
                             placeholder=t("views.keep"))
    b1, b2 = st.columns(2)
    if b1.button(t("explorer.save_update") if current else t("explorer.save_new"),
                 type="primary", key=f"vw_save_{key}"):
        try:
            vid = vw.save_view(con, name, items, description=desc, columns=cols,
                               period=period or None, persons=persons or None,
                               view_id=current.view_id if current else None)
            ss["vw_pending"] = vid
            ss["vw_flash"] = t("explorer.saved_ok")
            st.rerun()
        except vw.ViewError as exc:
            st.error(t(exc.key))
    if current and b2.button(t("explorer.delete"), key=f"vw_delete_{key}"):
        vw.delete_view(con, current.view_id)
        ss["vw_pending"] = NEW
        ss["vw_flash"] = t("explorer.deleted")
        st.rerun()

if current:
    if current.description:
        st.caption(current.description)
    grid = st.columns(current.columns)
    for i, item in enumerate(vw.run_view(con, current, model)):
        with grid[i % current.columns].container(border=True):
            st.markdown(f"**{item.analysis.name if item.analysis else item.analysis_id}**")
            if item.error:
                st.warning(t(item.error, **item.error_kwargs))
                continue
            res = item.result
            assert res is not None
            if res.spec.chart == "table":
                st.dataframe(q.labeled(display_frame(res)), hide_index=True, width="stretch")
            else:
                st.plotly_chart(build_figure(res), key=f"vw_fig_{i}")
            st.caption(t("explorer.info_rows", rows=q.fmt_num(res.n_rows),
                         date_from=q.fmt_date(res.date_from), date_to=q.fmt_date(res.date_to)))
disclaimer()
