"""SQL-konzol (SPEC 8.4, MAY) – read-only queries against the star schema."""

from __future__ import annotations

import streamlit as st

from vitalmart import sqlconsole as sc
from vitalmart.i18n import t
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con

cfg = get_cfg()
con = get_con(cfg)
ss = st.session_state
st.title(t("ui.nav.sql"))
st.caption(t("sql.intro", rows=q.fmt_num(sc.MAX_ROWS), s=int(sc.TIMEOUT_S)))
if not q.has_data(con):
    empty_state()
    st.stop()

left, right = st.columns([3, 2])
with right:
    st.subheader(t("sql.schema"))
    sch = sc.schema(con)
    tables = sorted(sch["table"].unique())
    tbl = st.selectbox(t("sql.table"), tables, key="sql_table")
    view = sch[sch["table"] == tbl].drop(columns=["table"])
    view.columns = [t(f"sql.col.{c}") for c in view.columns]
    st.dataframe(view, hide_index=True, width="stretch", height=360)
    st.subheader(t("sql.examples"))
    for key, text in sc.EXAMPLES.items():
        if st.button(t(key), key=f"sql_ex_{key}"):
            ss["sql_text"] = text
            st.rerun()

with left:
    ss.setdefault("sql_text", "SELECT * FROM dim_person")
    sql = st.text_area(t("sql.query"), key="sql_text", height=260)
    if st.button(t("sql.run"), type="primary", key="sql_run"):
        try:
            res = sc.execute(con, sql)
            ss["sql_result"] = res
            ss.pop("sql_error", None)
        except sc.SqlError as exc:
            ss["sql_error"] = str(exc)
            ss.pop("sql_result", None)
    if ss.get("sql_error"):
        st.error(ss["sql_error"])
    res = ss.get("sql_result")
    if res is not None:
        st.caption(t("sql.info", rows=q.fmt_num(len(res.df)),
                     s=q.fmt_num(res.elapsed_s, 2)))
        if res.truncated:
            st.warning(t("sql.truncated", rows=q.fmt_num(sc.MAX_ROWS)))
        st.dataframe(res.df, hide_index=True, width="stretch")
        st.download_button(t("sql.download"), res.df.to_csv(index=False).encode("utf-8"),
                           file_name="lekerdezes.csv", mime="text/csv", key="sql_dl")
disclaimer()
