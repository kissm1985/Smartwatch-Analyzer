"""Egészségügyi riport – rule-based report (SPEC 7): HTML view + PDF export (SPEC 8.1)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from vitalmart.export.service import export_report_pdf
from vitalmart.i18n import t
from vitalmart.report.engine import default_period, generate, list_snapshots, regenerate
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con, person_picker

cfg = get_cfg()
con = get_con(cfg)
ss = st.session_state
st.title(t("ui.nav.report"))
if not q.has_data(con):
    empty_state()
    st.stop()

person = person_picker(con, key="rp_person")
choice = st.segmented_control(t("report.ui.period"), [30, 90, 180, 365, 0], default=90,
                              key="rp_days",
                              format_func=lambda d: t("report.ui.days", n=d) if d
                              else t("report.ui.custom")) or 90
rng = default_period(con, person, choice or 90)
if rng is None:
    st.info(t("report.ui.no_data"))
    st.stop()
start, end = rng
if choice == 0:
    picked = st.date_input(t("report.ui.period"), value=(end - timedelta(days=89), end),
                           key="rp_range")
    if isinstance(picked, tuple) and len(picked) == 2:
        start, end = picked
st.caption(f"{q.fmt_date(start)} – {q.fmt_date(end)}")

if st.button(t("report.ui.generate"), type="primary", key="rp_generate"):
    with st.spinner(t("report.ui.generating")):
        rep, html, path = generate(con, cfg, person, start, end)
    ss["rp_html"], ss["rp_path"] = html, str(path)
    ss["rp_params"] = (person, start, end, rep.generated_at)
    ss.pop("rp_pdf", None)

if ss.get("rp_html"):
    st.success(t("report.ui.saved_to", path=ss["rp_path"]))
    c1, c2 = st.columns(2)
    c1.download_button(t("report.ui.download"), ss["rp_html"], mime="text/html",
                       file_name=Path(ss["rp_path"]).name, key="rp_download")
    if ss.get("rp_params") and c2.button(t("export.ui.report_pdf"), key="rp_pdf_make"):
        p_person, p_start, p_end, p_gen = ss["rp_params"]
        with st.spinner(t("report.ui.generating")):
            ss["rp_pdf"] = str(export_report_pdf(con, cfg, p_person, p_start, p_end,
                                                 generated_at=p_gen).path)
    if ss.get("rp_pdf") and Path(ss["rp_pdf"]).exists():
        c2.download_button(t("export.ui.report_pdf_download"), Path(ss["rp_pdf"]).read_bytes(),
                           mime="application/pdf", file_name=Path(ss["rp_pdf"]).name,
                           key="rp_pdf_download")
    components.html(ss["rp_html"], height=1400, scrolling=True)

with st.expander(t("report.ui.snapshots")):
    snaps = list_snapshots(con)
    if snaps.empty:
        st.caption(t("ui.quality.none"))
    else:
        st.dataframe(q.labeled(snaps), hide_index=True, width="stretch")
        sid = st.selectbox("ID", list(snaps["snapshot_id"]), key="rp_snap")
        if st.button(t("report.ui.regenerate"), key="rp_regen"):
            _rep, html, same = regenerate(con, cfg, sid)
            (st.success if same else st.warning)(
                t("report.ui.identical") if same else t("report.ui.changed"))
            ss["rp_html"] = html
            ss["rp_path"] = str(snaps.set_index("snapshot_id").loc[sid, "html_path"])
            ss["rp_params"] = (_rep.person, _rep.period.start, _rep.period.end,
                               _rep.generated_at)
            ss.pop("rp_pdf", None)
disclaimer()

