"""Apple Health import: sample end-to-end, idempotency, dedup/upsert, lineage."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from tests.conftest import SMALL_SPEC, rec, scalar
from vitalmart.importers.apple_health import AppleHealthAdapter
from vitalmart.importers.base import ImportFailure
from vitalmart.importers.service import detect_adapter, preview, run_import

CORE_TABLES = [
    "fact_measurement", "fact_heart_rate_sample", "fact_sleep_session",
    "fact_sleep_stage_interval", "fact_hrv_nightly", "fact_activity", "fact_body_measure",
    "fact_vo2max", "fact_daily_summary",
]


def counts(con) -> dict[str, int]:
    return {t: scalar(con, f"SELECT count(*) FROM {t}") for t in CORE_TABLES}


def test_detect_and_preview(sample_zip: Path):
    assert isinstance(detect_adapter(sample_zip), AppleHealthAdapter)
    seen: list[float] = []
    pv = preview(sample_zip, progress=seen.append)
    kinds = {f.kind for f in pv.discovery.files}
    assert {"main", "ignored", "route", "ecg"} <= kinds
    assert pv.type_counts["HKQuantityTypeIdentifierHeartRate"] > 100
    assert pv.date_from == "2024-03-19" and pv.date_to == "2024-04-08"
    assert seen and seen[-1] == 1.0


def test_sample_import_populates_core(con, cfg, sample_zip: Path):
    progress: list[float] = []
    res = run_import(con, cfg, sample_zip, progress=progress.append)
    assert res.status == "success"
    assert res.counts.new > 1000 and res.counts.errors == 0
    assert res.counts.skipped >= 1  # the planted exact duplicate
    assert progress[-1] == 1.0
    assert str(res.date_from) == "2024-03-19"
    c = counts(con)
    assert all(v > 0 for v in c.values()), c
    assert scalar(con, "SELECT count(*) FROM fact_sleep_session WHERE is_main AND has_stages") \
        == SMALL_SPEC.days
    assert scalar(con, "SELECT status FROM dim_import_batch WHERE import_batch_id = ?",
                  [res.batch_id]) == "success"
    # lineage: every fact row points at an existing successful batch
    for t in CORE_TABLES:
        assert scalar(con, f"""SELECT count(*) FROM {t} f
            LEFT JOIN dim_import_batch b ON b.import_batch_id = f.import_batch_id
            WHERE b.status IS DISTINCT FROM 'success'""") == 0, t
    # dimensions
    assert scalar(con, "SELECT count(*) FROM dim_source WHERE vendor = 'Apple'") == 4
    assert scalar(con, "SELECT device_type FROM dim_source WHERE source_key = 'apple:Teszt Apple Watch'") == "watch"
    assert scalar(con, "SELECT count(*) FROM dim_activity_type WHERE unified_type = 'running'") >= 0
    # HRV method is always stored and never mixed
    assert {r[0] for r in con.execute("SELECT DISTINCT hrv_method FROM fact_hrv_nightly").fetchall()} == {"SDNN"}
    # ECG stored verbatim, cda ignored but inventoried
    assert scalar(con, "SELECT count(*) FROM stg_apple_ecg") == 1
    assert scalar(con, "SELECT count(*) FROM stg_source_file WHERE file_kind = 'ignored'") == 1


def test_second_import_is_skipped_and_force_does_not_duplicate(con, cfg, sample_zip: Path):
    run_import(con, cfg, sample_zip)
    before = counts(con)
    stg_before = scalar(con, "SELECT count(*) FROM stg_apple_record")

    res2 = run_import(con, cfg, sample_zip)
    assert res2.status == "skipped" and res2.previous_batch_id is not None
    assert counts(con) == before

    res3 = run_import(con, cfg, sample_zip, force=True)
    assert res3.status == "success"
    assert res3.counts.new == 0 and res3.counts.updated == 0
    assert res3.counts.skipped == res3.counts.read
    assert counts(con) == before
    assert scalar(con, "SELECT count(*) FROM stg_apple_record") == stg_before


def test_same_data_as_folder_does_not_duplicate(con, cfg, sample_zip: Path, tmp_path: Path):
    run_import(con, cfg, sample_zip)
    before = counts(con)
    folder = tmp_path / "unzipped"
    with zipfile.ZipFile(sample_zip) as zf:
        zf.extractall(folder)
    res = run_import(con, cfg, folder)  # different fingerprint, identical content
    assert res.status == "success" and res.counts.new == 0
    assert counts(con) == before


def test_upsert_newer_batch_wins_and_raw_is_kept(con, cfg, write_xml):
    t = "2024-05-01 10:00:00 +0200"
    run_import(con, cfg, write_xml(rec("RestingHeartRate", t, value=55, unit="count/min")))
    res = run_import(con, cfg, write_xml(
        rec("RestingHeartRate", t, value=57, unit="count/min"),
        rec("RestingHeartRate", "2024-05-02 10:00:00 +0200", value=54, unit="count/min")))
    assert (res.counts.new, res.counts.updated, res.counts.skipped) == (1, 1, 0)
    vals = con.execute("SELECT value FROM fact_measurement WHERE metric_key='resting_hr' "
                       "ORDER BY ts_utc").fetchall()
    assert vals == [(57.0,), (54.0,)]
    raw = con.execute("SELECT value_num FROM stg_apple_record ORDER BY import_batch_id, value_num").fetchall()
    assert raw == [(55.0,), (54.0,), (57.0,)]


def test_same_instant_different_offset_is_same_record(con, cfg, write_xml):
    # export made in winter vs. summer writes the same instant with different offsets
    run_import(con, cfg, write_xml(rec("RestingHeartRate", "2024-05-01 10:00:00 +0200", value=55,
                                       unit="count/min")))
    res = run_import(con, cfg, write_xml(rec("RestingHeartRate", "2024-05-01 09:00:00 +0100",
                                             value=55, unit="count/min")))
    assert res.counts.new == 0 and res.counts.skipped == 1
    assert scalar(con, "SELECT count(*) FROM fact_measurement") == 1


def test_rebuild_reproduces_core(con, cfg, sample_zip: Path):
    from vitalmart.transform.rebuild import rebuild

    run_import(con, cfg, sample_zip)
    before = counts(con)
    snap = con.execute("SELECT sum(asleep_min), sum(in_bed_min) FROM fact_sleep_session").fetchall()[0]
    rebuild(con, cfg)
    assert counts(con) == before
    after = con.execute("SELECT sum(asleep_min), sum(in_bed_min) FROM fact_sleep_session").fetchall()[0]
    assert after == pytest.approx(snap, rel=1e-12)  # parallel float sums: order may differ


# ---------------------------------------------------------------- failures / rollback

def test_truncated_xml_rolls_back(con, cfg, sample_zip: Path, tmp_path: Path):
    with zipfile.ZipFile(sample_zip) as zf:
        xml = zf.read("apple_health_export/export.xml")
    bad = tmp_path / "export.xml"
    bad.write_bytes(xml[: len(xml) // 2])
    with pytest.raises(ImportFailure) as ei:
        run_import(con, cfg, bad)
    assert ei.value.i18n_key == "import.bad_xml"
    assert scalar(con, "SELECT count(*) FROM stg_apple_record") == 0
    assert scalar(con, "SELECT count(*) FROM fact_measurement") == 0
    assert scalar(con, "SELECT status FROM dim_import_batch") == "failed"


def test_failure_keeps_previous_data(con, cfg, sample_zip: Path, tmp_path: Path):
    run_import(con, cfg, sample_zip)
    before = counts(con)
    bad = tmp_path / "export.xml"
    bad.write_text('<?xml version="1.0"?><HealthData><Record type="x" startDate="')
    with pytest.raises(ImportFailure):
        run_import(con, cfg, bad)
    assert counts(con) == before


def test_corrupt_zip(con, cfg, tmp_path: Path):
    p = tmp_path / "export.zip"
    p.write_bytes(b"PK\x03\x04 definitely not a zip")
    with pytest.raises(ImportFailure) as ei:
        run_import(con, cfg, p, adapter_name="apple_health")
    assert ei.value.i18n_key in ("import.bad_zip", "import.missing_export_xml")


def test_zip_without_export_xml(con, cfg, tmp_path: Path):
    p = tmp_path / "export.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("something/else.txt", "x")
    with pytest.raises(ImportFailure) as ei:
        run_import(con, cfg, p)
    assert ei.value.i18n_key == "import.unknown_source"
    with pytest.raises(ImportFailure) as ei2:
        run_import(con, cfg, p, adapter_name="apple_health")
    assert ei2.value.i18n_key == "import.missing_export_xml"


def test_missing_path(con, cfg, tmp_path: Path):
    with pytest.raises(ImportFailure) as ei:
        run_import(con, cfg, tmp_path / "nope.zip")
    assert ei.value.i18n_key == "import.not_found"


def test_unknown_record_type_lands_in_fact_measurement(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        rec("HKQuantityTypeIdentifierSomeFutureMetric", "2024-05-01 10:00:00 +0200", value=3.5,
            unit="zz"),
        rec("HKCategoryTypeIdentifierHandwashingEvent", "2024-05-01 11:00:00 +0200",
            "2024-05-01 11:00:30 +0200", value="HKCategoryValueNotApplicable")))
    rows = con.execute("SELECT metric_key, value, value_text, unit FROM fact_measurement "
                       "ORDER BY metric_key").fetchall()
    assert rows == [("apple_handwashing_event", None, "HKCategoryValueNotApplicable", None),
                    ("apple_some_future_metric", 3.5, None, "zz")]
    assert scalar(con, "SELECT count(*) FROM dim_metric WHERE is_auto") == 2


def test_unit_conversion_scaling_and_quality_flags(con, cfg, write_xml):
    t = "2024-05-01 10:00:00 +0200"
    run_import(con, cfg, write_xml(
        rec("DistanceWalkingRunning", t, "2024-05-01 10:30:00 +0200", value=2, unit="mi"),
        rec("BodyMass", t, value=176.37, unit="lb", source="Scale", device=None),
        rec("OxygenSaturation", t, value=0.95, unit="%"),
        rec("BodyFatPercentage", t, value=0.2, unit="%", source="Scale", device=None),
        rec("ActiveEnergyBurned", t, value=418.4, unit="kJ"),
        rec("HeartRate", t, value=250, unit="count/min"),
        rec("HeartRate", "2024-05-01 10:01:00 +0200", value=60, unit="count/min"),
        rec("OxygenSaturation", "2024-05-01 10:05:00 +0200", value=0.5, unit="%"),
        rec("RestingHeartRate", t, value=1, unit="furlong")))
    got = dict(con.execute("SELECT metric_key || ':' || CAST(round(value, 2) AS VARCHAR), "
                           "quality_flag FROM fact_measurement").fetchall())
    assert got["distance_walk_run:3.22"] == "ok"
    assert got["body_mass:80.0"] == "ok"
    assert got["spo2:95.0"] == "ok"
    assert got["body_fat_pct:20.0"] == "ok"
    assert got["active_energy:100.0"] == "ok"
    assert got["spo2:50.0"] == "out_of_range"         # SPEC 4.5: SpO2 < 70 % flagged, kept
    assert got["resting_hr:1.0"] == "unit_unconverted"
    hr = con.execute("SELECT bpm, quality_flag FROM fact_heart_rate_sample ORDER BY ts_utc").fetchall()
    assert hr == [(250.0, "out_of_range"), (60.0, "ok")]
    # flagged values are excluded from the daily summary
    assert scalar(con, "SELECT hr_max FROM fact_daily_summary WHERE source_key='apple:Watch'") == 60
    assert scalar(con, "SELECT spo2_min FROM fact_daily_summary WHERE source_key='apple:Watch'") == 95


def test_workouts_legacy_and_statistics(con, cfg, write_xml):
    legacy = (
        '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="0.5" '
        'durationUnit="hr" totalDistance="3.10686" totalDistanceUnit="mi" '
        'totalEnergyBurned="300" totalEnergyBurnedUnit="kcal" sourceName="Watch" '
        'creationDate="2024-05-01 18:30:00 +0200" startDate="2024-05-01 18:00:00 +0200" '
        'endDate="2024-05-01 18:30:00 +0200">'
        '<MetadataEntry key="HKElevationAscended" value="5000 cm"/>'
        '<WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" average="150" minimum="100" '
        'maximum="175" unit="count/min"/></Workout>')
    modern = (
        '<Workout workoutActivityType="HKWorkoutActivityTypeCurling" duration="40" '
        'durationUnit="min" sourceName="Watch" creationDate="2024-05-02 19:40:00 +0200" '
        'startDate="2024-05-02 19:00:00 +0200" endDate="2024-05-02 19:40:00 +0200">'
        '<WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="836.8" '
        'unit="kJ"/></Workout>')
    run_import(con, cfg, write_xml(
        legacy, modern,
        rec("HeartRateRecoveryOneMinute", "2024-05-01 18:31:00 +0200", value=30,
            unit="count/min"),
        rec("HeartRate", "2024-05-01 18:10:00 +0200", value=150, unit="count/min")))
    rows = con.execute("""
        SELECT activity_type_key, round(duration_min, 2), round(distance_km, 3),
               round(energy_kcal, 1), hr_avg, round(avg_speed_kmh, 2), elevation_gain_m,
               hr_recovery_1min, date_key
        FROM fact_activity ORDER BY start_utc""").fetchall()
    assert rows[0] == ("apple:HKWorkoutActivityTypeRunning", 30.0, 5.0, 300.0, 150.0, 10.0,
                       50.0, 30.0, 20240501)
    assert rows[1][:4] == ("apple:HKWorkoutActivityTypeCurling", 40.0, None, 200.0)
    types = dict(con.execute("SELECT original_name, unified_type FROM dim_activity_type").fetchall())
    assert types == {"HKWorkoutActivityTypeRunning": "running",
                     "HKWorkoutActivityTypeCurling": "other"}
    assert scalar(con, "SELECT context FROM fact_heart_rate_sample") == "activity"


def test_side_files_from_folder(con, cfg, sample_zip: Path, tmp_path: Path):
    folder = tmp_path / "f"
    with zipfile.ZipFile(sample_zip) as zf:
        zf.extractall(folder)
    shutil.move(folder / "apple_health_export", tmp_path / "apple_health_export")
    res = run_import(con, cfg, tmp_path / "apple_health_export")
    assert res.status == "success"
    assert scalar(con, "SELECT count(*) FROM stg_apple_ecg") == 1
    assert scalar(con, "SELECT count(*) FROM stg_source_file WHERE file_kind='route'") == 1
