"""Apple external devices get their own source; sources can be excluded from analysis.

Real case: a Bluetooth heart-rate band paired with the iPhone saves its samples under the
iPhone's sourceName (device = only a firmware-style 'hardware:' value), so they looked like
iPhone data; and the user wants to leave an old band out of the analysis without deleting it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import DEV, rec, scalar
from vitalmart.config import load_config
from vitalmart.datamart import db
from vitalmart.importers.service import run_import
from vitalmart.semantic.engine import run
from vitalmart.semantic.spec import AnalysisSpec
from vitalmart.settings import load_settings, save_settings
from vitalmart.transform.rebuild import rebuild_core

PHONE = "<<HKDevice: 0x1>, name:iPhone, manufacturer:Apple Inc., model:iPhone, hardware:iPhone14,5, software:17.0>"
BAND_VIA_PHONE = "<<HKDevice: 0x2>, hardware:V0.59.17.18>"
MI_BAND = "<<HKDevice: 0x3>, name:Mi Smart Band 5, manufacturer:Xiaomi, model:XMSH10HM>"
AIRPODS = "<<HKDevice: 0x4>, name:AirPods Pro, manufacturer:Apple Inc., model:0x200E>"


def _records() -> list[str]:
    out = []
    for d in range(1, 11):
        day = f"2024-05-{d:02d}"
        out.append(rec("StepCount", f"{day} 10:00:00 +0200", f"{day} 11:00:00 +0200",
                       5000 + d, "count", source="Kata iPhone", device=PHONE))
        out.append(rec("HeartRate", f"{day} 12:00:00 +0200", value=70 + d, unit="count/min",
                       source="Kata iPhone", device=BAND_VIA_PHONE))
        out.append(rec("HeartRate", f"{day} 13:00:00 +0200", value=80, unit="count/min",
                       source="Mi Fit", device=MI_BAND))
        out.append(rec("HeadphoneAudioExposure", f"{day} 14:00:00 +0200",
                       f"{day} 14:30:00 +0200", 60, "dBASPL", source="Kata iPhone",
                       device=AIRPODS))
        out.append(rec("HeartRate", f"{day} 15:00:00 +0200", value=60, unit="count/min",
                       source="Kata Watch", device=DEV))
        out.append(rec("RestingHeartRate", f"{day} 07:00:00 +0200", value=55,
                       unit="count/min", source="Kata Watch", device=DEV))
    return out


@pytest.fixture
def loaded(cfg, con, write_xml):
    run_import(con, cfg, write_xml(*_records()), person="partner")
    return cfg, con


def test_external_devices_get_their_own_source(loaded):
    _cfg, con = loaded
    src = dict(con.execute("SELECT source_key, device_type FROM dim_source "
                           "WHERE vendor = 'Apple'").fetchall())
    assert src == {
        "apple:Kata iPhone": "phone",
        "apple:Kata iPhone › külső eszköz": "other",
        "apple:Kata iPhone › AirPods Pro": "other",
        "apple:Mi Fit › Mi Smart Band 5": "other",
        "apple:Kata Watch": "watch",
    }
    # heart rate is never attributed to the phone itself
    by_src = dict(con.execute("""SELECT source_key, count(*) FROM fact_heart_rate_sample
                                 GROUP BY 1""").fetchall())
    assert "apple:Kata iPhone" not in by_src
    assert by_src["apple:Kata iPhone › külső eszköz"] == 10
    assert by_src["apple:Kata Watch"] == 10
    # steps still come from the phone; the display name says which device
    assert scalar(con, """SELECT count(*) FROM derived_person_daily
                          WHERE metric_key = 'steps' AND source_key = 'apple:Kata iPhone'""") == 10
    assert scalar(con, """SELECT source_name FROM dim_source
                          WHERE source_key = 'apple:Kata iPhone › külső eszköz'""") \
        == "Kata iPhone › külső eszköz"
    # all of them belong to the importing person
    assert con.execute("SELECT DISTINCT person_key FROM dim_source").fetchall() == [("partner",)]


def test_reimport_keeps_split_sources_stable(loaded, write_xml):
    cfg, con = loaded
    before = sorted(con.execute("SELECT source_key FROM dim_source").fetchall())
    run_import(con, cfg, write_xml(*_records(), name="again.xml"), person="partner")
    assert sorted(con.execute("SELECT source_key FROM dim_source").fetchall()) == before
    assert scalar(con, "SELECT count(*) FROM fact_heart_rate_sample") == 30


def test_excluded_source_is_left_out_of_analysis_but_kept(loaded):
    cfg, con = loaded
    band = "apple:Kata iPhone › külső eszköz"
    hr_spec = AnalysisSpec.model_validate({"topic": "heart_rate", "measures": [{"key": "bpm",
                                           "agg": "count"}], "time_grain": "none",
                                           "period": "all", "dimensions": ["source.source"]})
    before = dict(run(con, hr_spec).df[["d0", "m0"]].itertuples(index=False))
    assert before["Kata iPhone › külső eszköz"] == 10

    save_settings(cfg.data_path, {"derived": {"excluded_sources": [band]}})
    cfg2 = load_config(data_dir=str(cfg.data_path), use_user_settings=True)
    rebuild_core(con, cfg2)
    assert scalar(con, "SELECT count(*) FROM map_excluded_source") == 1
    after = dict(run(con, hr_spec).df[["d0", "m0"]].itertuples(index=False))
    assert "Kata iPhone › külső eszköz" not in after and after["Kata Watch"] == 10
    assert scalar(con, "SELECT count(*) FROM derived_person_daily WHERE source_key = ?",
                  [band]) == 0
    # raw data is untouched
    assert scalar(con, "SELECT count(*) FROM fact_heart_rate_sample WHERE source_key = ?",
                  [band]) == 10

    save_settings(cfg.data_path, {"derived": {"excluded_sources": []}})
    rebuild_core(con, load_config(data_dir=str(cfg.data_path), use_user_settings=True))
    assert scalar(con, "SELECT count(*) FROM map_excluded_source") == 0


def test_settings_page_saves_exclusions(tmp_path, monkeypatch, write_xml):
    d = tmp_path / "data"
    monkeypatch.setenv("VITALMART_DATA_DIR", str(d))
    cfg = load_config(data_dir=str(d))
    con = db.connect(cfg)
    run_import(con, cfg, write_xml(*_records()), person="partner")
    con.close()
    root = Path(__file__).resolve().parents[1]
    at = AppTest.from_file(str(root / "app" / "views" / "7_Beallitasok.py"),
                           default_timeout=180).run()
    assert not at.exception
    ms = at.multiselect(key="set_excluded")
    assert "apple:Mi Fit › Mi Smart Band 5" in ms.options or any(
        "Mi Smart Band 5" in o for o in ms.options)
    ms.set_value(["apple:Mi Fit › Mi Smart Band 5"])
    at.button(key="save_sources").click().run()
    assert not at.exception
    assert load_settings(d)["derived"]["excluded_sources"] == ["apple:Mi Fit › Mi Smart Band 5"]
