"""Haladó elemzés – lag/shift correlation and comparison mode (SPEC 8.2/9–10)."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from vitalmart import events as ev
from vitalmart.i18n import t
from vitalmart.semantic import compare as cp
from vitalmart.semantic import lag
from vitalmart.semantic.model import load_model
from vitalmart.semantic.spec import AnalysisSpec, SpecError
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con, person_picker

cfg = get_cfg()
con = get_con(cfg)
model = load_model()
ss = st.session_state
st.title(t("ui.nav.advanced"))
if not q.has_data(con):
    empty_state()
    st.stop()

person = person_picker(con, key="adv_person")
last = q.last_data_date(con, person)
if person is None or last is None:
    st.info(t("report.ui.no_data"))
    st.stop()


def measure_picker(prefix: str, topics: list[str], default_topic: str, default_measure: str
                   ) -> lag.SeriesRef:
    c1, c2 = st.columns(2)
    topic = c1.selectbox(t("explorer.topic"), topics, key=f"{prefix}_topic",
                         index=topics.index(default_topic) if default_topic in topics else 0,
                         format_func=lambda k: model.topics[k].label_hu)
    ms = list(model.topics[topic].measures)
    measure = c2.selectbox(t("lag.measure"), ms, key=f"{prefix}_measure",
                           index=ms.index(default_measure) if default_measure in ms else 0,
                           format_func=lambda k: model.topics[topic].measures[k].label_hu)
    return lag.SeriesRef(topic, measure)


tab_lag, tab_cmp = st.tabs([t("lag.title"), t("compare.title")])

# ---------------------------------------------------------------- lag correlation
with tab_lag:
    st.caption(t("lag.intro"))
    topics = lag.lag_topics(model)
    st.markdown(f"**{t('lag.first')}**")
    x_ref = measure_picker("lag_x", topics, "load", "load")
    st.markdown(f"**{t('lag.second')}**")
    y_ref = measure_picker("lag_y", topics, "sleep", "asleep_h")
    c1, c2, c3 = st.columns(3)
    days = c1.selectbox(t("lag.period"), [90, 180, 365, 0], index=1, key="lag_days",
                        format_func=lambda d: t("report.ui.days", n=d) if d
                        else t("explorer.period_opt.all"))
    max_lag = c2.slider(t("lag.max_lag"), 1, 30, 7, key="lag_max")
    method = c3.radio(t("explorer.corr_method"), ["spearman", "pearson"], key="lag_method",
                      horizontal=True)
    d0 = last - timedelta(days=days - 1) if days else None
    x = lag.daily_series(con, model, x_ref, person, d0, last if days else None)
    y = lag.daily_series(con, model, y_ref, person, d0, last if days else None)
    xl, yl = lag.label(model, x_ref), lag.label(model, y_ref)
    if x.empty or y.empty:
        st.info(t("lag.no_series"))
    else:
        profile = lag.lag_profile(x, y, range(-max_lag, max_lag + 1))
        best = lag.best_lag(profile, method)
        chosen = st.slider(t("lag.selected"), -max_lag, max_lag,
                           1 if best is None else best, key="lag_selected")
        stat = lag.lag_stat(x, y, chosen)
        st.info(lag.describe(stat, method, xl, yl))
        if best is not None:
            st.caption(t("lag.best", lag=best))
        c1, c2 = st.columns(2)
        c1.plotly_chart(lag.profile_figure(profile, method, chosen), key="lag_profile")
        c2.plotly_chart(lag.scatter_figure(x, y, chosen, xl, yl), key="lag_scatter")
        with st.expander(t("explorer.table")):
            view = profile.rename(columns={c: t(f"lag.col.{c}") for c in profile.columns})
            st.dataframe(view.round(3), hide_index=True, width="stretch")
        st.caption(t("lag.method_note"))

# ---------------------------------------------------------------- comparison mode
with tab_cmp:
    st.caption(t("compare.intro"))
    topic_keys = [k for k in model.topics if not model.topics[k].intraday]
    topic = st.selectbox(t("explorer.topic"), topic_keys, key="cmp_topic",
                         format_func=lambda k: model.topics[k].label_hu)
    tp = model.topics[topic]
    default_m = [next(iter(tp.measures))]
    measures = st.multiselect(t("explorer.measures"), list(tp.measures), default=default_m,
                              max_selections=4, key=f"cmp_measures_{topic}",
                              format_func=lambda k: tp.measures[k].label_hu)
    dim_opts = [d for d in model.topic_dimensions(topic) if d != "person"]
    dims = st.multiselect(t("compare.dimension"), dim_opts, max_selections=1,
                          default=[d for d in tp.required_dimensions],
                          key=f"cmp_dims_{topic}",
                          format_func=lambda k: model.dimensions[k].label_hu)
    kinds = [*cp.PRESETS, "event"]
    kind = st.radio(t("compare.mode"), kinds, key="cmp_kind", horizontal=True,
                    format_func=lambda k: t(f"compare.kind.{k}"))
    a: cp.PeriodDef | None = None
    b: cp.PeriodDef | None = None
    if kind in ("previous", "year"):
        n = st.select_slider(t("compare.length"), [7, 14, 30, 60, 90, 180], value=30,
                             key="cmp_len")
        a, b = cp.preset(kind, last, n)
    elif kind == "month":
        a, b = cp.preset("month", last)
    elif kind == "custom":
        c1, c2 = st.columns(2)
        ra = c1.date_input(t("compare.period_a"), value=(last - timedelta(days=59),
                                                         last - timedelta(days=30)),
                           key="cmp_ra", format="YYYY.MM.DD")
        rb = c2.date_input(t("compare.period_b"), value=(last - timedelta(days=29), last),
                           key="cmp_rb", format="YYYY.MM.DD")
        if isinstance(ra, tuple) and len(ra) == 2 and isinstance(rb, tuple) and len(rb) == 2:
            a = cp.PeriodDef("A", ra[0], ra[1])
            b = cp.PeriodDef("B", rb[0], rb[1])
    else:
        items = ev.list_events(con, [person])
        if not items:
            st.info(t("compare.no_events"))
        else:
            labels = {e.event_id: f"{e.label} ({q.fmt_date(e.start_date)} – "
                                  f"{q.fmt_date(e.end_date)})" for e in items}
            eid = st.selectbox(t("compare.event"), list(labels), format_func=labels.get,
                               key="cmp_event")
            e = ev.get_event(con, eid)
            b = cp.PeriodDef(e.label, e.start_date, e.end_date)
            a1 = e.start_date - timedelta(days=1)
            a = cp.PeriodDef(t("compare.before_event", n=e.days),
                             a1 - timedelta(days=e.days - 1), a1)
    if a and b and measures:
        st.caption(t("compare.periods", a=a.label, a0=q.fmt_date(a.start), a1=q.fmt_date(a.end),
                     b=b.label, b0=q.fmt_date(b.start), b1=q.fmt_date(b.end)))
        try:
            spec = AnalysisSpec.model_validate({"topic": topic, "dimensions": dims,
                                                "measures": [{"key": m} for m in measures],
                                                "persons": [person]})
            res = cp.compare(con, spec, model, a, b)
        except SpecError as exc:
            st.warning(t(exc.key, **exc.kwargs))
            st.stop()
        if res.table.empty or res.table[["a", "b"]].isna().all().all():
            st.info(t("compare.empty"))
        else:
            st.dataframe(cp.display_table(res), hide_index=True, width="stretch")
            st.plotly_chart(cp.bar_figure(res), key="cmp_bar")
            fig = cp.overlay_figure(res)
            if fig is not None:
                st.plotly_chart(fig, key="cmp_overlay")
            st.caption(t("compare.note"))
disclaimer()
