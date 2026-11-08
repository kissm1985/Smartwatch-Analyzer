"""Garmin import (M2): Connect export JSON + FIT, mapping from YAML, unknown fields."""

from __future__ import annotations

import json
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

from tests.conftest import SMALL_SPEC, scalar
from vitalmart.fit_writer import FitWriter, fit_time
from vitalmart.importers.base import ImportFailure
from vitalmart.importers.garmin import GarminAdapter, extract_records, generic_kind
from vitalmart.importers.service import detect_adapter, preview, run_import
from vitalmart.sample_data import write_export_zip as write_apple_zip
from vitalmart.sample_data_garmin import (
    DEVICE_ID,
    GarminSpec,
    write_export_dir,
    write_export_zip,
    write_usb_folder,
)
from vitalmart.transform.garmin import field_sql, json_value_sql

SPEC = GarminSpec(start=date(2024, 10, 15), days=20, seed=3)   # spans 2024-10-27 DST
SRC = f"garmin:{DEVICE_ID}"
FACTS = ["fact_daily_summary", "fact_sleep_session", "fact_activity", "fact_activity_hr_zone",
         "fact_measurement", "fact_heart_rate_sample", "fact_vo2max", "fact_body_measure"]


@pytest.fixture(scope="module")
def garmin_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_export_zip(tmp_path_factory.mktemp("garmin"), SPEC)


def counts(con) -> dict[str, int]:
    return {t: scalar(con, f"SELECT count(*) FROM {t}") for t in FACTS}


def test_detect_and_preview(garmin_zip: Path):
    assert isinstance(detect_adapter(garmin_zip), GarminAdapter)
    pv = preview(garmin_zip)
    assert pv.type_counts["uds"] == SPEC.days and pv.type_counts["sleep"] == SPEC.days
    assert pv.type_counts["fit:activity"] >= 2
    assert "generic:hydration_log_file" in pv.type_counts
    kinds = {f.path.split("/")[-1]: f.kind for f in pv.discovery.files}
    assert kinds["user_profile.json"] == "ignored"       # personal profile never read
    assert pv.date_from == "2024-10-15"


def test_import_populates_core(con, cfg, garmin_zip: Path):
    res = run_import(con, cfg, garmin_zip)
    assert res.status == "success" and res.counts.errors == 0
    assert scalar(con, "SELECT count(*) FROM fact_daily_summary WHERE source_key = ?", [SRC]) \
        == SPEC.days
    assert scalar(con, "SELECT count(*) FROM fact_sleep_session WHERE is_main") == SPEC.days
    n_json = scalar(con, "SELECT count(*) FROM fact_activity WHERE activity_id NOT LIKE 'garmin:fit:%'")
    n_fit = scalar(con, "SELECT count(*) FROM fact_activity WHERE activity_id LIKE 'garmin:fit:%'")
    assert n_json > 0 and n_fit == 1  # FIT copies of JSON activities are NOT duplicated
    assert scalar(con, "SELECT count(*) FROM fact_activity_hr_zone") == n_json * 7
    # one source (the watch), owned by the default person; HRV never invented for Garmin
    assert con.execute("SELECT source_key, person_key, device_type FROM dim_source").fetchall() \
        == [(SRC, "me", "watch")]
    assert scalar(con, "SELECT count(*) FROM fact_hrv_nightly") == 0
    assert scalar(con, "SELECT count(*) FROM fact_vo2max") > 0
    assert scalar(con, "SELECT count(*) FROM fact_body_measure") == 2
    # lineage
    assert scalar(con, """SELECT count(*) FROM fact_activity f LEFT JOIN dim_import_batch b
                          USING (import_batch_id) WHERE b.status IS DISTINCT FROM 'success'""") == 0


def test_unknown_field_lands_in_fact_measurement(con, cfg, garmin_zip: Path):
    run_import(con, cfg, garmin_zip)
    assert scalar(con, "SELECT count(*) FROM fact_measurement "
                       "WHERE metric_key = 'garmin_uds_some_future_score'") == SPEC.days
    assert scalar(con, "SELECT is_auto FROM dim_metric "
                       "WHERE metric_key = 'garmin_uds_some_future_score'")
    # non-numeric unknown object stays in the raw layer only
    assert scalar(con, "SELECT count(*) FROM stg_garmin_record WHERE file_kind = 'uds' AND "
                       "json_extract(payload, '$.someFutureNested.value') IS NOT NULL") == SPEC.days
    assert scalar(con, "SELECT count(*) FROM dim_metric WHERE metric_key LIKE '%future_nested%'") == 0
    # identifiers / goals are not turned into metrics
    assert scalar(con, "SELECT count(*) FROM dim_metric WHERE metric_key LIKE '%profile_pk%' "
                       "OR metric_key LIKE '%goal%' OR metric_key LIKE '%duration_in_ms%'") == 0
    # unmapped files (generic) with a date also contribute
    assert scalar(con, "SELECT count(*) FROM fact_measurement "
                       "WHERE metric_key = 'garmin_hydration_log_file_value_in_ml'") > 0


def test_idempotent_reimport(con, cfg, garmin_zip: Path, tmp_path: Path):
    run_import(con, cfg, garmin_zip)
    before = counts(con)
    assert run_import(con, cfg, garmin_zip).status == "skipped"
    res = run_import(con, cfg, garmin_zip, force=True)
    assert res.counts.new == 0 and res.counts.skipped == res.counts.read
    assert counts(con) == before
    folder = write_export_dir(tmp_path / "folder", SPEC)   # same data, extracted layout
    res2 = run_import(con, cfg, folder)
    assert res2.status == "success" and res2.counts.new == 0
    assert counts(con) == before


def _mini_export(tmp_path: Path, activity: dict, uds: dict | None = None) -> Path:
    root = tmp_path / "mini" / "DI_CONNECT"
    (root / "DI-Connect-Fitness").mkdir(parents=True)
    (root / "DI-Connect-Fitness" / "x_0_summarizedActivities.json").write_text(
        json.dumps([{"summarizedActivitiesExport": [activity]}]))
    if uds:
        (root / "DI-Connect-Aggregator").mkdir()
        (root / "DI-Connect-Aggregator" / "UDSFile_2024-05-01_2024-05-02.json").write_text(
            json.dumps([uds]))
    return tmp_path / "mini"


def test_units_are_converted_exactly(con, cfg, tmp_path: Path):
    start = datetime(2024, 5, 1, 16, 0)
    act = {"activityId": 1, "activityType": "running", "sportType": "RUNNING",
           "userProfileId": 7, "deviceId": 42,
           "startTimeGmt": (start - datetime(1970, 1, 1)).total_seconds() * 1000,
           "duration": 1_800_000.0, "distance": 500_000.0, "avgSpeed": 500_000 / 1_800_000,
           "calories": 1255.2, "elevationGain": 5000.0, "avgHr": 150.0, "maxHr": 175.0,
           "hrTimeInZone_3": 600_000, "aerobicTrainingEffect": 3.1, "activityTrainingLoad": 80.0}
    uds = {"userProfilePK": 7, "calendarDate": "2024-05-01", "totalSteps": 12000,
           "totalDistanceMeters": 9000, "restingHeartRate": 52,
           "allDayStress": {"aggregatorList": [
               {"type": "AWAKE", "averageStressLevel": 40},
               {"type": "TOTAL", "averageStressLevel": 31, "highDuration": 1800}]},
           "bodyBattery": {"bodyBatteryStatList": [
               {"bodyBatteryStatType": "LOWEST", "statsValue": 12},
               {"bodyBatteryStatType": "HIGHEST", "statsValue": 88}]}}
    run_import(con, cfg, _mini_export(tmp_path, act, uds))
    row = con.execute("""SELECT duration_min, distance_km, round(energy_kcal, 6),
        round(avg_speed_kmh, 6), round(pace_min_per_km, 6), elevation_gain_m, aerobic_effect,
        training_load, start_local, date_key FROM fact_activity""").fetchall()[0]
    assert row == (30.0, 5.0, 300.0, 10.0, 6.0, 50.0, 3.1, 80.0, datetime(2024, 5, 1, 18, 0),
                   20240501)
    assert con.execute("SELECT zone, seconds_in_zone FROM fact_activity_hr_zone").fetchall() \
        == [(3, 600.0)]
    d = con.execute("""SELECT source_key, steps, distance_km, resting_hr, stress_avg,
        body_battery_max, body_battery_min FROM fact_daily_summary""").fetchall()[0]
    # UDS has no device id -> attributed to the account's main device (from activities)
    assert d == ("garmin:42", 12000, 9.0, 52, 31, 88, 12)
    m = dict(con.execute("SELECT metric_key, value FROM fact_measurement").fetchall())
    assert m["stress_awake"] == 40 and m["stress_high_min"] == 30.0


def test_negative_stress_is_not_an_average(con, cfg, tmp_path: Path):
    uds = {"userProfilePK": 7, "calendarDate": "2024-05-01", "totalSteps": 1,
           "allDayStress": {"aggregatorList": [{"type": "TOTAL", "averageStressLevel": -1}]}}
    root = tmp_path / "u" / "DI_CONNECT" / "DI-Connect-Aggregator"
    root.mkdir(parents=True)
    (root / "UDSFile_a.json").write_text(json.dumps([uds]))
    run_import(con, cfg, tmp_path / "u")
    assert scalar(con, "SELECT stress_avg FROM fact_daily_summary") is None
    assert scalar(con, "SELECT quality_flag FROM fact_measurement WHERE metric_key='stress'") \
        == "out_of_range"
    assert scalar(con, "SELECT source_key FROM fact_daily_summary") == "garmin:account-7"


def test_usb_monitoring_fit(con, cfg, tmp_path: Path):
    usb = write_usb_folder(tmp_path / "usb", date(2024, 10, 26), 2)   # DST night 10-27
    res = run_import(con, cfg, usb)
    assert res.status == "success"
    # one HR sample per minute (timestamp_16 expanded), local day 1440 minutes...
    per_day = dict(con.execute("SELECT date_key, count(*) FROM fact_heart_rate_sample "
                               "GROUP BY 1 ORDER BY 1").fetchall())
    assert per_day[20241026] == 1440
    # ... and the 25-hour local day 2024-10-27 still gets every UTC minute it covers
    assert per_day[20241027] == 1440
    assert scalar(con, "SELECT max(minute_of_day) FROM fact_heart_rate_sample") == 1439
    st = con.execute("SELECT category, quality_flag, count(*) FROM fact_stress_sample "
                     "GROUP BY 1, 2 ORDER BY 1").fetchall()
    assert ("invalid", "invalid_value", 2) in st   # the planted -1 values, kept & flagged
    assert scalar(con, "SELECT count(*) FROM stg_garmin_fit_file") == 2


def test_corrupt_fit_rolls_back(con, cfg, tmp_path: Path):
    w = FitWriter()
    w.message("file_id", type=4, manufacturer=1, product=1, serial_number=1,
              time_created=fit_time(datetime(2024, 1, 1)))
    data = bytearray(w.to_bytes())
    data[-1] ^= 0xFF  # break CRC
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "A.fit").write_bytes(bytes(data))
    with pytest.raises(ImportFailure) as ei:
        run_import(con, cfg, tmp_path / "bad")
    assert ei.value.i18n_key == "import.bad_fit"
    assert scalar(con, "SELECT count(*) FROM stg_garmin_fit_file") == 0


def test_bad_json_rolls_back(con, cfg, garmin_zip: Path, tmp_path: Path):
    run_import(con, cfg, garmin_zip)
    before = counts(con)
    root = tmp_path / "b" / "DI_CONNECT" / "DI-Connect-Aggregator"
    root.mkdir(parents=True)
    (root / "UDSFile_x.json").write_text('[{"calendarDate": "2024-01-01", "totalSteps": ')
    with pytest.raises(ImportFailure) as ei:
        run_import(con, cfg, tmp_path / "b")
    assert ei.value.i18n_key == "import.bad_json"
    assert counts(con) == before


def test_content_mismatch_is_counted_not_crashing(con, cfg, tmp_path: Path):
    root = tmp_path / "c" / "DI_CONNECT" / "DI-Connect-Aggregator"
    root.mkdir(parents=True)
    (root / "UDSFile_x.json").write_text(json.dumps([{"somethingElse": 1}]))
    (root / "UDSFile_y.json").write_text(json.dumps([{"calendarDate": "2024-01-02",
                                                      "totalSteps": 5}]))
    res = run_import(con, cfg, tmp_path / "c")
    assert res.status == "success" and res.counts.errors == 1
    assert scalar(con, "SELECT count(*) FROM fact_daily_summary") == 1


def test_household_two_persons_and_conflict(con, cfg, garmin_zip: Path, tmp_path: Path):
    apple = write_apple_zip(tmp_path / "apple", SMALL_SPEC)
    run_import(con, cfg, apple, person="partner")
    run_import(con, cfg, garmin_zip, person="me")
    persons = dict(con.execute("SELECT vendor, string_agg(DISTINCT person_key) FROM dim_source "
                               "GROUP BY 1").fetchall())
    assert persons == {"Apple": "partner", "Garmin": "me"}
    assert {r[0] for r in con.execute("SELECT person_key FROM dim_person").fetchall()} \
        == {"partner", "me"}
    before = counts(con)
    with pytest.raises(ImportFailure) as ei:   # same watch attributed to another person
        run_import(con, cfg, garmin_zip, person="partner", force=True)
    assert ei.value.i18n_key == "import.person_conflict"
    assert counts(con) == before
    assert scalar(con, "SELECT count(*) FROM dim_source WHERE person_conflict") == 0
    with pytest.raises(ImportFailure):
        run_import(con, cfg, garmin_zip, person="Bad Name!")


def test_dst_sleep_and_hr_context(con, cfg, tmp_path: Path):
    # Garmin sleep across the fall-back night: 22:00 CEST -> 06:00 CET = 9 h real time
    root = tmp_path / "s" / "DI_CONNECT" / "DI-Connect-Wellness"
    root.mkdir(parents=True)
    (root / f"a_b_{7}0000_sleepData.json").write_text(json.dumps([{
        "sleepStartTimestampGMT": "2024-10-26T20:00:00.0",
        "sleepEndTimestampGMT": "2024-10-27T05:00:00.0", "calendarDate": "2024-10-27",
        "deepSleepSeconds": 7200, "lightSleepSeconds": 18000, "remSleepSeconds": 6000,
        "awakeSleepSeconds": 1200, "sleepScores": {"overallScore": 80},
        "spo2SleepSummary": {"userProfilePk": 70000, "deviceId": DEVICE_ID}}]))
    run_import(con, cfg, tmp_path / "s")
    row = con.execute("SELECT sleep_date, in_bed_min, asleep_min, start_local, end_local, "
                      "sleep_score, has_stages, source_key FROM fact_sleep_session").fetchall()[0]
    assert row == (date(2024, 10, 27), 540.0, 520.0, datetime(2024, 10, 26, 22, 0),
                   datetime(2024, 10, 27, 6, 0), 80.0, True, SRC)
    # monitoring HR of the same watch inside the night -> context 'sleep'
    usb = write_usb_folder(tmp_path / "usb", date(2024, 10, 27), 1)
    run_import(con, cfg, usb)
    assert scalar(con, "SELECT count(*) FROM fact_heart_rate_sample WHERE context = 'sleep'") > 0
    assert scalar(con, "SELECT hr_min FROM fact_sleep_session") is not None


# ---------------------------------------------------------------- unit-level helpers

def test_json_value_sql_filter_and_validation(con):
    payload = json.dumps({"a": {"l": [{"t": "X", "v": 1}, {"t": "TOTAL", "v": 7}]}, "b": 2})
    expr = field_sql({"path": "a.l[t=TOTAL].v"})
    assert con.execute(f"SELECT {expr} FROM (SELECT ?::JSON AS payload)", [payload]).fetchall() \
        == [(7.0,)]
    expr2 = field_sql({"path": ["missing", "b"], "divide": 4})
    assert con.execute(f"SELECT {expr2} FROM (SELECT ?::JSON AS payload)", [payload]).fetchall() \
        == [(0.5,)]
    with pytest.raises(ValueError):
        json_value_sql("a'; DROP TABLE x; --")


def test_extract_records_and_generic_kind():
    assert extract_records([{"summarizedActivitiesExport": [{"a": 1}, {"a": 2}]}],
                           "summarizedActivitiesExport") == [{"a": 1}, {"a": 2}]
    assert extract_records({"items": [{"a": 1}]}, None) == [{"a": 1}]
    assert extract_records({"a": 1, "b": 2}, None) == [{"a": 1, "b": 2}]
    assert generic_kind("x/HydrationLogFile_2026-01-06_2026-04-16.json") == "hydration_log_file"
    assert generic_kind("x/someone@example.com_personalRecord.json") == "personal_record"
    assert generic_kind("x/100000001_fitnessAgeData.json") == "fitness_age_data"


def test_schema_dump_has_no_values(garmin_zip: Path, capsys):
    from vitalmart.cli import main

    assert main(["schema", str(garmin_zip)]) == 0
    out = capsys.readouterr().out
    assert "$[].allDayStress.aggregatorList[].averageStressLevel" in out
    assert "record (n=" in out              # FIT message structure
    assert "95.4" not in out and "Szintetikus" not in out
    assert "user_profile.json  [kihagyva]" in out


def test_mapping_lives_in_yaml(tmp_path: Path):
    # renaming the UDS file pattern in the mapping changes recognition without code changes
    from vitalmart.importers.garmin import load_mapping

    m = load_mapping()
    m["files"]["uds"]["match"]["filename"] = r"^DailySummary_.*\.json$"
    ad = GarminAdapter(m)
    assert ad.classify_name("DI_CONNECT/DI-Connect-Aggregator/DailySummary_1.json") == "json:uds"
    assert ad.classify_name("DI_CONNECT/DI-Connect-Aggregator/UDSFile_1.json") == "json:generic"
    assert zipfile.is_zipfile is not None
