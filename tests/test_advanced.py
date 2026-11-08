"""M8 acceptance tests for the Explorer's advanced features: events / annotations, lag
correlation, comparison mode, saved views, read-only SQL console. Synthetic data only."""

from __future__ import annotations

import io
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image
from scipy import stats
from streamlit.testing.v1 import AppTest

from vitalmart import events as ev
from vitalmart import sqlconsole as sc
from vitalmart.config import load_config
from vitalmart.datamart import db
from vitalmart.export.figures import figure_png
from vitalmart.importers.service import run_import
from vitalmart.sample_data import SampleSpec, write_export_zip
from vitalmart.sample_data_garmin import GarminSpec
from vitalmart.sample_data_garmin import write_export_zip as write_garmin
from vitalmart.semantic import compare as cp
from vitalmart.semantic import lag, store
from vitalmart.semantic import views as vw
from vitalmart.semantic.charts import build_figure
from vitalmart.semantic.engine import run
from vitalmart.semantic.model import load_model
from vitalmart.semantic.spec import AnalysisSpec

APP = Path(__file__).resolve().parents[1] / "app"
ILL = (date(2024, 3, 31), date(2024, 4, 8))          # synthetic illness period of the sample


@pytest.fixture(scope="module")
def hh(tmp_path_factory):
    d = tmp_path_factory.mktemp("m8")
    cfg = load_config(data_dir=str(d / "data"))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(d / "a", SampleSpec()), person="partner")
    run_import(con, cfg, write_garmin(d / "g", GarminSpec()), person="me")
    yield cfg, con
    con.close()


@pytest.fixture
def clean_events(hh):
    _cfg, con = hh
    con.execute("DELETE FROM dim_event")
    yield con
    con.execute("DELETE FROM dim_event")


def spec(**kw) -> AnalysisSpec:
    return AnalysisSpec.model_validate(kw)


def daily(con, person: str, metric: str) -> pd.Series:
    df = con.execute("""SELECT date, value FROM derived_person_daily
                        WHERE person_key = ? AND metric_key = ? ORDER BY date""",
                     [person, metric]).df()
    return pd.Series(df["value"].values, index=pd.to_datetime(df["date"]))


# ---------------------------------------------------------------- events

def test_event_crud_and_validation(clean_events):
    con = clean_events
    eid = ev.add_event(con, ILL[0], ILL[1], " Influenza ", category="betegség",
                       person="partner", note="szabad szöveg")
    e = ev.get_event(con, eid)
    assert (e.label, e.days, e.person_key, e.category) == ("Influenza", 9, "partner", "betegség")
    one_day = ev.add_event(con, date(2024, 5, 1), None, "Kávémentes nap")
    assert ev.get_event(con, one_day).end_date == date(2024, 5, 1)
    ev.update_event(con, eid, ILL[0], ILL[1] + timedelta(days=1), "Influenza",
                    category="betegség", person="partner")
    assert ev.get_event(con, eid).days == 10
    with pytest.raises(ev.EventError, match="nevet"):
        ev.add_event(con, ILL[0], ILL[1], "  ")
    with pytest.raises(ev.EventError, match="korábbi"):
        ev.add_event(con, ILL[1], ILL[0], "x")
    with pytest.raises(ev.EventError):
        ev.add_event(con, ILL[0], ILL[1], "x", person="nincs")
    # household-wide events belong to everyone, person events only to that person
    assert {x.label for x in ev.list_events(con, ["me"])} == {"Kávémentes nap"}
    assert len(ev.list_events(con, ["partner"])) == 2
    assert [x.label for x in ev.list_events(con, None, date(2024, 4, 9), date(2024, 4, 30))] \
        == ["Influenza"]
    assert "betegség" in ev.categories(con)
    ev.delete_event(con, one_day)
    with pytest.raises(ev.EventError):
        ev.get_event(con, one_day)


def test_event_day_view_overlaps_and_household(clean_events):
    con = clean_events
    ev.add_event(con, date(2024, 4, 1), date(2024, 4, 3), "Utazás", category="utazás")
    ev.add_event(con, date(2024, 4, 3), date(2024, 4, 4), "Betegség", person="partner")
    rows = con.execute("""SELECT person_key, date, labels FROM event_day
                          ORDER BY person_key, date""").fetchall()
    by = {(p, d): lab for p, d, lab in rows}
    assert by[("partner", date(2024, 4, 3))] == "Betegség, Utazás"
    assert by[("me", date(2024, 4, 3))] == "Utazás"
    assert ("me", date(2024, 4, 4)) not in by and len(rows) == 3 + 4


def test_event_dimension_matches_pandas_reference(hh, clean_events):
    _cfg, con = hh
    model = load_model()
    ev.add_event(con, ILL[0], ILL[1], "Influenza", category="betegség", person="partner")
    s = spec(topic="daily", measures=[{"key": "resting_hr"}], dimensions=["event.active"],
             persons=["partner"], time_grain="none", period="all")
    res = run(con, s, model)
    got = dict(zip(res.df["d0"], res.df["m0"], strict=True))
    ref = daily(con, "partner", "resting_hr")
    inside = (ref.index >= pd.Timestamp(ILL[0])) & (ref.index <= pd.Timestamp(ILL[1]))
    assert got["esemény alatt"] == pytest.approx(ref[inside].mean())
    assert got["egyébként"] == pytest.approx(ref[~inside].mean())
    assert got["esemény alatt"] > got["egyébként"]          # synthetic illness raises RHR
    # as a filter, on an intraday topic (join through dim_source for the person)
    f = spec(topic="heart_rate", measures=[{"key": "bpm"}], persons=["partner"],
             time_grain="day", period="all", chart="table",
             filters=[{"dimension": "event.label", "values": ["Influenza"]}])
    days = pd.to_datetime(run(con, f, model).df["period"])
    assert len(days) and days.min() >= pd.Timestamp(ILL[0]) and days.max() <= pd.Timestamp(ILL[1])
    # weekly topic has no event dimension, every other topic does
    for k in model.topics:
        assert ("event.label" in model.topic_dimensions(k)) == (k != "weekly"), k


def test_event_bands_on_time_charts_and_png(hh, clean_events):
    _cfg, con = hh
    ev.add_event(con, ILL[0], ILL[1], "Influenza", person="partner")
    ev.add_event(con, date(2024, 10, 1), date(2024, 10, 2), "Csak az enyém", person="me")
    s = spec(topic="daily", measures=[{"key": "resting_hr"}], persons=["partner"],
             time_grain="day", period="all", chart="line")
    res = run(con, s)
    assert [e.label for e in res.events] == ["Influenza"]
    fig = build_figure(res)
    rects = [sh for sh in fig.layout.shapes if sh.type == "rect"]
    assert len(rects) == 1 and rects[0].name == "Influenza"
    assert pd.Timestamp(rects[0].x1) == pd.Timestamp(ILL[1]) + pd.Timedelta(days=1)
    assert Image.open(io.BytesIO(figure_png(fig))).width > 1000
    # small multiples: one band per panel
    facet = s.model_copy(update={"persons": [], "dimensions": ["person"], "facet": "person"})
    assert len([x for x in build_figure(run(con, facet)).layout.shapes if x.type == "rect"]) \
        == 2 * 2
    off = s.model_copy(update={"show_events": False})
    assert not run(con, off).events and not build_figure(run(con, off)).layout.shapes
    agg = s.model_copy(update={"time_grain": "none", "chart": "bar"})
    assert not run(con, agg).events


# ---------------------------------------------------------------- lag correlation

def test_lag_finds_known_shift_and_matches_scipy():
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    x = pd.Series(rng.normal(size=200), index=idx)
    y = pd.Series(np.roll(x.values, 2) + rng.normal(scale=0.5, size=200), index=idx)
    y.iloc[:2] = np.nan                    # y[t + 2] = x[t] + noise
    prof = lag.lag_profile(x, y.dropna(), range(-5, 6))
    assert lag.best_lag(prof, "pearson") == 2 and lag.best_lag(prof, "spearman") == 2
    st = lag.lag_stat(x, y.dropna(), 2)
    j = pd.concat([x[:-2].reset_index(drop=True), y[2:].reset_index(drop=True)], axis=1).dropna()
    assert st.n == len(j) == 198
    assert st.pearson == pytest.approx(stats.pearsonr(j[0], j[1]).statistic)
    assert st.spearman == pytest.approx(stats.spearmanr(j[0], j[1]).statistic)
    assert st.pearson_lo < st.pearson < st.pearson_hi
    z, se = math.atanh(st.spearman), math.sqrt(1.06 / (st.n - 3))
    assert st.spearman_lo == pytest.approx(math.tanh(z - 1.959964 * se), abs=1e-6)
    assert lag.lag_stat(x, y.dropna(), 0).pearson < 0.2
    text = lag.describe(st, "spearman", "X", "Y")
    assert "2 nappal később" in text and "n = 198" in text and "ok-okozati" in text
    assert "erős pozitív" in text
    short = lag.lag_stat(x[:5], y[:5], 0)
    assert short.pearson is None and "Nincs elegendő" in lag.describe(short, "pearson", "X", "Y")


def test_lag_series_from_semantic_layer_and_report_convention(hh):
    _cfg, con = hh
    model = load_model()
    x = lag.daily_series(con, model, lag.SeriesRef("daily", "steps"), "partner")
    y = lag.daily_series(con, model, lag.SeriesRef("daily", "sleep_hours"), "partner")
    ref = daily(con, "partner", "steps")
    assert list(x.index) == list(ref.index) and np.allclose(x.values, ref.values)
    # same convention as the report: y shifted so that x[t] pairs with y[t + lag]
    p = lag.pairs(x, y, 1)
    t0 = p.index[0]
    assert p.loc[t0, "y"] == pytest.approx(y[t0 + pd.Timedelta(days=1)])
    assert "weekly" not in lag.lag_topics(model) and "hrv" not in lag.lag_topics(model)
    fig = lag.profile_figure(lag.lag_profile(x, y, range(-3, 4)), "spearman", 1)
    assert len(fig.data[0].x) == 7
    figure_png(fig)
    figure_png(lag.scatter_figure(x, y, 1, "Lépés", "Alvás"))


# ---------------------------------------------------------------- comparison mode

def test_compare_presets():
    a, b = cp.preset("previous", date(2024, 5, 29), 30)
    assert (b.start, b.end, a.start, a.end) == (date(2024, 4, 30), date(2024, 5, 29),
                                                 date(2024, 3, 31), date(2024, 4, 29))
    a, b = cp.preset("month", date(2024, 3, 15))
    assert (a.start, a.end, b.start, b.end) == (date(2024, 2, 1), date(2024, 2, 29),
                                                 date(2024, 3, 1), date(2024, 3, 15))
    a, b = cp.preset("year", date(2024, 2, 29), 7)
    assert b.days == a.days == 7 and a.end.year == 2023


def test_compare_matches_pandas_reference(hh):
    _cfg, con = hh
    model = load_model()
    a = cp.PeriodDef("A", date(2024, 3, 1), date(2024, 3, 30))
    b = cp.PeriodDef("B", ILL[0], ILL[1])
    s = spec(topic="daily", measures=[{"key": "resting_hr"}, {"key": "steps"}],
             persons=["partner"])
    c = cp.compare(con, s, model, a, b)
    for metric, row in zip(("resting_hr", "steps"), c.table.itertuples(), strict=True):
        ref = daily(con, "partner", metric)
        ra = ref[pd.Timestamp(a.start):pd.Timestamp(a.end)]
        rb = ref[pd.Timestamp(b.start):pd.Timestamp(b.end)]
        assert row.a == pytest.approx(ra.mean()) and row.b == pytest.approx(rb.mean())
        assert row.diff == pytest.approx(rb.mean() - ra.mean())
        assert row.diff_pct == pytest.approx((rb.mean() - ra.mean()) / ra.mean() * 100)
        assert (row.n_a, row.n_b) == (len(ra), len(rb))
        sp = math.sqrt(((len(ra) - 1) * ra.var() + (len(rb) - 1) * rb.var())
                       / (len(ra) + len(rb) - 2))
        assert row.d == pytest.approx((rb.mean() - ra.mean()) / sp)
    assert c.table.iloc[0]["diff"] > 0                       # illness: higher RHR
    assert set(c.daily["period"]) == {"A", "B"} and c.daily["day"].min() == 1
    tbl = cp.display_table(c)
    assert list(tbl.columns[:3]) == ["Mérőszám", "A", "B"]
    figure_png(cp.bar_figure(c))
    figure_png(cp.overlay_figure(c))
    # with a dimension (weekday / weekend) and a sum measure
    s2 = spec(topic="daily", measures=[{"key": "steps", "agg": "sum"}],
              dimensions=["date.weekend"], persons=["partner"])
    c2 = cp.compare(con, s2, model, a, b)
    ref = daily(con, "partner", "steps")[pd.Timestamp(a.start):pd.Timestamp(a.end)]
    wk = ref.groupby(ref.index.day_of_week >= 5).sum()
    got = dict(zip(c2.table["d0"], c2.table["a"], strict=True))
    assert got["hétvége"] == pytest.approx(wk[True]) and got["hétköznap"] == pytest.approx(wk[False])
    assert c2.daily.empty
    figure_png(cp.bar_figure(c2))


# ---------------------------------------------------------------- saved views

def test_saved_views_crud_overrides_and_missing_items(hh):
    _cfg, con = hh
    model = load_model()
    user_id = store.save(con, "Saját", spec(topic="daily", measures=[{"key": "steps"}]))
    items = ["builtin:rhr_trend", "builtin:sleep_by_weekday", user_id]
    vid = vw.save_view(con, "Heti áttekintés", items, columns=2, period="last_30",
                       persons=["partner"])
    v = vw.get_view(con, vid)
    assert (v.items, v.columns, v.period, v.persons) == (items, 2, "last_30", ["partner"])
    out = vw.run_view(con, v, model)
    assert [i.error for i in out] == [None, None, None]
    for item in out:
        assert item.spec.period == "last_30" and item.spec.persons == ["partner"]
        assert item.result.date_to - item.result.date_from == timedelta(days=29)
    vw.save_view(con, "Átnevezve", items[:1], view_id=vid, columns=5)
    assert vw.get_view(con, vid).columns == 3 and vw.get_view(con, vid).period is None
    keep = vw.run_view(con, vw.get_view(con, vid), model)[0]
    assert keep.spec.period == store.get(con, items[0]).spec.period
    vw.save_view(con, "Átnevezve", [items[0], user_id], view_id=vid)
    store.delete(con, user_id)
    out = vw.run_view(con, vw.get_view(con, vid), model)
    assert out[1].error == "views.err.missing_item" and out[0].result is not None
    for bad, key in ((("", items), "views.err.name"), (("x", []), "views.err.items"),
                     (("x", ["nincs"]), "views.err.unknown_analysis"),
                     (("x", items[:1] * 13), "views.err.too_many")):
        with pytest.raises(vw.ViewError) as exc:
            vw.save_view(con, *bad)
        assert exc.value.key == key
    vw.delete_view(con, vid)
    assert vid not in {x.view_id for x in vw.list_views(con)}


# ---------------------------------------------------------------- SQL console

@pytest.mark.parametrize("sql", [
    "DELETE FROM dim_person", "DROP TABLE fact_activity", "UPDATE dim_event SET label = 'x'",
    "INSERT INTO dim_event (start_date, label) VALUES ('2024-01-01', 'x')",
    "CREATE TABLE fact_x AS SELECT 1", "COPY fact_activity TO 'x.csv'", "ATTACH 'x.db'",
    "PRAGMA database_list", "SELECT 1; DELETE FROM dim_person",
    "SELECT * FROM stg_apple_record", "SELECT * FROM saved_analysis",
    "SELECT * FROM read_csv('x.csv')", "SELECT * FROM 'x.parquet'",
    "SELECT * FROM other.main.dim_date", "SELECT nextval('seq_event')",
    "SELECT * FROM dim_date WHERE date_key IN (SELECT import_batch_id FROM stg_garmin_record)",
    "", "SELEC 1",
])
def test_sql_console_rejects_unsafe(hh, sql):
    _cfg, con = hh
    before = con.execute("SELECT count(*) FROM dim_person").fetchall()
    with pytest.raises(sc.SqlError):
        sc.execute(con, sql)
    assert con.execute("SELECT count(*) FROM dim_person").fetchall() == before


def test_sql_console_runs_examples_limits_and_timeout(hh):
    _cfg, con = hh
    for text in sc.EXAMPLES.values():
        assert not sc.execute(con, text).df.empty
    r = sc.execute(con, "SELECT * FROM fact_heart_rate_sample;", max_rows=100)
    assert r.truncated and len(r.df) == 100 and "bpm" in r.df.columns
    r = sc.execute(con, "WITH x AS (SELECT * FROM dim_person) SELECT count(*) AS n FROM x")
    assert r.df["n"].iloc[0] == 2 and not r.truncated
    with pytest.raises(sc.SqlError) as exc:
        sc.execute(con, "SELECT count(*) FROM range(100000000) a, range(100000) b",
                   timeout_s=0.5)
    assert exc.value.key == "sql.err.timeout"
    assert sc.execute(con, "SELECT 1 AS ok").df["ok"].iloc[0] == 1     # connection still usable
    sch = sc.schema(con)
    assert not sch["table"].str.startswith("stg_").any()
    assert {"event_day", "fact_activity", "derived_person_daily"} <= set(sch["table"])
    assert sch[(sch["table"] == "fact_activity") & (sch["column"] == "hr_avg")]["unit"].iloc[0] \
        == "bpm"


# ---------------------------------------------------------------- UI

@pytest.fixture
def ui_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("VITALMART_DATA_DIR", str(d))
    cfg = load_config(data_dir=str(d))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(tmp_path / "a", SampleSpec(days=70)), person="partner")
    ev.add_event(con, date(2024, 3, 31), date(2024, 4, 8), "Influenza", person="partner")
    con.close()
    return d


def _page(name: str) -> AppTest:
    return AppTest.from_file(str(APP / "views" / name), default_timeout=180)


def _db(d: Path):
    cfg = load_config(data_dir=str(d))
    return db.connect(cfg)


def test_events_page_add_and_delete(ui_dir):
    at = _page("8_Esemenyek.py").run()
    assert not at.exception
    at.text_input(key="evn_label").set_value("Utazás")
    at.button[0].click().run()            # form submit
    assert not at.exception
    con = _db(ui_dir)
    assert {e.label for e in ev.list_events(con)} == {"Influenza", "Utazás"}
    con.close()
    at.button(key="ev_delete").click().run()
    assert not at.exception
    con = _db(ui_dir)
    assert len(ev.list_events(con)) == 1
    con.close()


def test_advanced_page_lag_and_event_comparison(ui_dir):
    at = _page("9_Halado.py").run()
    assert not at.exception
    assert any("ok-okozati" in i.value for i in at.info)
    at.radio(key="cmp_kind").set_value("event").run()
    assert not at.exception
    assert any("Influenza" in c.value for c in at.caption)
    assert len(at.dataframe) >= 2


def test_views_page_create_and_render(ui_dir):
    at = _page("10_Nezetek.py").run()
    assert not at.exception
    at.text_input(key="vw_name_new").set_value("Reggeli nézet")
    at.multiselect(key="vw_items_new").set_value(["builtin:rhr_trend", "builtin:steps_weekly"])
    at.button(key="vw_save_new").click().run()
    assert not at.exception
    assert at.selectbox(key="vw_select").value.startswith("view:")
    assert not at.warning


def test_explorer_event_dimension_and_toggle(ui_dir):
    at = _page("4_Elemzo.py").run()
    assert not at.exception
    at.checkbox(key="ex_events").uncheck().run()
    assert not at.exception


def test_sql_page_runs_and_blocks(ui_dir):
    at = _page("11_SQL.py").run()
    at.button(key="sql_run").click().run()
    assert not at.exception and len(at.dataframe) >= 2
    at.text_area(key="sql_text").set_value("DELETE FROM dim_person").run()
    at.button(key="sql_run").click().run()
    assert any("Csak SELECT" in e.value for e in at.error)
