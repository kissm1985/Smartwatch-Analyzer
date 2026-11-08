"""M4: Streamlit UI (headless AppTest) – import from the UI with preview and summary,
overview, data quality, settings (overlay, profile SCD2, thresholds), wipe."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from tests.conftest import rec
from vitalmart.config import load_config
from vitalmart.datamart import db
from vitalmart.importers.service import run_import
from vitalmart.settings import load_settings, rules_parameters, save_settings
from vitalmart.ui import queries as q

APP = Path(__file__).resolve().parents[1] / "app"
T = 120


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "data"
    monkeypatch.setenv("VITALMART_DATA_DIR", str(d))
    return d


def page(name: str) -> AppTest:
    return AppTest.from_file(str(APP / "views" / name), default_timeout=T)


def _texts(at: AppTest) -> str:
    parts = [e.value for e in (*at.markdown, *at.caption, *at.info, *at.success, *at.warning,
                                *at.error, *at.title, *at.subheader)]
    return "\n".join(str(p) for p in parts)


def _db(data_dir: Path):
    cfg = load_config(data_dir=str(data_dir))
    return cfg, db.connect(cfg, read_only=False)


def test_overview_empty_state_shows_export_guides(data_dir):
    at = page("1_Attekintes.py").run()
    assert not at.exception
    txt = _texts(at)
    assert "Még nincs betöltött adat" in txt and "Összes egészségügyi adat exportálása" in txt
    assert "Adatok exportálása" in txt


def test_full_import_from_ui_with_preview_and_summary(data_dir, sample_zip):
    at = page("2_Import.py").run()
    assert not at.exception
    at.text_input(key="imp_path").input(str(sample_zip))
    at.selectbox(key="imp_person").select("me")
    at.button(key="imp_preview_btn").click().run()
    assert not at.exception
    txt = _texts(at)
    assert "Előnézet – apple_health" in txt and "Dátumtartomány: 2024-03-19" in txt
    assert any("export_cda.xml" in w.value for w in at.warning)          # ignored file noted
    at.button(key="imp_confirm_btn").click().run()
    assert not at.exception, at.exception
    assert any("sikeres" in s.value for s in at.success)
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics["Új"].replace(" ", "")) > 1000
    assert metrics["Hibás"] == "0"
    # second time: duplicate file is skipped with a clear message
    at.button(key="imp_preview_btn").click().run()
    at.button(key="imp_confirm_btn").click().run()
    assert any("már betöltötted" in w.value for w in at.warning)
    log = at.dataframe[-1].value
    assert list(log["Állapot"]) == ["kihagyva (már betöltve)", "sikeres"]


def test_import_errors_are_friendly(data_dir, tmp_path):
    bad = tmp_path / "nope.zip"
    at = page("2_Import.py").run()
    at.text_input(key="imp_path").input(str(bad))
    at.button(key="imp_preview_btn").click().run()
    assert not at.exception
    assert any("nem létezik" in e.value for e in at.error)
    at2 = page("2_Import.py").run()
    at2.button(key="imp_preview_btn").click().run()
    assert any("Válassz fájlt" in w.value for w in at2.warning)


def test_overview_and_quality_with_data(data_dir, sample_zip):
    cfg, con = _db(data_dir)
    run_import(con, cfg, sample_zip, person="partner")
    con.close()
    from vitalmart.ui.common import _connection

    _connection.clear()
    at = page("1_Attekintes.py").run()
    assert not at.exception, at.exception
    labels = [m.label for m in at.metric]
    assert {"Nyugalmi pulzus", "Alvásidő", "Lépésszám"} <= set(labels)
    assert any("Utolsó adat: 2024.04.08." in c.value for c in at.caption)
    at.segmented_control(key="ov_days").set_value(7).run()
    assert not at.exception
    dq = page("5_Adatminoseg.py").run()
    assert not dq.exception, dq.exception
    assert {m.label for m in dq.metric} >= {"Források", "Hézagok", "Megjelölt érték"}
    assert len(dq.tabs) == 5


def test_settings_general_save_writes_overlay_and_rebuilds(data_dir, sample_zip):
    cfg, con = _db(data_dir)
    run_import(con, cfg, sample_zip)
    con.close()
    from vitalmart.ui.common import _connection

    _connection.clear()
    at = page("7_Beallitasok.py").run()
    assert not at.exception, at.exception
    at.number_input(key="set_goal").set_value(8.0)
    at.radio(key="set_conv").set_value("start")
    at.button[0].click().run()                     # form submit: save + recompute
    assert not at.exception, at.exception
    assert any("Újraszámolva" in s.value for s in at.success)
    stored = load_settings(data_dir)
    assert stored["sleep"]["goal_hours"] == 8.0 and stored["sleep"]["day_convention"] == "start"
    assert load_config().sleep.goal_hours == 8.0      # overlay is applied
    assert load_config(use_user_settings=False).sleep.goal_hours == 7.5


def test_settings_overlay_reset_and_rules_parameters(data_dir):
    save_settings(data_dir, {"rules_parameters": {"sleep_min_hours": 6.5}})
    cfg = load_config()
    p = rules_parameters(cfg.config_dir, data_dir)
    assert p["sleep_min_hours"]["value"] == 6.5 and p["sleep_min_hours"]["default"] == 7.0
    save_settings(data_dir, {"rules_parameters": None})          # None = reset
    assert "rules_parameters" not in load_settings(data_dir)
    with pytest.raises(ValueError):
        save_settings(data_dir, {"data_dir": "/elsewhere"})


def test_profile_scd2(con):
    con.execute("INSERT INTO dim_person (person_key) VALUES ('me')")
    d0 = date(2026, 1, 1)
    q.save_profile(con, "me", birth_year=1985, sex="male", height_cm=180.0, max_hr=185.0,
                   max_hr_source="estimated", zones=q.zone_bounds(185), today=d0)
    q.save_profile(con, "me", birth_year=1985, sex="male", height_cm=180.0, max_hr=190.0,
                   max_hr_source="measured", zones=None, today=d0 + timedelta(days=30))
    q.save_profile(con, "me", birth_year=1985, sex="male", height_cm=181.0, max_hr=190.0,
                   max_hr_source="measured", zones=None, today=d0 + timedelta(days=30))
    rows = con.execute("SELECT valid_from, valid_to, max_hr, height_cm FROM dim_user_profile "
                       "ORDER BY valid_from").fetchall()
    assert rows == [(d0, d0 + timedelta(days=29), 185.0, 180.0),
                    (d0 + timedelta(days=30), None, 190.0, 181.0)]
    cur = q.current_profile(con, "me")
    assert cur["max_hr"] == 190 and cur["zone_bounds"] is None
    assert q.zone_bounds(185) == [92, 111, 130, 148, 166]


def test_settings_profile_form_and_display_name(data_dir, sample_zip):
    cfg, con = _db(data_dir)
    run_import(con, cfg, sample_zip, person="partner")
    con.close()
    from vitalmart.ui.common import _connection

    _connection.clear()
    at = page("7_Beallitasok.py").run()
    at.text_input(key="name_partner").input("Teszt Név")
    at.number_input(key="mhr_partner").set_value(180.0)
    submit = next(b for b in at.button if b.label == "Mentés")
    submit.click().run()
    assert not at.exception, at.exception
    assert any("Profil elmentve" in s.value for s in at.success)
    _connection.clear()
    cfg, con = _db(data_dir)
    assert con.execute("SELECT display_name FROM dim_person").fetchall() == [("Teszt Név",)]
    assert q.current_profile(con, "partner")["zone_bounds"] == [90, 108, 126, 144, 162]
    con.close()


def test_wipe_from_settings(data_dir, sample_zip):
    cfg, con = _db(data_dir)
    run_import(con, cfg, sample_zip)
    con.close()
    save_settings(data_dir, {"sleep": {"goal_hours": 8.0}})
    from vitalmart.ui.common import _connection

    _connection.clear()
    at = page("7_Beallitasok.py").run()
    assert at.button(key="wipe_btn").disabled
    at.text_input(key="wipe_confirm").input("TÖRLÉS").run()
    at.button(key="wipe_btn").click().run()
    assert not at.exception, at.exception
    assert any("Minden adat törölve" in s.value for s in at.success)
    assert not (data_dir / "vitalmart.duckdb").exists()
    assert not (data_dir / "settings.yaml").exists()


def test_localhost_guard():
    at = AppTest.from_string(
        "from vitalmart.ui.common import guard_localhost\nguard_localhost()\n"
        "import streamlit as st\nst.write('served')")
    at.run()
    assert not at.error
    at2 = AppTest.from_string(
        "import streamlit as st\nfrom streamlit import config\n"
        "config.set_option('server.address', '0.0.0.0')\n"
        "from vitalmart.ui.common import guard_localhost\nguard_localhost()\nst.write('served')")
    at2.run()
    assert at2.error and "127.0.0.1" in at2.error[0].value
    from streamlit import config

    config.set_option("server.address", "127.0.0.1")


def test_streamlit_config_is_local_and_private():
    conf = (Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml").read_text()
    assert 'address = "127.0.0.1"' in conf and "gatherUsageStats = false" in conf


def test_formatting_helpers():
    assert q.fmt_num(1234567.891, 2) == "1 234 567,89"
    assert q.fmt_num(None) == "–"
    assert q.fmt_date(date(2024, 3, 5)) == "2024.03.05."
    assert q.fmt_hm(445) == "7 ó 25 p"


def test_overview_cards_baseline(con, cfg, write_xml):
    els = [rec("RestingHeartRate", (date(2024, 1, 1) + timedelta(days=i)).strftime(
        "%Y-%m-%d 08:00:00 +0100"), value=60 if i < 60 else 66, unit="count/min")
        for i in range(67)]
    run_import(con, cfg, write_xml(*els))
    cards = {c.metric_key: c for c in q.overview_cards(con, "me", 7)}
    c = cards["resting_hr"]
    assert c.value == 66 and c.baseline == 60 and c.delta == 6 and c.n_days == 7
    assert c.delta_pct == pytest.approx(10.0)


def test_rules_yaml_has_review_fields():
    rules = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "rules.yaml")
                           .read_text(encoding="utf-8"))
    assert "reviewed_by" in rules and "reviewed_at" in rules
    assert rules["parameters"]["who_moderate_min_week"]["value"] == 150
