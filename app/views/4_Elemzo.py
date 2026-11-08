"""Elemző (Explorer) – analysis builder over the semantic layer (SPEC 8)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import streamlit as st

from vitalmart.export.service import ANALYSIS_FORMATS, export_analysis
from vitalmart.i18n import t
from vitalmart.semantic import store
from vitalmart.semantic.charts import build_figure
from vitalmart.semantic.engine import display_frame, run
from vitalmart.semantic.model import load_model
from vitalmart.semantic.spec import MAX_DIMENSIONS, MAX_MEASURES, AnalysisSpec, SpecError
from vitalmart.semantic.sqlgen import distinct_values_query
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con

cfg = get_cfg()
con = get_con(cfg)
model = load_model()
ss = st.session_state
st.title(t("explorer.title"))
st.caption(t("explorer.intro"))
if not q.has_data(con):
    empty_state()
    st.stop()

people = q.persons(con)
person_keys = [k for k, _ in people]
person_names = dict(people)
current_person = ss.get("person_global") if ss.get("person_global") in person_keys \
    else person_keys[0]

GRAINS = ["day", "week", "month", "quarter", "year", "none"]
INTRADAY_GRAINS = ["hour", "raw"]
PERIODS = ["last_7", "last_30", "last_90", "last_180", "last_365", "all", "custom"]
TRANSFORMS = ["none", "moving_avg", "cumsum", "diff", "pct_change", "index100", "zscore",
              "minmax"]
CHARTS = ["line", "bar", "bar_stacked", "area", "scatter", "bubble", "histogram", "box",
          "violin", "heatmap", "calendar", "corr", "radar", "timeline", "table"]


def load_into_state(spec: AnalysisSpec, *, scope_person: bool = True) -> None:
    """Write a definition into the widget state (then rerun)."""
    ss["ex_topic"] = spec.topic
    ss["ex_measures"] = [m.key for m in spec.measures]
    for m in spec.measures:
        ss[f"ex_agg_{m.key}"] = m.agg or model.topics[spec.topic].measures[m.key].agg
    ss["ex_dims"] = list(spec.dimensions)
    ss["ex_grain"] = spec.time_grain
    ss["ex_period"] = spec.period
    if spec.date_from and spec.date_to:
        ss["ex_dates"] = (spec.date_from, spec.date_to)
    persons = list(spec.persons)
    if not persons and scope_person and "person" not in spec.dimensions:
        persons = [current_person]
    ss["ex_persons"] = [p for p in persons if p in person_keys]
    ss["ex_filter_dims"] = [f.dimension for f in spec.filters]
    for f in spec.filters:
        ss[f"ex_fv_{f.dimension}"] = list(f.values)
        ss[f"ex_fx_{f.dimension}"] = f.exclude
    vf = spec.value_filters[0] if spec.value_filters else None
    ss["ex_vf_measure"] = vf.measure if vf else ""
    ss["ex_vf_min"] = vf.min if vf else None
    ss["ex_vf_max"] = vf.max if vf else None
    ss["ex_quality"] = spec.quality_ok_only
    ss["ex_transform"] = spec.transform
    ss["ex_window"] = spec.transform_window
    ss["ex_chart"] = spec.chart
    ss["ex_secondary"] = spec.secondary_measure or ""
    ss["ex_facet"] = spec.facet or ""
    ss["ex_corr"] = spec.corr_method
    ss["ex_events"] = spec.show_events


if "ex_topic" not in ss:
    load_into_state(store.get(con, "builtin:steps_weekly").spec)

# ---------------------------------------------------------------- saved analyses
with st.expander(t("explorer.saved"), expanded=False):
    tags = store.all_tags(con)
    tag = st.selectbox(t("explorer.tag"), ["", *tags], key="ex_tag",
                       format_func=lambda x: x or t("explorer.all_tags"))
    items = store.list_analyses(con, tag or None)
    labels = {a.analysis_id: (f"🔒 {a.name}" if a.is_builtin else a.name) for a in items}
    sel = st.selectbox(t("explorer.saved"), list(labels), format_func=lambda k: labels[k],
                       key="ex_saved_sel")
    c1, c2, c3 = st.columns(3)
    if c1.button(t("explorer.open"), key="ex_open") and sel:
        a = store.get(con, sel)
        load_into_state(a.spec)
        ss["ex_loaded"] = a.analysis_id
        ss["ex_name"] = "" if a.is_builtin else a.name
        ss["ex_desc"] = a.description
        ss["ex_tags"] = ", ".join(a.tags)
        st.rerun()
    if c2.button(t("explorer.duplicate"), key="ex_dup") and sel:
        new_id = store.duplicate(con, sel)
        ss["ex_loaded"] = new_id
        a = store.get(con, new_id)
        load_into_state(a.spec)
        ss["ex_name"], ss["ex_desc"], ss["ex_tags"] = a.name, a.description, ", ".join(a.tags)
        st.rerun()
    if c3.button(t("explorer.delete"), key="ex_del") and sel:
        try:
            store.delete(con, sel)
            ss.pop("ex_loaded", None)
            ss.pop("ex_saved_sel", None)
            st.rerun()
        except store.StoreError as exc:
            st.error(t(exc.key))
    up = st.file_uploader(t("explorer.upload"), type=["json"], key="ex_upload")
    if up is not None and ss.get("ex_upload_done") != up.file_id:
        try:
            load_into_state(AnalysisSpec.model_validate(json.loads(up.getvalue())),
                            scope_person=False)
            ss["ex_upload_done"] = up.file_id
            st.rerun()
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(str(exc))

left, right = st.columns([1, 2], gap="large")

# ---------------------------------------------------------------- builder
with left:
    topics = list(model.topics)
    topic_key = st.selectbox(t("explorer.topic"), topics, key="ex_topic",
                             format_func=lambda k: model.topics[k].label_hu)
    topic = model.topics[topic_key]
    m_opts = list(topic.measures)
    ss["ex_measures"] = [m for m in ss.get("ex_measures", []) if m in m_opts] or [m_opts[0]]
    measures = st.multiselect(t("explorer.measures"), m_opts, key="ex_measures",
                              max_selections=MAX_MEASURES,
                              format_func=lambda k: topic.measures[k].label_hu)
    with st.expander(t("explorer.aggs")):
        aggs = {}
        for mk in measures:
            opts = list(model.aggregations)
            if ss.get(f"ex_agg_{mk}") not in opts:
                ss[f"ex_agg_{mk}"] = topic.measures[mk].agg
            aggs[mk] = st.selectbox(topic.measures[mk].label_hu, opts, key=f"ex_agg_{mk}",
                                    format_func=lambda a: model.aggregations[a]["label_hu"])
    d_opts = model.topic_dimensions(topic_key)
    ss["ex_dims"] = [d for d in ss.get("ex_dims", []) if d in d_opts]
    dims = st.multiselect(t("explorer.dimensions"), d_opts, key="ex_dims",
                          max_selections=MAX_DIMENSIONS, placeholder=t("explorer.none"),
                          format_func=lambda k: model.dimensions[k].label_hu)
    g_opts = GRAINS + (INTRADAY_GRAINS if topic.ts_expr else [])
    if ss.get("ex_grain") not in g_opts:
        ss["ex_grain"] = "day"
    grain = st.selectbox(t("explorer.grain"), g_opts, key="ex_grain",
                         format_func=lambda g: t(f"explorer.grain_opt.{g}"))
    period = st.selectbox(t("explorer.period"), PERIODS, key="ex_period",
                          format_func=lambda p: t(f"explorer.period_opt.{p}"))
    d_from = d_to = None
    if period == "custom":
        ss.setdefault("ex_dates", (date.today().replace(day=1), date.today()))
        rng = st.date_input(t("explorer.date_range"), key="ex_dates")
        if isinstance(rng, tuple) and len(rng) == 2:
            d_from, d_to = rng
    if "ex_persons" not in ss:
        ss["ex_persons"] = [current_person]
    persons = st.multiselect(t("explorer.persons"), person_keys, key="ex_persons",
                             format_func=lambda k: person_names.get(k, k),
                             placeholder=t("explorer.all_persons"))
    with st.expander(t("explorer.filters")):
        ss["ex_filter_dims"] = [d for d in ss.get("ex_filter_dims", []) if d in d_opts]
        fdims = st.multiselect(t("explorer.filter_dims"), d_opts, key="ex_filter_dims",
                               placeholder=t("explorer.none"),
                               format_func=lambda k: model.dimensions[k].label_hu)
        filters = []
        for fd in fdims:
            vals = [r[0] for r in con.execute(distinct_values_query(topic_key, fd, model))
                    .fetchall()]
            ss[f"ex_fv_{fd}"] = [v for v in ss.get(f"ex_fv_{fd}", []) if v in vals]
            chosen = st.multiselect(model.dimensions[fd].label_hu, vals, key=f"ex_fv_{fd}",
                                    placeholder=t("explorer.none"))
            excl = st.checkbox(t("explorer.exclude"), key=f"ex_fx_{fd}")
            filters.append({"dimension": fd, "values": chosen, "exclude": excl})
        st.caption(t("explorer.value_filter"))
        if ss.get("ex_vf_measure", "") not in ["", *measures]:
            ss["ex_vf_measure"] = ""
        vf_m = st.selectbox(t("explorer.vf_measure"), ["", *measures], key="ex_vf_measure",
                            format_func=lambda k: topic.measures[k].label_hu if k
                            else t("explorer.none"))
        c1, c2 = st.columns(2)
        ss.setdefault("ex_vf_min", None)
        ss.setdefault("ex_vf_max", None)
        ss.setdefault("ex_quality", True)
        vf_min = c1.number_input(t("explorer.vf_min"), key="ex_vf_min")
        vf_max = c2.number_input(t("explorer.vf_max"), key="ex_vf_max")
        quality = st.toggle(t("explorer.quality_ok"), key="ex_quality")
    tr = st.selectbox(t("explorer.transform"), TRANSFORMS, key="ex_transform",
                      format_func=lambda x: t(f"explorer.transform_opt.{x}"))
    ss.setdefault("ex_window", 7)
    window = st.number_input(t("explorer.window"), 2, 365, key="ex_window",
                             disabled=tr != "moving_avg")
    chart = st.selectbox(t("explorer.chart"), CHARTS, key="ex_chart",
                         format_func=lambda c: t(f"explorer.chart_opt.{c}"))
    if ss.get("ex_secondary", "") not in ["", *measures]:
        ss["ex_secondary"] = ""
    secondary = st.selectbox(t("explorer.secondary"), ["", *measures], key="ex_secondary",
                             format_func=lambda k: topic.measures[k].label_hu if k
                             else t("explorer.none"))
    if ss.get("ex_facet", "") not in ["", *dims]:
        ss["ex_facet"] = ""
    facet = st.selectbox(t("explorer.facet"), ["", *dims], key="ex_facet",
                         format_func=lambda k: model.dimensions[k].label_hu if k
                         else t("explorer.none"))
    corr = st.radio(t("explorer.corr_method"), ["spearman", "pearson"], key="ex_corr",
                    horizontal=True)
    show_events = st.checkbox(t("explorer.show_events"), value=True, key="ex_events")

spec_dict = {
    "topic": topic_key,
    "measures": [{"key": m, "agg": aggs.get(m)} for m in measures],
    "dimensions": dims, "time_grain": grain, "period": period,
    "date_from": d_from, "date_to": d_to, "persons": persons,
    "filters": [f for f in filters if f["values"]],
    "value_filters": ([{"measure": vf_m, "min": vf_min, "max": vf_max}]
                      if vf_m and (vf_min is not None or vf_max is not None) else []),
    "quality_ok_only": quality, "transform": tr, "transform_window": int(window),
    "chart": chart, "secondary_measure": secondary or None, "facet": facet or None,
    "corr_method": corr, "show_events": show_events,
}

# ---------------------------------------------------------------- result
with right:
    try:
        spec = AnalysisSpec.model_validate(spec_dict)
        res = run(con, spec, model)
    except SpecError as exc:
        st.warning(t(exc.key, **exc.kwargs))
        st.stop()
    if "person" not in dims and len(persons) != 1 and len(person_keys) > 1:
        st.info(t("explorer.person_scoped"))
    if chart != "table":
        st.plotly_chart(build_figure(res), key="ex_fig")
    shown_from, shown_to = res.date_from, res.date_to
    if shown_from is None and "period" in res.df.columns and len(res.df):
        shown_from, shown_to = res.df["period"].min(), res.df["period"].max()
    st.caption(t("explorer.info_rows", rows=q.fmt_num(res.n_rows),
                 date_from=q.fmt_date(shown_from), date_to=q.fmt_date(shown_to)))
    if res.truncated:
        st.warning(t("explorer.info_truncated"))
    if res.downsampled_from:
        st.caption(t("explorer.info_downsampled", n=q.fmt_num(res.downsampled_from)))
    if chart in ("corr", "scatter", "bubble"):
        st.caption("⚠️ " + t("explorer.corr_note"))

    with st.expander(t("explorer.table"), expanded=chart == "table"):
        table = display_frame(res)
        cols = st.multiselect(t("explorer.columns"), list(table.columns),
                              default=list(table.columns), placeholder=t("explorer.none"))
        st.dataframe(q.labeled(table[cols]) if cols else table, hide_index=True,
                     width="stretch")
    with st.expander(t("explorer.sql")):
        st.code(res.query.sql, language="sql")
        st.caption(t("explorer.params") + ": " + ", ".join(map(str, res.query.params)))

    with st.expander(t("explorer.save_title")):
        name = st.text_input(t("explorer.name"), key="ex_name")
        desc = st.text_area(t("explorer.description"), key="ex_desc")
        tags_in = st.text_input(t("explorer.tags"), key="ex_tags")
        tag_list = [x.strip() for x in tags_in.split(",") if x.strip()]
        c1, c2, c3 = st.columns(3)
        loaded = ss.get("ex_loaded", "")
        if ss.pop("ex_flash", None):
            st.success(t("explorer.saved_ok"))
        if c1.button(t("explorer.save_new"), key="ex_save_new"):
            try:
                ss["ex_loaded"] = store.save(con, name, spec, description=desc, tags=tag_list)
                ss["ex_flash"] = True
                st.rerun()                       # refresh the saved-analyses list
            except store.StoreError as exc:
                st.error(t(exc.key))
        if c2.button(t("explorer.save_update"), key="ex_save_upd",
                     disabled=not loaded.startswith("user:")):
            try:
                store.save(con, name, spec, description=desc, tags=tag_list, analysis_id=loaded)
                ss["ex_flash"] = True
                st.rerun()
            except store.StoreError as exc:
                st.error(t(exc.key))
        c3.download_button(t("explorer.download"),
                           spec.model_dump_json(indent=2, exclude_defaults=True),
                           file_name=f"{(name or 'elemzes').replace(' ', '_')}.json",
                           mime="application/json", key="ex_dl")

    with st.expander(t("export.ui.analysis_title")):
        loaded_name = next((a.name for a in store.list_analyses(con)
                            if a.analysis_id == ss.get("ex_loaded")), "")
        x_title = st.text_input(t("export.ui.analysis_name"), value=loaded_name or
                                topic.label_hu, key="ex_x_title")
        x_note = st.text_area(t("export.ui.analysis_note"), key="ex_x_note")
        cols_x = st.columns(len(ANALYSIS_FORMATS))
        for col_x, fmt in zip(cols_x, ANALYSIS_FORMATS, strict=True):
            if col_x.button(t(f"export.ui.analysis_{fmt}"), key=f"ex_x_{fmt}"):
                with st.spinner(t("export.ui.creating")):
                    out = export_analysis(con, cfg, spec, fmt, title=x_title or topic.label_hu,
                                          note=x_note, model=model)
                ss["ex_x_path"] = str(out.path)
        x_path = ss.get("ex_x_path")
        if x_path and Path(x_path).exists():
            st.success(t("report.ui.saved_to", path=x_path))
            st.download_button(t("export.ui.download") + f" – {Path(x_path).name}",
                               Path(x_path).read_bytes(), file_name=Path(x_path).name,
                               key="ex_x_dl")
disclaimer()
