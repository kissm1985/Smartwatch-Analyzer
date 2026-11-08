"""M5: Explorer page (headless AppTest)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import SMALL_SPEC
from vitalmart.config import load_config
from vitalmart.datamart import db
from vitalmart.importers.service import run_import
from vitalmart.sample_data import write_export_zip
from vitalmart.semantic import store

PAGE = Path(__file__).resolve().parents[1] / "app" / "views" / "4_Elemzo.py"


@pytest.fixture
def at(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("VITALMART_DATA_DIR", str(d))
    cfg = load_config()
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(tmp_path / "s", SMALL_SPEC), person="partner")
    con.close()
    from vitalmart.ui.common import _connection

    _connection.clear()
    app = AppTest.from_file(str(PAGE), default_timeout=120)
    app.run()
    assert not app.exception, app.exception
    yield app


def _sql(app: AppTest) -> str:
    return next(c.value for c in app.code)


def test_default_analysis_renders_with_sql_and_table(at):
    assert "derived_person_daily" in _sql(at)
    at.selectbox(key="ex_period").select("last_30").run()
    assert "?" in _sql(at)                          # user values are parameters
    assert len(at.get("plotly_chart")) == 1
    assert any("sor · időszak" in c.value for c in at.caption)
    assert len(at.dataframe) == 1


def test_open_builtin_and_change_settings(at):
    at.selectbox(key="ex_saved_sel").select("builtin:sleep_stages_monthly")
    at.button(key="ex_open").click().run()
    assert not at.exception, at.exception
    assert at.selectbox(key="ex_topic").value == "sleep"
    assert "fact_sleep_session" in _sql(at)
    at.selectbox(key="ex_chart").select("table").run()
    assert not at.exception
    assert len(at.get("plotly_chart")) == 0
    at.selectbox(key="ex_transform").select("cumsum").run()
    assert not at.exception


def test_invalid_combination_shows_friendly_message(at):
    at.selectbox(key="ex_chart").select("corr").run()
    assert any("legalább két mérőszám" in w.value for w in at.warning)


def test_save_update_duplicate_delete(at, tmp_path):
    at.text_input(key="ex_name").input("Saját lépés")
    at.text_input(key="ex_tags").input("saját, teszt")
    at.button(key="ex_save_new").click().run()
    assert not at.exception, at.exception
    assert any("Elmentve" in s.value for s in at.success)
    loaded = at.session_state["ex_loaded"]
    assert loaded.startswith("user:")
    from vitalmart.ui.common import _connection

    cfg = load_config()
    con = _connection(str(cfg.db_path), cfg).cursor()
    a = store.get(con, loaded)
    assert a.name == "Saját lépés" and a.tags == ["saját", "teszt"]
    at.selectbox(key="ex_saved_sel").select(loaded)
    at.button(key="ex_del").click().run()
    assert not at.exception
    with pytest.raises(store.StoreError):
        store.get(con, loaded)
    at.selectbox(key="ex_saved_sel").select("builtin:hr_heatmap")
    at.button(key="ex_del").click().run()
    assert any("beépített" in e.value for e in at.error)
