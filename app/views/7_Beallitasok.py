"""Beállítások – general, people/profile, primary source, report thresholds, DB (SPEC 6.3)."""

from __future__ import annotations

from datetime import date
from zoneinfo import available_timezones

import pandas as pd
import streamlit as st
import yaml

from vitalmart.datamart import db
from vitalmart.i18n import t
from vitalmart.settings import load_settings, rules_parameters, save_settings
from vitalmart.transform.rebuild import rebuild
from vitalmart.ui import queries as q
from vitalmart.ui.common import close_connection, get_cfg, get_con

cfg = get_cfg()
con = get_con(cfg)
st.title(t("ui.settings.title"))


def _rebuild() -> None:
    new_cfg = get_cfg()
    secs = rebuild(get_con(new_cfg), new_cfg)
    st.success(t("ui.settings.rebuilt", seconds=q.fmt_num(secs, 1)))


tab_g, tab_p, tab_s, tab_r, tab_d = st.tabs([
    t("ui.settings.tab_general"), t("ui.settings.tab_people"), t("ui.settings.tab_sources"),
    t("ui.settings.tab_rules"), t("ui.settings.tab_db")])

# ---------------------------------------------------------------- general
with tab_g, st.form("general"):
    zones = sorted(available_timezones())
    tz = st.selectbox(t("ui.settings.timezone"), zones, index=zones.index(cfg.timezone),
                      key="set_tz")
    lang = st.selectbox(t("ui.settings.language"), ["hu", "en"],
                        index=["hu", "en"].index(cfg.language), key="set_lang",
                        format_func=lambda x: t(f"lang_name.{x}"))
    conv = st.radio(t("ui.settings.sleep_convention"), ["wake", "start"], horizontal=True,
                    index=["wake", "start"].index(cfg.sleep.day_convention),
                    format_func=lambda c: t(f"ui.settings.conv_{c}"), key="set_conv")
    c1, c2, c3 = st.columns(3)
    goal = c1.number_input(t("ui.settings.sleep_goal"), 4.0, 12.0, float(cfg.sleep.goal_hours),
                           0.25, key="set_goal")
    gap = c2.number_input(t("ui.settings.session_gap"), 10, 600,
                          int(cfg.sleep.session_gap_minutes), 10, key="set_gap")
    mx = c3.number_input(t("ui.settings.max_interval"), 4.0, 48.0,
                         float(cfg.sleep.max_interval_hours), 1.0, key="set_maxint")
    c4, c5 = st.columns(2)
    blw = c4.number_input(t("ui.settings.baseline_window"), 14, 365,
                          int(cfg.derived.baseline_window_days), 1, key="set_blw")
    blm = c5.number_input(t("ui.settings.baseline_min"), 2, 365,
                          int(cfg.derived.baseline_min_days), 1, key="set_blm")
    if st.form_submit_button(t("ui.settings.save_rebuild"), type="primary"):
        save_settings(cfg.data_path, {
            "timezone": tz, "language": lang,
            "sleep": {"day_convention": conv, "goal_hours": float(goal),
                      "session_gap_minutes": int(gap), "max_interval_hours": float(mx)},
            "derived": {"baseline_window_days": int(blw), "baseline_min_days": int(blm)},
        })
        st.success(t("ui.settings.saved"))
        _rebuild()

# ---------------------------------------------------------------- people / profile
with tab_p:
    people = q.persons(con)
    if not people:
        st.caption(t("ui.quality.none"))
    for key, name in people:
        with st.expander(f"{name} ({key})", expanded=len(people) == 1), st.form(f"prof_{key}"):
            prof = q.current_profile(con, key) or {}
            disp = st.text_input(t("ui.settings.person_name"),
                                 value="" if name == key else name, key=f"name_{key}")
            st.caption(t("ui.settings.profile"))
            c1, c2, c3 = st.columns(3)
            by = c1.number_input(t("ui.settings.birth_year"), 1900, date.today().year,
                                 value=int(prof["birth_year"]) if prof.get("birth_year") else None,
                                 key=f"by_{key}")
            sexes = ["", "female", "male", "other"]
            sex = c2.selectbox(t("ui.settings.sex"), sexes,
                               index=sexes.index(prof.get("sex") or ""), key=f"sex_{key}",
                               format_func=lambda s: t(f"ui.settings.sex_{s or 'none'}"))
            height = c3.number_input(t("ui.settings.height"), 80.0, 250.0,
                                     value=float(prof["height_cm"]) if prof.get("height_cm")
                                     else None, key=f"h_{key}")
            c4, c5 = st.columns(2)
            mhr = c4.number_input(t("ui.settings.max_hr"), 100.0, 240.0,
                                  value=float(prof["max_hr"]) if prof.get("max_hr") else None,
                                  key=f"mhr_{key}")
            srcs = ["measured", "estimated"]
            mhs = c5.selectbox(t("ui.settings.max_hr_source"), srcs,
                               index=srcs.index(prof.get("max_hr_source") or "estimated"),
                               format_func=lambda s: t(f"ui.settings.{s}"), key=f"mhs_{key}")
            st.caption(t("ui.settings.zones") + " – " + t("ui.settings.zones_default"))
            default_z = prof.get("zone_bounds") or (q.zone_bounds(mhr) if mhr else [0] * 5)
            zc = st.columns(5)
            zones_in = [zc[i].number_input(f"{i + 1}.", 0, 240, int(default_z[i]),
                                           key=f"z{i}_{key}") for i in range(5)]
            if st.form_submit_button(t("ui.settings.save"), type="primary"):
                q.set_display_name(con, key, disp)
                zones_out = zones_in if any(zones_in) else (q.zone_bounds(mhr) if mhr else None)
                q.save_profile(con, key, birth_year=int(by) if by else None, sex=sex or None,
                               height_cm=height, max_hr=mhr, max_hr_source=mhs if mhr else None,
                               zones=zones_out)
                st.success(t("ui.settings.profile_saved", date=q.fmt_date(date.today())))

# ---------------------------------------------------------------- primary source
with tab_s:
    st.caption(t("ui.settings.priority_help"))
    all_types = ["watch", "phone", "summary", "app", "other"]
    prio = st.multiselect(t("ui.settings.priority"), all_types,
                          default=[x for x in cfg.derived.source_priority if x in all_types],
                          key="set_prio")
    ms = q.multi_source_metrics(con)
    overrides = dict(cfg.derived.primary_source)
    new_over: dict[str, str] = {}
    st.subheader(t("ui.settings.override"))
    if ms.empty:
        st.caption(t("ui.settings.override_none"))
    for row in ms.itertuples():
        opts = ["", *list(row.sources)]
        cur = overrides.get(row.metric_key, "")
        sel = st.selectbox(f"{q.metric_label(con, row.metric_key)} ({row.metric_key})", opts,
                           index=opts.index(cur) if cur in opts else 0,
                           format_func=lambda s: s or t("ui.settings.override_auto"),
                           key=f"ovr_{row.metric_key}")
        if sel:
            new_over[row.metric_key] = sel
    if st.button(t("ui.settings.save_rebuild"), type="primary", key="save_sources"):
        stored = load_settings(cfg.data_path)
        stored.setdefault("derived", {})["primary_source"] = new_over
        stored["derived"]["source_priority"] = prio or all_types
        # primary_source must be REPLACED, not merged (removing an override must work)
        save_settings(cfg.data_path, {"derived": {"primary_source": None}})
        save_settings(cfg.data_path, {"derived": stored["derived"]})
        st.success(t("ui.settings.saved"))
        _rebuild()

# ---------------------------------------------------------------- report thresholds
with tab_r:
    st.warning(t("ui.settings.rules_note"))
    with (cfg.config_dir / "rules.yaml").open(encoding="utf-8") as fh:
        meta = yaml.safe_load(fh) or {}
    if meta.get("reviewed_by"):
        st.caption(t("ui.settings.rules_reviewed", by=meta["reviewed_by"],
                     at=meta.get("reviewed_at") or "–"))
    else:
        st.caption(t("ui.settings.rules_not_reviewed"))
    params = rules_parameters(cfg.config_dir, cfg.data_path)
    df = pd.DataFrame([{"key": k, "label": p.get("label_hu", k), "value": float(p["value"]),
                        "default": float(p["default"]), "unit": p.get("unit", "")}
                       for k, p in params.items()])
    edited = st.data_editor(df, hide_index=True, width="stretch", key="rules_editor",
                            disabled=["key", "label", "default", "unit"],
                            column_config={c: t(f"col.{c}") for c in df.columns})
    c1, c2 = st.columns(2)
    if c1.button(t("ui.settings.save"), type="primary", key="save_rules"):
        changed = {r.key: r.value for r in edited.itertuples() if r.value != r.default}
        save_settings(cfg.data_path, {"rules_parameters": None})
        if changed:
            save_settings(cfg.data_path, {"rules_parameters": changed})
        st.success(t("ui.settings.saved"))
    if c2.button(t("ui.settings.reset_default"), key="reset_rules"):
        save_settings(cfg.data_path, {"rules_parameters": None})
        st.success(t("ui.settings.saved"))

# ---------------------------------------------------------------- database
with tab_d:
    st.write(t("ui.settings.db_path", path=cfg.db_path,
               size=q.fmt_num(q.db_size_mb(cfg.db_path), 1)))
    c1, c2 = st.columns(2)
    if c1.button(t("ui.settings.backup"), key="db_backup"):
        target = db.backup(cfg, con)
        st.success(t("ui.settings.backup_done", path=target))
    if c2.button(t("ui.settings.rebuild"), key="db_rebuild"):
        _rebuild()
    st.divider()
    st.subheader(t("ui.settings.wipe_title"))
    st.error(t("ui.settings.wipe_warning"))
    confirm = st.text_input(t("ui.settings.wipe_confirm"), key="wipe_confirm")
    if st.button(t("ui.settings.wipe_btn"), type="primary", disabled=confirm != "TÖRLÉS",
                 key="wipe_btn"):
        close_connection(cfg)
        db.wipe(cfg)
        for k in list(st.session_state):
            del st.session_state[k]
        st.success(t("ui.settings.wiped"))
