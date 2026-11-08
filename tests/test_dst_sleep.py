"""Time-zone / DST handling and sleep-day assignment (SPEC 4.4)."""

from __future__ import annotations

from datetime import date, datetime

from tests.conftest import rec, scalar, sleep
from vitalmart.config import load_config
from vitalmart.importers.service import run_import


def _session(con, sleep_date: str):
    return con.execute(
        "SELECT asleep_min, in_bed_min, deep_min, light_min, rem_min, awake_min, start_local, "
        "end_local, start_tz_offset_min, end_tz_offset_min, quality_flag "
        "FROM fact_sleep_session WHERE sleep_date = ? AND source_key = 'apple:Watch'",
        [sleep_date]).fetchall()[0]


def test_spring_forward_night_is_7_hours(con, cfg, write_xml):
    # 2024-03-30 23:00 CET -> 2024-03-31 07:00 CEST = 7 h of real time
    run_import(con, cfg, write_xml(
        sleep("AsleepCore", "2024-03-30 23:00:00 +0100", "2024-03-31 01:00:00 +0100"),
        sleep("AsleepDeep", "2024-03-31 01:00:00 +0100", "2024-03-31 04:00:00 +0200"),
        sleep("AsleepREM", "2024-03-31 04:00:00 +0200", "2024-03-31 06:30:00 +0200"),
        sleep("Awake", "2024-03-31 06:30:00 +0200", "2024-03-31 07:00:00 +0200"),
    ))
    s = _session(con, "2024-03-31")
    assert s[0] == 390  # asleep: 120 + 120 + 150
    assert s[1] == 420  # span (no InBed record) = 7 h, not 8
    assert s[2:6] == (120, 120, 150, 30)
    assert s[6] == datetime(2024, 3, 30, 23, 0) and s[7] == datetime(2024, 3, 31, 7, 0)
    assert (s[8], s[9]) == (60, 120)
    assert s[10] == "ok"


def test_fall_back_night_is_9_hours(con, cfg, write_xml):
    # 2024-10-26 23:00 CEST -> 2024-10-27 07:00 CET = 9 h of real time
    run_import(con, cfg, write_xml(
        sleep("InBed", "2024-10-26 23:00:00 +0200", "2024-10-27 07:00:00 +0100", source="Phone"),
        sleep("AsleepCore", "2024-10-26 23:00:00 +0200", "2024-10-27 07:00:00 +0100"),
    ))
    s = _session(con, "2024-10-27")
    assert s[0] == 540 and s[1] == 540
    phone = con.execute("SELECT in_bed_min, asleep_min, quality_flag FROM fact_sleep_session "
                        "WHERE source_key = 'apple:Phone'").fetchall()[0]
    assert phone == (540, None, "in_bed_only")


def test_local_time_uses_configured_zone_not_file_offset(con, cfg, write_xml):
    # same instant written with the export-time (winter) offset in summer
    run_import(con, cfg, write_xml(rec("HeartRate", "2024-07-01 09:30:00 +0100", value=60,
                                       unit="count/min")))
    row = con.execute("SELECT ts_utc, ts_local, tz_offset_min, date_key, minute_of_day "
                      "FROM fact_heart_rate_sample").fetchall()[0]
    assert row == (datetime(2024, 7, 1, 8, 30), datetime(2024, 7, 1, 10, 30), 120, 20240701, 630)


def test_local_date_boundary(con, cfg, write_xml):
    # 22:30 UTC on 06-30 is already 07-01 in Budapest
    run_import(con, cfg, write_xml(rec("StepCount", "2024-06-30 22:30:00 +0000",
                                       "2024-06-30 22:40:00 +0000", value=100, unit="count")))
    assert scalar(con, "SELECT date_key FROM fact_daily_summary") == 20240701


def test_other_timezone(tmp_path, write_xml):
    from vitalmart.datamart import db

    cfg = load_config(data_dir=str(tmp_path / "d2"), timezone="America/New_York")
    con = db.connect(cfg)
    run_import(con, cfg, write_xml(rec("HeartRate", "2024-07-01 09:30:00 +0200", value=60,
                                       unit="count/min")))
    assert scalar(con, "SELECT tz_offset_min FROM fact_heart_rate_sample") == -240
    assert scalar(con, "SELECT ts_local FROM fact_heart_rate_sample") == datetime(2024, 7, 1, 3, 30)
    con.close()


def test_sleep_day_convention_start(tmp_path, write_xml):
    from vitalmart.datamart import db

    els = [sleep("AsleepCore", "2024-05-01 23:30:00 +0200", "2024-05-02 06:30:00 +0200"),
           sleep("AsleepCore", "2024-05-03 00:30:00 +0200", "2024-05-03 07:00:00 +0200")]
    wake_cfg = load_config(data_dir=str(tmp_path / "w"))
    start_cfg = load_config(data_dir=str(tmp_path / "s"), sleep={"day_convention": "start"})
    xml = write_xml(*els)
    for cfg_, expected in ((wake_cfg, [date(2024, 5, 2), date(2024, 5, 3)]),
                           (start_cfg, [date(2024, 5, 1), date(2024, 5, 2)])):
        con = db.connect(cfg_)
        run_import(con, cfg_, xml)
        got = [r[0] for r in con.execute(
            "SELECT sleep_date FROM fact_sleep_session ORDER BY start_utc").fetchall()]
        assert got == expected
        con.close()


def test_session_split_by_gap_and_main_selection(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        sleep("AsleepCore", "2024-05-01 23:00:00 +0200", "2024-05-02 03:00:00 +0200"),
        sleep("Awake", "2024-05-02 03:00:00 +0200", "2024-05-02 03:40:00 +0200"),
        sleep("AsleepCore", "2024-05-02 03:40:00 +0200", "2024-05-02 06:40:00 +0200"),
        # afternoon nap, same wake day, separate session (gap > 90 min)
        sleep("AsleepCore", "2024-05-02 14:00:00 +0200", "2024-05-02 14:30:00 +0200"),
    ))
    rows = con.execute("SELECT is_main, asleep_min, awake_min FROM fact_sleep_session "
                       "ORDER BY start_utc").fetchall()
    assert rows == [(True, 420, 40), (False, 30, 0)]
    assert scalar(con, "SELECT count(DISTINCT session_id) FROM fact_sleep_stage_interval") == 2


def test_hrv_nightly_uses_main_session_samples(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        sleep("AsleepCore", "2024-05-01 23:00:00 +0200", "2024-05-02 07:00:00 +0200"),
        rec("HeartRateVariabilitySDNN", "2024-05-02 01:00:00 +0200", value=40, unit="ms"),
        rec("HeartRateVariabilitySDNN", "2024-05-02 03:00:00 +0200", value=50, unit="ms"),
        rec("HeartRateVariabilitySDNN", "2024-05-02 15:00:00 +0200", value=99, unit="ms"),
        rec("HeartRate", "2024-05-02 02:00:00 +0200", value=50, unit="count/min"),
    ))
    row = con.execute("SELECT sleep_date, hrv_method, hrv_avg_ms, sample_count, quality_flag "
                      "FROM fact_hrv_nightly").fetchall()[0]
    assert row == (date(2024, 5, 2), "SDNN", 45.0, 2, "ok")
    assert scalar(con, "SELECT context FROM fact_heart_rate_sample") == "sleep"
    assert scalar(con, "SELECT hr_avg FROM fact_sleep_session") == 50
