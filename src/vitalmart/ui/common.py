"""Streamlit helpers shared by the pages (config, DB connection, person picker …)."""

from __future__ import annotations

import contextlib
import ipaddress

import duckdb
import streamlit as st

from vitalmart.config import AppConfig, load_config
from vitalmart.datamart import db
from vitalmart.i18n import set_language, t
from vitalmart.ui import queries as q


def get_cfg() -> AppConfig:
    cfg = load_config()
    set_language(cfg.language)
    return cfg


@st.cache_resource(show_spinner=False)
def _connection(db_path: str, _cfg: AppConfig) -> duckdb.DuckDBPyConnection:
    return db.connect(_cfg)


def get_con(cfg: AppConfig) -> duckdb.DuckDBPyConnection:
    """A cursor on the process-wide connection (one DuckDB writer per process)."""
    return _connection(str(cfg.db_path), cfg).cursor()


def close_connection(cfg: AppConfig) -> None:
    """Close and forget the cached connection (before wiping the database)."""
    with contextlib.suppress(duckdb.Error):
        _connection(str(cfg.db_path), cfg).close()
    _connection.clear()


def guard_localhost() -> None:
    """SPEC 10.1: refuse to serve on anything but a loopback address."""
    addr = st.get_option("server.address") or "127.0.0.1"
    try:
        ok = addr == "localhost" or ipaddress.ip_address(addr).is_loopback
    except ValueError:
        ok = False
    if not ok:
        st.error(t("ui.not_localhost", address=addr))
        st.stop()


def person_picker(con: duckdb.DuckDBPyConnection, key: str = "person") -> str | None:
    people = q.persons(con)
    if not people:
        return None
    if len(people) == 1:
        st.caption(t("ui.person_single", name=people[0][1]))
        return people[0][0]
    labels = {k: n for k, n in people}
    default = st.session_state.get("person_global")
    keys = list(labels)
    idx = keys.index(default) if default in keys else 0
    choice = st.selectbox(t("ui.person"), keys, index=idx, format_func=lambda k: labels[k],
                          key=key)
    st.session_state["person_global"] = choice
    return choice


def empty_state() -> None:
    st.info(t("ui.empty.title"))
    with st.expander(t("ui.empty.apple_title"), expanded=True):
        st.markdown(t("ui.empty.apple_steps"))
    with st.expander(t("ui.empty.garmin_title"), expanded=True):
        st.markdown(t("ui.empty.garmin_steps"))
    with contextlib.suppress(Exception):  # page links need the multipage app (main.py)
        st.page_link("pages/2_Import.py", label=t("ui.empty.goto_import"), icon="📥")


def disclaimer() -> None:
    st.caption("⚠️ " + t("app.disclaimer"))
