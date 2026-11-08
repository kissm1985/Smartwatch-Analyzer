"""M5: semantic layer, SQL generation, engine, charts, saved analyses."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.conftest import SMALL_SPEC
from vitalmart.importers.service import run_import
from vitalmart.sample_data import write_export_zip
from vitalmart.sample_data_garmin import GarminSpec, write_usb_folder
from vitalmart.sample_data_garmin import write_export_zip as write_garmin_zip
from vitalmart.semantic import store
from vitalmart.semantic.charts import build_figure
from vitalmart.semantic.engine import lttb, run, transform
from vitalmart.semantic.model import load_model
from vitalmart.semantic.spec import AnalysisSpec, SpecError, validate
from vitalmart.semantic.sqlgen import build_query, distinct_values_query

MODEL = load_model()


def S(**kw) -> AnalysisSpec:
    kw.setdefault("period", "all")
    return AnalysisSpec.model_validate(kw)


@pytest.fixture(scope="module")
def household(tmp_path_factory):
    """Apple (partner) + Garmin (me) synthetic data in one database."""
    from vitalmart.config import load_config
    from vitalmart.datamart import db

    d = tmp_path_factory.mktemp("hh")
    cfg = load_config(data_dir=str(d / "data"))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(d / "a", SMALL_SPEC), person="partner")
    gspec = GarminSpec(days=40)
    run_import(con, cfg, write_garmin_zip(d / "g", gspec), person="me")
    run_import(con, cfg, write_usb_folder(d / "usb", gspec.start, 2), person="me")
    yield con
    con.close()


# ---------------------------------------------------------------- model / SQL compile

def test_every_measure_and_dimension_compiles(con):
    """Each YAML expression is valid SQL against the real schema (empty DB)."""
    for tk, topic in MODEL.topics.items():
        extra = []
        if topic.required_filters:
            extra = [{"dimension": d, "values": ["x"]} for d in topic.required_filters]
        req_dims = list(topic.required_dimensions)
        for mk in topic.measures:
            spec = S(topic=tk, measures=[{"key": mk}], dimensions=req_dims, filters=extra)
            run(con, spec, MODEL)
        for dk in MODEL.topic_dimensions(tk):
            dims = list(dict.fromkeys([*req_dims, dk]))[:3]
            spec = S(topic=tk, measures=[{"key": next(iter(topic.measures))}], dimensions=dims,
                     filters=extra, time_grain="none")
            run(con, spec, MODEL)
            con.execute(distinct_values_query(tk, dk, MODEL)).fetchall()
        if topic.timeline:
            run(con, S(topic=tk, measures=[{"key": next(iter(topic.measures))}],
                       dimensions=req_dims, filters=extra, chart="timeline"), MODEL)


def test_validation_rules():
    cases = [
        (dict(topic="nope", measures=[{"key": "x"}]), "explorer.err.topic"),
        (dict(topic="daily", measures=[{"key": "steps"}, {"key": "steps"}]),
         "explorer.err.duplicate_measure"),
        (dict(topic="daily", measures=[{"key": k} for k in
                                       ("steps", "resting_hr", "hr_avg", "spo2_avg", "vo2max")]),
         "explorer.err.measure_count"),
        (dict(topic="daily", measures=[{"key": "steps"}],
              dimensions=["date.year", "date.month", "person", "date.weekend"]),
         "explorer.err.dim_count"),
        (dict(topic="daily", measures=[{"key": "steps"}], dimensions=["activity.type"]),
         "explorer.err.dimension"),
        (dict(topic="daily", measures=[{"key": "steps", "agg": "none"}, {"key": "resting_hr"}]),
         "explorer.err.mixed_raw"),
        (dict(topic="daily", measures=[{"key": "steps", "agg": "none"},
                                       {"key": "resting_hr", "agg": "none"}]),
         "explorer.err.raw_long"),
        (dict(topic="daily", measures=[{"key": "steps"}], time_grain="hour"),
         "explorer.err.grain_intraday"),
        (dict(topic="hrv", measures=[{"key": "hrv"}]), "explorer.err.required_dimension"),
        (dict(topic="baseline", measures=[{"key": "value"}]), "explorer.err.required_filter"),
        (dict(topic="daily", measures=[{"key": "steps"}], chart="corr"), "explorer.err.need_two"),
        (dict(topic="daily", measures=[{"key": "steps"}], chart="calendar", time_grain="week"),
         "explorer.err.calendar"),
        (dict(topic="daily", measures=[{"key": "steps"}], period="custom"),
         "explorer.err.custom_period"),
        (dict(topic="daily", measures=[{"key": "steps"}], facet="person"), "explorer.err.facet"),
    ]
    for kw, key in cases:
        with pytest.raises(SpecError) as ei:
            validate(S(**kw), MODEL)
        assert ei.value.key == key, kw
    # HRV method fixed by a single-value filter is fine (no mixing)
    validate(S(topic="hrv", measures=[{"key": "hrv"}],
               filters=[{"dimension": "hrv.method", "values": ["SDNN"]}]), MODEL)


def test_user_values_are_parameters_not_sql(household):
    evil = "x'); DROP TABLE dim_date; --"
    spec = S(topic="daily", measures=[{"key": "steps"}], persons=[evil],
             filters=[{"dimension": "date.day_of_week", "values": [evil]}])
    q = build_query(spec, MODEL, None, None)
    assert evil not in q.sql and q.params.count(evil) == 2
    assert run(household, spec, MODEL).df.empty
    assert household.execute("SELECT count(*) FROM dim_date").fetchall()[0][0] > 9000


# ---------------------------------------------------------------- correctness vs pandas

def test_weekly_aggregation_matches_pandas(household):
    res = run(household, S(topic="daily", measures=[{"key": "steps"}], dimensions=["person"],
                           time_grain="week"), MODEL)
    raw = household.execute("SELECT person_key, date, value FROM derived_person_daily "
                            "WHERE metric_key = 'steps'").df()
    raw["week"] = pd.to_datetime(raw["date"]).dt.to_period("W-SUN").dt.start_time
    ref = raw.groupby(["week", "person_key"])["value"].mean().reset_index()
    got = res.df.assign(period=pd.to_datetime(res.df["period"]))
    merged = got.merge(ref, left_on=["period", "d0"], right_on=["week", "person_key"])
    assert len(merged) == len(ref) == len(got)
    np.testing.assert_allclose(merged["m0"], merged["value"], rtol=1e-12)


@pytest.mark.parametrize("agg,fn", [("median", "median"), ("std", "std"), ("max", "max"),
                                    ("p90", lambda s: s.quantile(0.9)), ("count", "count")])
def test_aggregations_match_pandas(household, agg, fn):
    res = run(household, S(topic="daily", measures=[{"key": "resting_hr", "agg": agg}],
                           dimensions=["person", "date.weekend"], time_grain="none"), MODEL)
    raw = household.execute("""
        SELECT f.person_key, d.is_weekend, f.value FROM derived_person_daily f
        JOIN dim_date d USING (date_key) WHERE metric_key = 'resting_hr'""").df()
    ref = raw.groupby(["person_key", "is_weekend"])["value"].agg(fn).reset_index()
    ref["d1"] = np.where(ref["is_weekend"], "hétvége", "hétköznap")
    m = res.df.merge(ref, left_on=["d0", "d1"], right_on=["person_key", "d1"])
    assert len(m) == len(ref)
    np.testing.assert_allclose(m["m0"].astype(float), m["value"].astype(float), rtol=1e-12)


def test_transforms_per_series_match_pandas():
    df = pd.DataFrame({"period": list(pd.date_range("2024-01-01", periods=6)) * 2,
                       "d0": ["a"] * 6 + ["b"] * 6,
                       "m0": [1.0, 2, 4, 8, 16, 32, 10, 9, 8, 7, 6, 5]})
    for how, ref in [
        ("moving_avg", lambda s: s.rolling(3, min_periods=1).mean()),
        ("cumsum", lambda s: s.cumsum()),
        ("diff", lambda s: s.diff()),
        ("pct_change", lambda s: s.pct_change() * 100),
        ("index100", lambda s: s / s.iloc[0] * 100),
        ("zscore", lambda s: (s - s.mean()) / s.std()),
        ("minmax", lambda s: (s - s.min()) / (s.max() - s.min())),
    ]:
        spec = S(topic="daily", measures=[{"key": "steps"}], transform=how, transform_window=3)
        out = transform(df, spec, ["m0"], ["d0"])
        for key, g in df.groupby("d0"):
            got = out[out["d0"] == key]["m0"].to_numpy()
            np.testing.assert_allclose(got, ref(g["m0"]).to_numpy(), rtol=1e-12, equal_nan=True)


def test_value_filter_on_long_topic_only_filters_its_measure(household):
    base = run(household, S(topic="daily", measures=[{"key": "steps", "agg": "count"},
                                                     {"key": "resting_hr", "agg": "count"}],
                            time_grain="none"), MODEL).df
    filt = run(household, S(topic="daily", measures=[{"key": "steps", "agg": "count"},
                                                     {"key": "resting_hr", "agg": "count"}],
                            time_grain="none",
                            value_filters=[{"measure": "steps", "min": 8000}]), MODEL).df
    assert filt["m0"].iloc[0] < base["m0"].iloc[0]
    assert filt["m1"].iloc[0] == base["m1"].iloc[0]       # other metric untouched


def test_period_is_relative_to_last_data_day(household):
    res = run(household, S(topic="daily", measures=[{"key": "steps"}], period="last_7",
                           persons=["partner"]), MODEL)
    assert str(res.date_to) == "2024-04-08" and str(res.date_from) == "2024-04-02"
    assert len(res.df) == 7


def test_quality_toggle(household):
    on = run(household, S(topic="heart_rate", measures=[{"key": "bpm", "agg": "max"}],
                          time_grain="none"), MODEL).df["m0"].iloc[0]
    assert on <= 230


# ---------------------------------------------------------------- LTTB

def test_lttb_properties():
    rng = np.random.default_rng(0)
    x = np.arange(100_000)
    y = rng.normal(0, 1, len(x))
    y[54_321] = 50.0                                   # spike must survive
    idx = lttb(x, y, 1000)
    assert len(idx) == 1000 and idx[0] == 0 and idx[-1] == len(x) - 1
    assert np.all(np.diff(idx) > 0)
    assert 54_321 in idx
    assert np.array_equal(lttb(x[:10], y[:10], 100), np.arange(10))


def test_intraday_line_is_downsampled(con, tmp_path):
    con.execute("INSERT INTO dim_source (source_key, vendor, person_key) VALUES ('s', 'X', 'me')")
    con.execute("""INSERT INTO fact_heart_rate_sample
        SELECT 's', ts, ts, 60, CAST(strftime(ts, '%Y%m%d') AS INT),
               hour(ts) * 60 + minute(ts), 60 + (i % 50), 'rest', 'ok', 1
        FROM (SELECT TIMESTAMP '2024-01-01' + to_minutes(i) AS ts, i FROM range(60000) r(i))""")
    res = run(con, S(topic="heart_rate", measures=[{"key": "bpm", "agg": "none"}],
                     time_grain="raw", chart="line"), MODEL)
    assert res.downsampled_from == 60000 and len(res.df) == 20000


# ---------------------------------------------------------------- performance (SPEC 8.5)

def test_performance_targets(con):
    con.execute("INSERT INTO dim_source (source_key, vendor, person_key) VALUES ('s', 'X', 'me')")
    con.execute("""INSERT INTO derived_person_daily
        SELECT p, m, d, CAST(strftime(d, '%Y%m%d') AS INT), random() * 100, 's', 1
        FROM (SELECT DATE '2020-01-01' + CAST(i AS INTEGER) AS d FROM range(5 * 366) r(i)),
             (SELECT unnest(['steps','resting_hr','sleep_min','hr_avg','active_kcal',
                             'stress_avg','who_equiv_min','spo2_avg']) AS m),
             (SELECT unnest(['me', 'partner']) AS p)""")
    con.execute("""INSERT INTO fact_heart_rate_sample
        SELECT 's', ts, ts, 60, CAST(strftime(ts, '%Y%m%d') AS INT),
               hour(ts) * 60 + minute(ts), 50 + random() * 80, 'rest', 'ok', 1
        FROM (SELECT TIMESTAMP '2023-01-01' + to_minutes(i) AS ts FROM range(525600) r(i))""")
    run(con, S(topic="daily", measures=[{"key": "steps"}], time_grain="none"), MODEL)  # warm-up
    t0 = time.perf_counter()
    res = run(con, S(topic="daily", measures=[{"key": "steps"}, {"key": "resting_hr"},
                                               {"key": "sleep_hours"}],
                     dimensions=["person", "date.day_of_week"], time_grain="month"), MODEL)
    daily_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    res2 = run(con, S(topic="heart_rate", measures=[{"key": "bpm"}],
                      dimensions=["time.hour", "date.day_of_week"], time_grain="none",
                      chart="heatmap"), MODEL)
    intraday_s = time.perf_counter() - t0
    assert len(res.df) > 0 and len(res2.df) == 168
    assert daily_s < 1.0, daily_s
    assert intraday_s < 3.0, intraday_s


# ---------------------------------------------------------------- saved analyses (M5 acceptance)

def test_at_least_ten_builtin_analyses_run_and_render(household):
    items = [a for a in store.list_analyses(household) if a.is_builtin]
    assert len(items) >= 10
    for a in items:
        res = run(household, a.spec, MODEL)
        fig = build_figure(res)
        assert res.n_rows > 0, a.analysis_id
        assert len(fig.data) >= 1, a.analysis_id
        assert "SELECT" in res.query.sql


def test_store_crud_and_json_roundtrip(household):
    spec = S(topic="sleep", measures=[{"key": "asleep_h"}], time_grain="week")
    uid = store.save(household, "Saját alvás", spec, description="d", tags=["alvás", " x "])
    a = store.get(household, uid)
    assert a.name == "Saját alvás" and a.tags == ["alvás", "x"] and not a.is_builtin
    assert AnalysisSpec.model_validate_json(a.spec.model_dump_json()) == a.spec
    assert uid in {x.analysis_id for x in store.list_analyses(household, "x")}
    store.save(household, "Saját alvás 2", spec, analysis_id=uid)
    assert store.get(household, uid).name == "Saját alvás 2"
    dup = store.duplicate(household, "builtin:hr_heatmap")
    assert store.get(household, dup).name.endswith("(másolat)")
    with pytest.raises(store.StoreError):
        store.delete(household, "builtin:hr_heatmap")
    with pytest.raises(store.StoreError):
        store.save(household, "x", spec, analysis_id="builtin:hr_heatmap")
    with pytest.raises(store.StoreError):
        store.save(household, "  ", spec)
    store.delete(household, uid)
    store.delete(household, dup)
    with pytest.raises(store.StoreError):
        store.get(household, uid)


def test_builtins_survive_reseed_and_user_items_kept(household):
    uid = store.save(household, "Marad", S(topic="daily", measures=[{"key": "steps"}]))
    n = store.seed_builtins(household)
    assert n >= 10 and store.get(household, uid).name == "Marad"
    store.delete(household, uid)


def test_semantic_yaml_lives_in_config():
    assert (Path(__file__).resolve().parents[1] / "config" / "semantic.yaml").exists()
    assert len(MODEL.topics) >= 8
