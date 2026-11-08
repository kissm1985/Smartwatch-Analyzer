"""Export – raw / derived data as XLSX, CSV, Parquet + export history (SPEC 8.2–8.3)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from vitalmart.export import service, tabular
from vitalmart.i18n import t
from vitalmart.ui import queries as q
from vitalmart.ui.common import disclaimer, empty_state, get_cfg, get_con

MIME = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv", "parquet": "application/vnd.apache.parquet",
        "zip": "application/zip", "pdf": "application/pdf"}
LARGE_ROWS = 200_000

cfg = get_cfg()
con = get_con(cfg)
ss = st.session_state
st.title(t("ui.nav.export"))
st.caption(t("export.ui.intro"))
if not q.has_data(con):
    empty_state()
    st.stop()

reg = tabular.load_registry()


def download(path: Path, key: str) -> None:
    if not path.exists():
        st.warning(t("export.err.missing_file", path=path))
        return
    st.download_button(t("export.ui.download") + f" – {path.name}", path.read_bytes(),
                       file_name=path.name, key=key,
                       mime=MIME.get(path.suffix.lstrip("."), "application/octet-stream"))


tab_raw, tab_hist = st.tabs([t("export.ui.tab_raw"), t("export.ui.tab_history")])

with tab_raw:
    tables = st.multiselect(
        t("export.ui.tables"), list(reg.tables), default=reg.default_tables, key="xp_tables",
        format_func=lambda k: f"{reg.tables[k].label_hu} · "
                              f"{t('export.ui.group_' + reg.tables[k].group)}")
    people = q.persons(con)
    persons = st.multiselect(t("export.ui.persons"), [k for k, _ in people], key="xp_persons",
                             format_func=dict(people).get)
    all_time = st.checkbox(t("export.ui.all_time"), value=True, key="xp_all")
    d_from: date | None = None
    d_to: date | None = None
    if not all_time:
        last = max((q.last_data_date(con, k) or date.today()) for k, _ in people)
        picked = st.date_input(t("export.ui.period"), value=(last.replace(day=1), last),
                               key="xp_range")
        if isinstance(picked, tuple) and len(picked) == 2:
            d_from, d_to = picked
    fmt = st.radio(t("export.ui.format"), list(service.RAW_FORMATS), key="xp_fmt",
                   format_func=lambda f: t(f"export.ui.fmt_{f}"))

    sel = tabular.Selection(tables=tables, persons=persons, date_from=d_from, date_to=d_to)
    if tables:
        counts = tabular.count_rows(con, reg, sel)
        total = sum(counts.values())
        st.caption(t("export.ui.estimate") + ": " + " · ".join(
            t("export.ui.rows_line", table=reg.tables[k].label_hu, rows=q.fmt_num(n))
            for k, n in counts.items()))
        if fmt == "xlsx" and any(n > tabular.XLSX_MAX_ROWS for n in counts.values()):
            st.warning(t("export.warn.over_limit", limit=q.fmt_num(tabular.XLSX_MAX_ROWS)))
        elif fmt == "xlsx" and total > LARGE_ROWS:
            st.warning(t("export.warn.large", rows=q.fmt_num(total)))

    if st.button(t("export.ui.create"), type="primary", key="xp_create", disabled=not tables):
        try:
            with st.spinner(t("export.ui.creating")):
                out = service.export_raw(con, cfg, sel, fmt)
            ss["xp_last"] = str(out.path)
            ss["xp_last_rows"] = out.rows
            ss["xp_last_warn"] = out.warnings
        except ValueError as exc:
            st.error(str(exc))
    if ss.get("xp_last"):
        st.success(t("export.ui.done", path=ss["xp_last"], rows=q.fmt_num(ss["xp_last_rows"])))
        for w in ss.get("xp_last_warn") or []:
            st.warning(w)
        download(Path(ss["xp_last"]), "xp_dl_last")

with tab_hist:
    hist = service.list_history(con)
    if hist.empty:
        st.caption(t("export.ui.history_empty"))
    else:
        view = hist[["created_at", "kind", "fmt", "title", "row_count", "size_bytes"]].copy()
        view["created_at"] = view["created_at"].map(q.fmt_datetime)
        view["kind"] = view["kind"].map(lambda k: t(f"export.kind.{k}"))
        view["size_bytes"] = (view["size_bytes"] / 1024).map(lambda v: q.fmt_num(v, 0) + " kB")
        view.columns = [t(f"export.col.{c}") for c in view.columns]
        st.dataframe(view, hide_index=True, width="stretch")
        labels = {r.export_id: f"{q.fmt_datetime(r.created_at)} · {r.fmt.upper()} · {r.title}"
                  for r in hist.itertuples()}
        eid = st.selectbox(t("export.ui.select"), list(labels), format_func=labels.get,
                           key="xp_hist_sel")
        row = hist.set_index("export_id").loc[eid]
        with st.expander(t("export.ui.params")):
            st.code(row["params"], language="json")
        if row["warnings"]:
            st.warning(row["warnings"])
        download(Path(row["file_path"]), "xp_dl_hist")
        c1, c2 = st.columns(2)
        if c1.button(t("export.ui.regenerate"), key="xp_regen"):
            with st.spinner(t("export.ui.creating")):
                out = service.regenerate(con, cfg, eid)
            ss["xp_regen_path"] = str(out.path)
            st.rerun()
        if c2.button(t("export.ui.delete"), key="xp_delete"):
            service.delete_history(con, eid)
            ss["xp_flash_deleted"] = True
            st.rerun()
        if ss.get("xp_regen_path"):
            st.success(t("export.ui.regenerated", path=ss["xp_regen_path"]))
            download(Path(ss["xp_regen_path"]), "xp_dl_regen")
        if ss.pop("xp_flash_deleted", None):
            st.success(t("export.ui.deleted"))
disclaimer()
