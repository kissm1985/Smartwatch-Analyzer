"""Regression tests for cases found in REAL exports (reproduced with synthetic data only)."""

from __future__ import annotations

import json
import unicodedata
import zipfile
from pathlib import Path

from tests.conftest import apple_xml, rec, scalar, sleep
from vitalmart.importers.apple_health import AppleHealthAdapter
from vitalmart.importers.service import detect_adapter, run_import


def _session(con):
    return con.execute("SELECT asleep_min, awake_min, in_bed_min, quality_flag "
                       "FROM fact_sleep_session WHERE source_key = 'apple:App'").fetchall()


def test_localised_nfd_main_xml_with_long_dtd(con, cfg, tmp_path: Path):
    # Hungarian iOS: "exportálás.xml", zip member name in NFD; DTD longer than 4 KB
    name = unicodedata.normalize("NFD", "apple_health_export/exportálás.xml")
    long_dtd = "".join(f"<!ATTLIST Record attr{i} CDATA #IMPLIED>\n" for i in range(300))
    xml = apple_xml(rec("RestingHeartRate", "2024-05-01 10:00:00 +0200", value=55,
                        unit="count/min")).replace("<!ELEMENT HealthData ANY>",
                                                   "<!ELEMENT HealthData ANY>\n" + long_dtd)
    assert xml.index("<HealthData") > 4096
    p = tmp_path / unicodedata.normalize("NFD", "exportálás.zip")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(name, xml)
        zf.writestr("apple_health_export/export_cda.xml", "<ClinicalDocument/>")
    assert isinstance(detect_adapter(p), AppleHealthAdapter)
    disc = AppleHealthAdapter().discover(p)
    assert {f.kind for f in disc.files} == {"main", "ignored"}
    assert run_import(con, cfg, p).status == "success"
    assert scalar(con, "SELECT value FROM fact_measurement") == 55


def test_chained_overlapping_segments_are_unioned(con, cfg, write_xml):
    # a sleep app writes overlapping chunks: 23:00-01:00, 00:50-03:00, 02:55-06:00
    run_import(con, cfg, write_xml(
        sleep("AsleepUnspecified", "2024-05-01 23:00:00 +0200", "2024-05-02 01:00:00 +0200", "App"),
        sleep("AsleepUnspecified", "2024-05-02 00:50:00 +0200", "2024-05-02 03:00:00 +0200", "App"),
        sleep("AsleepUnspecified", "2024-05-02 02:55:00 +0200", "2024-05-02 06:00:00 +0200", "App"),
    ))
    assert _session(con) == [(420.0, None, 420.0, "ok")]   # 7 h, not 7 h 15 min
    flags = sorted(r[0] for r in con.execute(
        "SELECT quality_flag FROM fact_sleep_stage_interval").fetchall())
    assert flags == ["ok", "overlap", "overlap"]


def test_awake_wins_over_overlapping_asleep(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        sleep("AsleepUnspecified", "2024-05-01 23:00:00 +0200", "2024-05-02 03:05:00 +0200", "App"),
        sleep("Awake", "2024-05-02 03:00:00 +0200", "2024-05-02 03:30:00 +0200", "App"),
        sleep("AsleepUnspecified", "2024-05-02 03:25:00 +0200", "2024-05-02 06:00:00 +0200", "App"),
    ))
    asleep, awake, in_bed, flag = _session(con)[0]
    assert awake == 30 and asleep == 390 and in_bed == 420 and flag == "ok"


def test_implausibly_long_interval_is_flagged_not_used(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        sleep("InBed", "2024-05-01 10:00:00 +0200", "2024-05-02 08:00:00 +0200", "App"),  # 22 h
        sleep("AsleepUnspecified", "2024-05-01 23:30:00 +0200", "2024-05-02 06:30:00 +0200", "App"),
    ))
    assert _session(con) == [(420.0, None, 420.0, "ok")]
    assert con.execute("SELECT session_id IS NULL, quality_flag FROM fact_sleep_stage_interval "
                       "WHERE stage_key = 'in_bed'").fetchall() == [(True, "implausible_duration")]


def test_hrv_rmssd_and_sdnn_are_separate_methods(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        sleep("AsleepCore", "2024-05-01 23:00:00 +0200", "2024-05-02 07:00:00 +0200"),
        rec("HeartRateVariabilitySDNN", "2024-05-02 02:00:00 +0200", value=40, unit="ms"),
        rec("HeartRateVariabilityRMSSD", "2024-05-02 02:00:00 +0200", value=30, unit="ms"),
        rec("HeartRateVariabilityRMSSD", "2024-05-02 04:00:00 +0200", value=34, unit="ms"),
    ))
    rows = con.execute("SELECT hrv_method, hrv_avg_ms, sample_count FROM fact_hrv_nightly "
                       "ORDER BY 1").fetchall()
    assert rows == [("RMSSD", 32.0, 2), ("SDNN", 40.0, 1)]


def test_workout_hr_falls_back_to_samples(con, cfg, write_xml):
    wk = ('<Workout workoutActivityType="HKWorkoutActivityTypeSquash" duration="30" '
          'durationUnit="min" sourceName="Watch" startDate="2024-05-01 18:00:00 +0200" '
          'endDate="2024-05-01 18:30:00 +0200" creationDate="2024-05-01 18:30:00 +0200"/>')
    hrs = [rec("HeartRate", f"2024-05-01 18:{m:02d}:00 +0200", value=v, unit="count/min")
           for m, v in ((5, 140), (15, 160), (25, 150))]
    run_import(con, cfg, write_xml(wk, *hrs))
    assert con.execute("SELECT hr_avg, hr_max, hr_min FROM fact_activity").fetchall() \
        == [(150.0, 160.0, 140.0)]
    assert con.execute("SELECT unified_type, unified_name_hu FROM dim_activity_type").fetchall() \
        == [("racket", "Ütős sport (squash, tenisz …)")]


def test_reproductive_records_are_tagged_sensitive(con, cfg, write_xml):
    run_import(con, cfg, write_xml(rec(
        "HKCategoryTypeIdentifierMenstrualFlow", "2024-05-01 08:00:00 +0200",
        value="HKCategoryValueVaginalBleedingLight", device=None)))
    assert con.execute("SELECT d.category, f.value_text FROM fact_measurement f "
                       "JOIN dim_metric d USING (metric_key)").fetchall() \
        == [("reproductive", "HKCategoryValueVaginalBleedingLight")]


def test_garmin_indoor_cardio_is_squash(con, cfg, tmp_path: Path):
    root = tmp_path / "g" / "DI_CONNECT" / "DI-Connect-Fitness"
    root.mkdir(parents=True)
    (root / "x_0_summarizedActivities.json").write_text(json.dumps([{
        "summarizedActivitiesExport": [{"activityId": 5, "activityType": "indoor_cardio",
                                        "deviceId": 1, "startTimeGmt": 1.7e12,
                                        "duration": 3.6e6}]}]))
    run_import(con, cfg, tmp_path / "g")
    assert scalar(con, "SELECT unified_type FROM dim_activity_type") == "racket"
