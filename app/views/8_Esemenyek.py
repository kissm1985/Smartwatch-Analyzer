"""Események / annotációk (SPEC 8.2/11) – stored locally in dim_event."""

from __future__ import annotations

from datetime import date

import streamlit as st

from vitalmart import events as ev
from vitalmart.i18n import t
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con

cfg = get_cfg()
con = get_con(cfg)
ss = st.session_state
st.title(t("ui.nav.events"))
st.caption(t("events.intro"))
if not q.has_data(con):
    empty_state()
    st.stop()

people = q.persons(con)
names = dict(people)
ALL = ""
person_opts = [ALL, *[k for k, _ in people]]


def person_label(k: str) -> str:
    return t("events.household") if k == ALL else names.get(k, k)


def form(prefix: str, current: ev.Event | None = None) -> dict | None:
    cats = ev.categories(con)
    with st.form(f"{prefix}_form", clear_on_submit=current is None):
        c1, c2 = st.columns(2)
        label = c1.text_input(t("events.label"), value=current.label if current else "",
                              key=f"{prefix}_label")
        person = c2.selectbox(t("events.person"), person_opts, format_func=person_label,
                              index=person_opts.index(current.person_key or ALL)
                              if current else 0, key=f"{prefix}_person")
        last = q.last_data_date(con, people[0][0]) if people else None
        default = current.start_date if current else (last or date.today())
        c3, c4 = st.columns(2)
        start = c3.date_input(t("events.start"), value=default, key=f"{prefix}_start",
                              format="YYYY.MM.DD")
        end = c4.date_input(t("events.end"), value=current.end_date if current else default,
                            key=f"{prefix}_end", format="YYYY.MM.DD")
        opts = [*cats, t("events.other_category")]
        cur_cat = current.category if current else cats[0]
        cat = st.selectbox(t("events.category"), opts,
                           index=opts.index(cur_cat) if cur_cat in opts else len(opts) - 1,
                           key=f"{prefix}_cat")
        custom = st.text_input(t("events.custom_category"), key=f"{prefix}_custom",
                               value="" if cur_cat in opts else cur_cat)
        note = st.text_area(t("events.note"), value=current.note if current else "",
                            key=f"{prefix}_note")
        if st.form_submit_button(t("events.save") if current else t("events.add"),
                                 type="primary"):
            return {"start": start, "end": end, "label": label, "person": person or None,
                    "category": custom.strip() if cat == opts[-1] else cat, "note": note}
    return None


with st.expander(t("events.new"), expanded=True):
    data = form("evn")
    if data:
        try:
            ev.add_event(con, data.pop("start"), data.pop("end"), data.pop("label"), **data)
            ss["ev_flash"] = t("events.added")
            st.rerun()
        except ev.EventError as exc:
            st.error(t(exc.key))
if ss.get("ev_flash"):
    st.success(ss.pop("ev_flash"))

items = ev.list_events(con)
if not items:
    st.info(t("events.none"))
else:
    df = ev.events_frame(items)
    view = df.drop(columns=["event_id"]).copy()
    view["start_date"] = view["start_date"].map(q.fmt_date)
    view["end_date"] = view["end_date"].map(q.fmt_date)
    view["person_key"] = view["person_key"].map(person_label)
    view.columns = [t(f"events.col.{c}") for c in view.columns]
    st.dataframe(view, hide_index=True, width="stretch")
    st.caption(t("events.usage"))

    labels = {e.event_id: f"{q.fmt_date(e.start_date)} – {q.fmt_date(e.end_date)} · {e.label}"
              for e in items}
    eid = st.selectbox(t("events.select"), list(labels), format_func=labels.get, key="ev_sel")
    chosen = ev.get_event(con, eid)
    with st.expander(t("events.edit")):
        data = form(f"eve{eid}", chosen)
        if data:
            try:
                ev.update_event(con, eid, data.pop("start"), data.pop("end"), data.pop("label"),
                                **data)
                ss["ev_flash"] = t("events.saved")
                st.rerun()
            except ev.EventError as exc:
                st.error(t(exc.key))
    if st.button(t("events.delete"), key="ev_delete"):
        ev.delete_event(con, eid)
        ss["ev_flash"] = t("events.deleted")
        st.rerun()
disclaimer()
