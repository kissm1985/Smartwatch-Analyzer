"""M3: derived metrics vs an independent pandas/scipy reference, primary source,
sleep regularity, WHO weekly volume, training load / ACWR, data quality."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import median_abs_deviation

from tests.conftest import rec, scalar, sleep
from vitalmart.config import load_config
from vitalmart.datamart import db
from vitalmart.importers.service import run_import
from vitalmart.paths import TRANSFORM_DIR
from vitalmart.sample_data_garmin import GarminSpec, write_export_zip, write_usb_folder
from vitalmart.transform import derived

RTOL = 1e-9


def _run(con, cfg, *files: str) -> None:
    derived.prepare(con, cfg)
    con.execute((TRANSFORM_DIR / "_pre" / "000_macros.sql").read_text())
    for f in files:
        con.execute((TRANSFORM_DIR / f).read_text())


def _series(seed: int = 1, days: int = 160) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days, freq="D")
    s = pd.Series(60 + rng.normal(0, 4, days).round(1), index=idx)
    drop = rng.choice(days, size=days // 5, replace=False)       # 20 % random gaps
    s = s.drop(s.index[drop])
    return s.drop(s.index[40:52])                                # plus one long gap


def _assert_close(a, b) -> None:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    assert np.array_equal(np.isnan(a), np.isnan(b)), (a, b)
    np.testing.assert_allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=RTOL, atol=1e-9)


def _load_series(con, s: pd.Series, metric: str = "resting_hr", person: str = "p") -> None:
    con.executemany(
        "INSERT INTO derived_person_daily VALUES (?, ?, ?, ?, ?, 'src', 1)",
        [(person, metric, d.date(), int(d.strftime("%Y%m%d")), float(v)) for d, v in s.items()])


def _pandas_reference(s: pd.Series, window: int = 60, min_days: int = 28) -> pd.DataFrame:
    r7, r28 = s.rolling("7D"), s.rolling("28D")
    bl = s.rolling(f"{window}D", closed="left")
    out = pd.DataFrame({
        "n7": r7.count(), "ma7": r7.mean(), "med7": r7.median(),
        "n28": r28.count(), "ma28": r28.mean(), "med28": r28.median(),
        "bl_n": bl.count().fillna(0),
        "bl_mean": bl.mean(), "bl_std": bl.std(ddof=1), "bl_median": bl.median(),
        "bl_mad": bl.apply(lambda x: median_abs_deviation(x, scale=1.0), raw=True),
    })
    enough = out["bl_n"] >= min_days
    for c in ("bl_mean", "bl_std", "bl_median", "bl_mad"):
        out.loc[~enough, c] = np.nan
    out["z"] = (s - out["bl_mean"]) / out["bl_std"]
    out["robust_z"] = (s - out["bl_median"]) / (1.4826 * out["bl_mad"])
    out["trend"] = out["ma7"] - out["ma28"]
    return out


def test_stats_match_pandas_scipy_reference(con, cfg):
    s = _series()
    _load_series(con, s)
    _run(con, cfg, "derived/020_stats.sql")
    got = con.execute("""SELECT n7, ma7, med7, n28, ma28, med28, bl_n, bl_mean, bl_std,
        bl_median, bl_mad, z_score, robust_z, trend_7v28, outside_band FROM derived_daily_stats
        ORDER BY date""").df()
    ref = _pandas_reference(s)
    for col_db, col_ref in (("n7", "n7"), ("ma7", "ma7"), ("med7", "med7"), ("n28", "n28"),
                            ("ma28", "ma28"), ("med28", "med28"), ("bl_n", "bl_n"),
                            ("bl_mean", "bl_mean"), ("bl_std", "bl_std"),
                            ("bl_median", "bl_median"), ("bl_mad", "bl_mad"),
                            ("z_score", "z"), ("robust_z", "robust_z"),
                            ("trend_7v28", "trend")):
        _assert_close(got[col_db], ref[col_ref].to_numpy())
    band = (ref["z"].abs() >= 1.5).where(ref["z"].notna())
    assert got["outside_band"].isna().sum() == ref["z"].isna().sum()
    assert (got["outside_band"].dropna().astype(bool).to_numpy()
            == band.dropna().astype(bool).to_numpy()).all()


def test_baseline_parameters_from_config(tmp_path):
    cfg = load_config(data_dir=str(tmp_path / "d"),
                      derived={"baseline_window_days": 30, "baseline_min_days": 10})
    con = db.connect(cfg)
    s = _series(seed=5)
    _load_series(con, s)
    _run(con, cfg, "derived/020_stats.sql")
    got = con.execute("SELECT bl_mean, bl_mad FROM derived_daily_stats ORDER BY date").df()
    ref = _pandas_reference(s, window=30, min_days=10)
    _assert_close(got["bl_mean"], ref["bl_mean"].to_numpy())
    _assert_close(got["bl_mad"], ref["bl_mad"].to_numpy())
    con.close()


def test_persons_and_metrics_are_independent(con, cfg):
    a, b = _series(seed=2), _series(seed=3) + 20
    _load_series(con, a, person="me")
    _load_series(con, b, person="partner")
    _load_series(con, a * 2, metric="steps", person="me")
    _run(con, cfg, "derived/020_stats.sql")
    for person, metric, s in (("me", "resting_hr", a), ("partner", "resting_hr", b),
                              ("me", "steps", a * 2)):
        got = con.execute("SELECT ma28, bl_std FROM derived_daily_stats WHERE person_key = ? "
                          "AND metric_key = ? ORDER BY date", [person, metric]).df()
        ref = _pandas_reference(s)
        _assert_close(got["ma28"], ref["ma28"].to_numpy())
        _assert_close(got["bl_std"], ref["bl_std"].to_numpy())


def test_sleep_regularity_and_debt_match_pandas(con, cfg):
    s = _series(seed=9, days=60)
    rng = np.random.default_rng(4)
    bed = pd.Series(600 + rng.normal(0, 30, len(s)), index=s.index)     # ~22:00
    wake = pd.Series(420 + rng.normal(0, 25, len(s)), index=s.index)    # ~07:00
    sleep_min = pd.Series(450 + rng.normal(0, 40, len(s)), index=s.index)
    _load_series(con, bed, "bedtime_min")
    _load_series(con, wake, "waketime_min")
    _load_series(con, sleep_min, "sleep_min")
    _run(con, cfg, "derived/030_sleep_regularity.sql")
    got = con.execute("""SELECT bedtime_std7, bedtime_std14, waketime_std7, debt7_min,
                         debt14_min FROM derived_sleep_regularity ORDER BY date""").df()
    goal = cfg.sleep.goal_hours * 60

    def std(x: pd.Series, win: str) -> np.ndarray:
        r = x.rolling(win)
        return r.std().where(r.count() >= 4).to_numpy()

    _assert_close(got["bedtime_std7"], std(bed, "7D"))
    _assert_close(got["bedtime_std14"], std(bed, "14D"))
    _assert_close(got["waketime_std7"], std(wake, "7D"))
    _assert_close(got["debt7_min"], (goal - sleep_min).rolling("7D").sum().to_numpy())
    _assert_close(got["debt14_min"], (goal - sleep_min).rolling("14D").sum().to_numpy())


# ---------------------------------------------------------------- end-to-end (import → derived)

@pytest.fixture(scope="module")
def garmin_zip(tmp_path_factory) -> Path:
    return write_export_zip(tmp_path_factory.mktemp("g"), GarminSpec(days=75, seed=8))


def test_end_to_end_stats_equal_reference(con, cfg, garmin_zip):
    run_import(con, cfg, garmin_zip)
    s = con.execute("""SELECT date, value FROM derived_person_daily
                       WHERE person_key = 'me' AND metric_key = 'resting_hr' ORDER BY date""").df()
    series = pd.Series(s["value"].to_numpy(), index=pd.to_datetime(s["date"]))
    assert len(series) == 75
    got = con.execute("""SELECT ma7, med28, bl_mean, bl_std, z_score FROM derived_daily_stats
                         WHERE person_key = 'me' AND metric_key = 'resting_hr'
                         ORDER BY date""").df()
    ref = _pandas_reference(series)
    for a, b in (("ma7", "ma7"), ("med28", "med28"), ("bl_mean", "bl_mean"),
                 ("bl_std", "bl_std"), ("z_score", "z")):
        _assert_close(got[a], ref[b].to_numpy())
    # the planted illness period (resting HR +8) produces high z-scores
    ill = con.execute("""SELECT max(z_score) FROM derived_daily_stats
                         WHERE metric_key = 'resting_hr' AND date BETWEEN ? AND ?""",
                      [date(2024, 9, 20) + timedelta(days=22),
                       date(2024, 9, 20) + timedelta(days=25)]).fetchall()[0][0]
    assert ill is None or ill > 1.5  # baseline needs 28 days; illness is at day 22-25
    metrics = {r[0] for r in con.execute(
        "SELECT DISTINCT metric_key FROM derived_person_daily").fetchall()}
    assert {"steps", "resting_hr", "stress_avg", "body_battery_max", "sleep_min",
            "bedtime_min", "who_equiv_min", "sleep_score", "vo2max", "body_mass",
            "stress_awake", "body_battery_charged"} <= metrics
    assert not any(m.startswith("garmin_") for m in metrics)      # auto metrics excluded
    assert not {"hrv_sdnn", "hrv_rmssd"} & metrics                 # Venu Sq 2: no HRV


def test_who_weekly_and_training_load(con, cfg, garmin_zip):
    run_import(con, cfg, garmin_zip)
    w = con.execute("""SELECT week_start, moderate_min, vigorous_min, who_equiv_min, who_method
                       FROM derived_weekly_activity ORDER BY week_start""").fetchall()
    for _ws, m, v, who, method in w:
        assert who == pytest.approx((m or 0) + 2 * (v or 0))
        assert method == "intensity_minutes"
    # Edwards TRIMP from Garmin zone minutes == independent recomputation
    tl = con.execute("""SELECT a.activity_id, sum(z.seconds_in_zone / 60.0 * least(z.zone, 5))
                        FROM fact_activity a JOIN fact_activity_hr_zone z USING (activity_id)
                        GROUP BY 1""").fetchall()
    assert tl and all(v > 0 for _, v in tl)
    load = con.execute("""SELECT date, load, acwr, acwr_valid, load_method, is_rest_day
                          FROM derived_training_load ORDER BY date""").df()
    s = pd.Series(load["load"].to_numpy())
    ref = s.rolling(7, min_periods=1).mean() / s.rolling(28, min_periods=1).mean()
    _assert_close(load["acwr"].where(s.rolling(28, min_periods=1).mean() > 0),
                  ref.where(s.rolling(28, min_periods=1).mean() > 0).to_numpy())
    assert not load["acwr_valid"].iloc[:27].any() and load["acwr_valid"].iloc[27:].any()
    assert set(load["load_method"]) <= {"edwards_trimp", "duration", "mixed", "none"}
    assert (load["is_rest_day"] == (load["load"] == 0)).all()


def test_primary_source_priority_and_override(tmp_path, write_xml):
    els = [rec("StepCount", "2024-05-01 10:00:00 +0200", "2024-05-01 10:30:00 +0200",
               value=1000, unit="count", source="Watch"),
           rec("StepCount", "2024-05-01 10:00:00 +0200", "2024-05-01 10:30:00 +0200",
               value=800, unit="count", source="Phone",
               device="<<HKDevice: 0x0>, name:iPhone, model:iPhone>")]
    xml = write_xml(*els)
    for override, expected in ((None, ("apple:Watch", 1000.0)),
                               ({"steps": "apple:Phone"}, ("apple:Phone", 800.0))):
        cfg = load_config(data_dir=str(tmp_path / f"d{bool(override)}"),
                          derived={"primary_source": override or {}})
        con = db.connect(cfg)
        run_import(con, cfg, xml)
        row = con.execute("SELECT source_key, value, n_sources FROM derived_person_daily "
                          "WHERE metric_key = 'steps'").fetchall()[0]
        assert row == (*expected, 2)
        con.close()


def test_apple_who_uses_exercise_minutes_as_moderate(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        rec("AppleExerciseTime", "2024-05-06 10:00:00 +0200", "2024-05-06 10:40:00 +0200",
            value=40, unit="min"),
        rec("AppleExerciseTime", "2024-05-08 10:00:00 +0200", "2024-05-08 10:30:00 +0200",
            value=30, unit="min")))
    assert con.execute("SELECT who_equiv_min, who_method, days_with_data FROM "
                       "derived_weekly_activity").fetchall() == [(70.0, "exercise_as_moderate", 2)]


def test_ln_hrv_and_sleep_metrics(con, cfg, write_xml):
    run_import(con, cfg, write_xml(
        sleep("AsleepCore", "2024-05-01 22:30:00 +0200", "2024-05-02 06:30:00 +0200"),
        rec("HeartRateVariabilitySDNN", "2024-05-02 02:00:00 +0200", value=50, unit="ms"),
        rec("HeartRateVariabilitySDNN", "2024-05-02 03:00:00 +0200", value=50, unit="ms")))
    m = dict(con.execute("SELECT metric_key, value FROM derived_person_daily").fetchall())
    assert m["hrv_sdnn"] == 50 and m["ln_hrv_sdnn"] == pytest.approx(np.log(50))
    assert m["sleep_min"] == 480 and m["bedtime_min"] == 630 and m["waketime_min"] == 390


def test_overlapping_workouts_of_one_person_count_once(con, cfg, write_xml):
    def wk(src: str) -> str:
        return (f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" '
                f'durationUnit="min" sourceName="{src}" startDate="2024-05-01 18:00:00 +0200" '
                f'endDate="2024-05-01 18:30:00 +0200" creationDate="2024-05-01 18:30:00 +0200"/>')
    run_import(con, cfg, write_xml(wk("Watch"), wk("Strava"),
                                   rec("StepCount", "2024-05-01 10:00:00 +0200", value=5,
                                       unit="count")))
    assert scalar(con, "SELECT count(*) FROM fact_activity") == 2
    assert scalar(con, "SELECT load FROM derived_training_load") == 30


# ---------------------------------------------------------------- data quality

def test_quality_coverage_gaps_flags_nonwear(con, cfg, write_xml):
    els = [rec("StepCount", f"2024-05-{d:02d} 10:00:00 +0200", value=100, unit="count")
           for d in list(range(1, 11)) + list(range(16, 21))]            # 5-day gap
    els += [rec("HeartRate", f"2024-05-01 0{h}:{m:02d}:00 +0200", value=61, unit="count/min")
            for h in range(0, 3) for m in range(0, 60, 5)]                # 36 identical values
    els += [rec("HeartRate", "2024-05-01 08:00:00 +0200", value=250, unit="count/min")]
    run_import(con, cfg, write_xml(*els))
    cov = con.execute("SELECT days_with_data, days_in_span, coverage_pct FROM dq_coverage "
                      "WHERE metric_key = 'steps'").fetchall()
    assert cov == [(15, 20, 75.0)]
    assert con.execute("SELECT gap_start, gap_end, gap_days FROM dq_gap").fetchall() \
        == [(date(2024, 5, 11), date(2024, 5, 15), 5)]
    flags = con.execute("SELECT fact_table, quality_flag, n FROM dq_flag_summary").fetchall()
    assert ("fact_heart_rate_sample", "out_of_range", 1) in flags
    kinds = dict(con.execute("SELECT kind, count(*) FROM dq_nonwear GROUP BY 1").fetchall())
    assert kinds == {"hr_constant": 1, "hr_gap": 1}   # 02:55 -> 08:00 pause, 36 × 61 bpm


def test_source_switch_detection(con, cfg, write_xml):
    phone = "<<HKDevice: 0x0>, name:iPhone, model:iPhone>"
    els = [rec("StepCount", f"2024-05-{d:02d} 10:00:00 +0200", value=100, unit="count",
               source="Phone", device=phone) for d in range(1, 21)]
    els += [rec("StepCount", f"2024-05-{d:02d} 11:00:00 +0200", value=120, unit="count",
                source="Watch") for d in range(10, 21)]                   # new watch from 05-10
    run_import(con, cfg, write_xml(*els))
    assert con.execute("SELECT metric_key, date, from_source, to_source FROM dq_source_switch")\
        .fetchall() == [("steps", date(2024, 5, 10), "apple:Phone", "apple:Watch")]


def test_garmin_offwrist_and_quality_cli(con, cfg, tmp_path, capsys):
    run_import(con, cfg, write_usb_folder(tmp_path / "usb", date(2024, 10, 1), 1))
    assert scalar(con, "SELECT count(*) FROM dq_nonwear WHERE kind = 'offwrist'") == 1
    con.close()
    from vitalmart.cli import main

    assert main(["--data-dir", str(cfg.data_path), "quality"]) == 0
    out = capsys.readouterr().out
    assert "Személy: me" in out and "nem viselt" in out and "Forrásváltások" in out


def test_quality_report_empty(con):
    from vitalmart.analytics.quality import quality_report

    assert list(quality_report(con)) == ["Nincs adatminőségi adat (még nem volt import)."]


def test_unknown_person_sources_are_unassigned(con, cfg, tmp_path):
    root = tmp_path / "g" / "DI_CONNECT" / "DI-Connect-Aggregator"
    root.mkdir(parents=True)
    (root / "UDSFile_x.json").write_text(json.dumps([{"calendarDate": "2024-01-02",
                                                      "totalSteps": 5}]))
    run_import(con, cfg, tmp_path / "g", person="me")
    assert scalar(con, "SELECT person_key FROM derived_person_daily") == "me"
