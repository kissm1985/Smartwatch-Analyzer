"""Import – upload or local path, detection, preview, confirmation, summary (SPEC 4.2)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from vitalmart.i18n import t
from vitalmart.importers.base import ImportFailure
from vitalmart.importers.service import PERSON_RE, preview, run_import
from vitalmart.ui import queries as q
from vitalmart.ui.common import get_cfg, get_con

cfg = get_cfg()
con = get_con(cfg)
st.title(t("ui.import.title"))
st.caption(t("ui.import.intro"))
st.caption("ℹ️ " + t("ui.import.lock_hint"))

mode = st.radio(t("ui.import.mode"), ["path", "upload"], horizontal=True,
                format_func=lambda m: t(f"ui.import.mode_{m}"), key="imp_mode")
src_path: Path | None = None
if mode == "upload":
    up = st.file_uploader(t("ui.import.upload"), type=["zip", "xml", "fit"], key="imp_upload")
    if up is not None:
        target_dir = cfg.data_path / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        src_path = target_dir / Path(up.name).name
        if not src_path.exists() or src_path.stat().st_size != up.size:
            with src_path.open("wb") as fh:
                shutil.copyfileobj(up, fh, length=1 << 20)
else:
    raw = st.text_input(t("ui.import.path"), help=t("ui.import.path_help"), key="imp_path")
    if raw.strip():
        src_path = Path(raw.strip()).expanduser()

people = [k for k, _ in q.persons(con)]
options = [*sorted(set(people) | {cfg.default_person}), "__new__"]
c1, c2 = st.columns(2)
with c1:
    choice = st.selectbox(t("ui.import.person"), options, key="imp_person",
                          format_func=lambda k: "➕ " + t("ui.import.person_new")
                          if k == "__new__" else k, help=t("ui.import.person_help"))
    person = (st.text_input(t("ui.import.person_new"), key="imp_person_new").strip()
              if choice == "__new__" else choice)
with c2:
    adapter = st.selectbox(t("ui.import.adapter"), ["", "apple_health", "garmin"],
                           format_func=lambda a: a or t("ui.import.adapter_auto"),
                           key="imp_adapter")
force = st.checkbox(t("ui.import.force"), key="imp_force")


def _fail(exc: ImportFailure) -> None:
    msg = t(exc.i18n_key, **exc.kwargs)
    st.error(f"{t('ui.import.failed')}: {msg}" if exc.i18n_key != "import.failed" else msg)


if st.button(t("ui.import.preview_btn"), type="secondary", key="imp_preview_btn"):
    st.session_state.pop("imp_result", None)
    if src_path is None:
        st.warning(t("ui.import.need_input"))
    else:
        bar = st.progress(0.0, text=t("ui.import.previewing"))
        try:
            pv = preview(src_path, adapter or None, lambda f: bar.progress(min(f, 1.0)))
            st.session_state["imp_preview"] = {"path": str(src_path), "pv": pv}
        except ImportFailure as exc:
            st.session_state.pop("imp_preview", None)
            _fail(exc)
        bar.empty()

pvs = st.session_state.get("imp_preview")
if pvs and src_path is not None and pvs["path"] == str(src_path):
    pv = pvs["pv"]
    st.subheader(t("ui.import.preview_title") + f" – {pv.discovery.adapter}")
    for w in pv.discovery.warnings:
        st.warning(w)
    st.write(t("ui.import.range", date_from=pv.date_from or "–", date_to=pv.date_to or "–"))
    st.write(t("ui.import.total", n=q.fmt_num(pv.total_records)))
    a, b = st.columns(2)
    with a:
        st.caption(t("ui.import.types"))
        st.dataframe(q.labeled(pd.DataFrame(sorted(pv.type_counts.items(), key=lambda kv: -kv[1]),
                                            columns=["type", "n"])),
                     hide_index=True, width="stretch")
    with b:
        st.caption(t("ui.import.files"))
        st.dataframe(q.labeled(pd.DataFrame(
            [(f.kind, f.path, f.size_bytes) for f in pv.discovery.files],
            columns=["kind", "path", "bytes"])), hide_index=True, width="stretch")

    if st.button(t("ui.import.confirm_btn"), type="primary", key="imp_confirm_btn"):
        if not PERSON_RE.match(person or ""):
            st.error(t("import.invalid_person", person=person))
        else:
            bar = st.progress(0.0, text=t("ui.import.loading"))
            try:
                res = run_import(con, cfg, src_path, force=force, adapter_name=adapter or None,
                                 progress=lambda f: bar.progress(min(f, 1.0)), person=person)
                st.session_state["imp_result"] = res
            except ImportFailure as exc:
                _fail(exc)
            finally:
                bar.empty()
                if mode == "upload" and src_path.is_relative_to(cfg.data_path / "uploads"):
                    src_path.unlink(missing_ok=True)   # no lingering copies of health data
            st.session_state.pop("imp_preview", None)

res = st.session_state.get("imp_result")
if res is not None:
    if res.status == "success":
        st.success(t("ui.import.done"))
        cols = st.columns(5)
        for col, key, val in zip(cols, ("read", "new", "updated", "skipped", "errors"),
                                 (res.counts.read, res.counts.new, res.counts.updated,
                                  res.counts.skipped, res.counts.errors), strict=True):
            col.metric(t(f"ui.import.summary_{key}"), q.fmt_num(val))
        st.write(t("ui.import.range", date_from=q.fmt_date(res.date_from),
                   date_to=q.fmt_date(res.date_to)))
    else:
        st.warning(f"{t('ui.import.skipped')}: {res.message}")

st.subheader(t("ui.import.log"))
st.dataframe(q.labeled(q.import_log(con)), hide_index=True, width="stretch")
