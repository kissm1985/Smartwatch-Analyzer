"""M6: rule-based report – sections, rules, 'not enough data' branches, reproducibility."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from scipy.stats import spearmanr

from vitalmart.config import load_config
from vitalmart.datamart import db
from vitalmart.importers.service import run_import
from vitalmart.paths import CONFIG_DIR
from vitalmart.report import engine
from vitalmart.report.data import Period, ReportData
from vitalmart.report.rules import (
    confidence,
    correlations,
    evaluate,
    load_catalog,
    longest_run,
    social_jetlag,
)
from vitalmart.sample_data import SampleSpec, write_export_zip
from vitalmart.sample_data_garmin import GarminSpec
from vitalmart.sample_data_garmin import write_export_zip as write_garmin
from vitalmart.settings import save_settings

START, END = date(2024, 3, 1), date(2024, 5, 29)
SECTIONS = ["osszegzes", "sleep", "heart", "stress", "activity", "respiration", "body",
            "correlations", "anomalies", "fokusz", "modszertan", "nyilatkozat"]


@pytest.fixture(scope="module")
def hh(tmp_path_factory):
    d = tmp_path_factory.mktemp("rep")
    cfg = load_config(data_dir=str(d / "data"))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(d / "a", SampleSpec()), person="partner")
    run_import(con, cfg, write_garmin(d / "g", GarminSpec()), person="me")
    yield cfg, con
    con.close()


# ---------------------------------------------------------------- full report on the sample

def test_all_sections_render_on_sample(hh):
    cfg, con = hh
    rep, html, path = engine.generate(con, cfg, "partner", START, END,
                                      generated_at=datetime(2026, 11, 8, 12, 0))
    assert path.exists()
    for sid in SECTIONS:
        assert f'id="{sid}"' in html, sid
    assert "Egészségügyi riport" in html and "112" in html            # disclaimer
    assert html.count('class="js-plotly-plot"') == 0                   # plotted client-side
    assert len(re.findall(r'id="chart-[a-z_A-Z0-9]+"', html)) >= 10
    # no remote resources (SPEC 10.1): no CDN / external script or stylesheet
    assert not re.search(r'<(script|link)[^>]+(src|href)="https?://', html)
    assert "szabálykatalógus v1" in html and rep.data_version in html
    m = rep.section_map
    for key in ("sleep", "heart", "activity", "respiration", "body", "correlations"):
        assert m[key].available, key
    assert not m["stress"].available and "Garmin-specifikus" in m["stress"].reason
    ids = {f.rule_id for f in rep.findings}
    # the planted 8-day illness period is detected (resting HR up, HRV down)
    assert {"rhr_sustained_high", "hrv_sustained_low"} <= ids
    assert rep.focus and len(rep.focus) <= 5
    for f in rep.findings:                      # every finding: value, basis, confidence
        assert f.basis and f.confidence in ("magas", "közepes", "alacsony") and f.n > 0


def test_garmin_person_has_stress_section(hh):
    cfg, con = hh
    rng = engine.default_period(con, "me", 45)
    rep = engine.build(con, cfg, "me", *rng)
    assert rep.section_map["stress"].available
    assert not any(c.chart_id.startswith("hrv") for c in rep.section_map["heart"].charts)
    assert "nincs éjszakai HRV" in " ".join(rep.section_map["heart"].paragraphs)


def test_not_enough_data_branches(tmp_path):
    cfg = load_config(data_dir=str(tmp_path / "d"))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(tmp_path / "s", SampleSpec(days=10, hr_interval_min=60)),
               person="p")
    rng = engine.default_period(con, "p", 30)
    rep = engine.build(con, cfg, "p", *rng)
    m = rep.section_map
    for key in ("sleep", "heart", "activity", "respiration", "correlations"):
        assert not m[key].available, key
        assert m[key].reason
    assert "Nincs elegendő adat" in m["sleep"].reason and "10 érvényes nap" in m["sleep"].reason
    assert rep.findings == [] or all(f.section not in ("sleep", "heart") for f in rep.findings)
    html = engine.render_html(rep)
    assert html.count('class="na"') >= 5
    con.close()


def test_no_diagnostic_wording():
    cat = load_catalog(CONFIG_DIR)
    texts = " ".join(r["text"] + " " + r.get("focus", "") for r in cat.rules)
    assert not re.search(r"(Önnek|önnek)\s+\S*\s*(betegsége|zavara|rendellenessége)", texts)
    assert not re.search(r"\b(diagnosztizál|diagnózisa)\b", texts)
    for r in cat.rules:
        assert r["severity"] in ("info", "attention", "consult")
        assert r["source"] in cat.references, r["id"]


def test_rules_yaml_schema_and_review_status():
    raw = yaml.safe_load((CONFIG_DIR / "rules.yaml").read_text(encoding="utf-8"))
    assert raw["reviewed_by"] is None and raw["review_status"] == "not_requested"
    params = set(raw["parameters"])
    for r in raw["rules"]:
        for k in ("threshold", "share", "days"):
            if k in r:
                assert r[k]["param"] in params, (r["id"], k)
    ids = [r["id"] for r in raw["rules"]]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------- rule types (targeted data)

def _data(days: int = 60, **series: list[float] | np.ndarray) -> ReportData:
    end = date(2024, 6, 30)
    period = Period(end - timedelta(days=days - 1), end)
    cmp = period.previous()
    idx = pd.date_range(cmp.start, end, freq="D")
    daily = pd.DataFrame(index=idx)
    for k, v in series.items():
        arr = np.asarray(v, dtype=float)
        daily[k] = np.r_[np.full(len(idx) - len(arr), np.nan), arr]
    empty = pd.DataFrame()
    return ReportData("p", "p", period, cmp, daily, {}, pd.DataFrame(
        columns=["metric_key", "date", "z_score"]), pd.DataFrame(columns=["bedtime_std7"]),
        pd.DataFrame(), pd.DataFrame(), empty, empty, pd.DataFrame(columns=["vendor"]),
        empty)


def _ids(d: ReportData, cat=None) -> set[str]:
    return {f.rule_id for f in evaluate(d, cat or load_catalog(CONFIG_DIR))}


def test_rule_share_below_threshold_edges():
    short = [6 * 60] * 31 + [8 * 60] * 29          # 51.7 % short → fires
    assert "sleep_short_share" in _ids(_data(sleep_min=short))
    half = [6 * 60] * 30 + [8 * 60] * 30           # exactly 50 % → does not fire (> 50 %)
    assert "sleep_short_share" not in _ids(_data(sleep_min=half))
    good = [7.5 * 60] * 60
    assert "sleep_goal_good" in _ids(_data(sleep_min=good))


def test_rule_parameters_can_be_overridden(tmp_path):
    d = _data(sleep_min=[6.8 * 60] * 60)
    assert "sleep_short_share" in _ids(d)
    save_settings(tmp_path, {"rules_parameters": {"sleep_min_hours": 6.5}})
    assert "sleep_short_share" not in _ids(d, load_catalog(CONFIG_DIR, tmp_path))


def test_rule_mean_and_change():
    d = _data(resting_hr=[101.0] * 120)
    f = {x.rule_id: x for x in evaluate(d, load_catalog(CONFIG_DIR))}
    assert f["rhr_high_abs"].severity == "consult"
    assert "orvossal" in f["rhr_high_abs"].text and "101" in f["rhr_high_abs"].text
    d2 = _data(resting_hr=[60.0] * 60 + [57.0] * 60)          # previous 60, current 57
    f2 = {x.rule_id: x for x in evaluate(d2, load_catalog(CONFIG_DIR))}
    assert "rhr_improved" in f2 and f2["rhr_improved"].positive and "3,0 bpm" in \
        f2["rhr_improved"].text
    d3 = _data(steps=[10000.0] * 60 + [8000.0] * 60)
    f3 = {x.rule_id: x for x in evaluate(d3, load_catalog(CONFIG_DIR))}
    assert "-20,0 %" in f3["steps_change"].text


def test_rule_consecutive_z():
    d = _data(resting_hr=[60.0] * 60)
    dates = pd.date_range(d.period.start, d.period.end)
    z = np.zeros(len(dates))
    z[20:27] = 2.0                                          # 7 days >= 1.5
    d.stats = pd.DataFrame({"metric_key": "resting_hr", "date": dates, "z_score": z})
    f = [x for x in evaluate(d, load_catalog(CONFIG_DIR)) if x.rule_id == "rhr_sustained_high"]
    assert f and "7 egymást követő" in f[0].text and "2024.05.28." in f[0].text
    z[23] = 1.4                                             # broken run → no finding
    d.stats["z_score"] = z
    assert "rhr_sustained_high" not in _ids(d)


def test_rule_weekly_days_above_count_and_span():
    d = _data(spo2_sleep_min=[95.0] * 57 + [85.0] * 3, body_mass=[80.0] + [np.nan] * 58 + [77.5])
    d.weekly = pd.DataFrame({"days_with_data": [7] * 8, "who_equiv_min": [100.0] * 8,
                             "strength_days": [2] * 8})
    d.load = pd.DataFrame({"date": pd.date_range(d.period.start, periods=60),
                           "acwr": [1.0] * 56 + [1.8] * 4, "acwr_valid": [True] * 60})
    ids = _ids(d)
    assert {"who_below", "acwr_high", "spo2_low_nights", "weight_change"} <= ids
    assert "strength_below" not in ids and "who_met" not in ids


def test_sleep_regularity_and_social_jetlag():
    d = _data(days=28)
    dates = pd.date_range(d.cmp.start, d.period.end)
    weekend = dates.day_of_week >= 5
    d.daily["bedtime_min"] = np.where(weekend, 720.0, 600.0)     # 00:00 vs 22:00
    d.daily["waketime_min"] = np.where(weekend, 540.0, 420.0)    # 09:00 vs 07:00
    diff, n = social_jetlag(d)
    assert diff == pytest.approx(120.0) and n == 28
    assert "social_jetlag" in _ids(d)
    d.regularity = pd.DataFrame({"bedtime_std7": [75.0] * 28})
    assert "sleep_irregular" in _ids(d)


def test_confidence_and_longest_run():
    assert confidence(80, 90) == "magas"
    assert confidence(20, 90) == "alacsony"
    assert confidence(50, 90) == "közepes"
    assert confidence(14, 20) == "közepes"
    s = pd.Series([True, True, False, True, True, True],
                  index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
                                        "2024-01-05", "2024-01-07"]))
    assert longest_run(s)[0] == 2                                # gap on 01-06 breaks the run


def test_correlations_match_scipy_with_lag():
    rng = np.random.default_rng(3)
    sleep = rng.normal(420, 40, 120)
    steps = rng.normal(8000, 1500, 120)
    d = _data(sleep_min=sleep, steps=steps)
    cors = {c.corr_id: c for c in correlations(d, load_catalog(CONFIG_DIR))}
    c = cors["steps_sleep"]
    x = d.series("steps")
    y = d.series("sleep_min")
    y.index = y.index - pd.Timedelta(days=1)
    j = pd.concat([x, y], axis=1, sort=True).dropna()
    ref = spearmanr(j.iloc[:, 0], j.iloc[:, 1])
    assert c.n == len(j) and c.rho == pytest.approx(ref.statistic) and c.p == pytest.approx(
        ref.pvalue)


# ---------------------------------------------------------------- reproducibility (SPEC 7.5)

def test_reproducible_snapshot(tmp_path):
    cfg = load_config(data_dir=str(tmp_path / "d"))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(tmp_path / "s", SampleSpec(days=60, hr_interval_min=30)),
               person="p")
    ts = datetime(2026, 11, 8, 9, 30)
    _r1, h1, _ = engine.generate(con, cfg, "p", date(2024, 3, 1), date(2024, 4, 14),
                                 generated_at=ts, save_snapshot=True)
    _r2, h2, _ = engine.generate(con, cfg, "p", date(2024, 3, 1), date(2024, 4, 14),
                                 generated_at=ts, save_snapshot=False)
    assert h1 == h2
    sid = engine.list_snapshots(con)["snapshot_id"].iloc[0]
    _rep, h3, same = engine.regenerate(con, cfg, sid)
    assert same and h3 == h1
    # changed rule parameter → same data version, but output differs
    save_settings(cfg.data_path, {"rules_parameters": {"min_valid_days": 50}})
    _rep, _h4, same2 = engine.regenerate(con, cfg, sid)
    assert not same2
    con.close()


# ---------------------------------------------------------------- CLI / UI

def test_cli_report(tmp_path, capsys):
    from vitalmart.cli import main

    d = tmp_path / "data"
    main(["--data-dir", str(d), "import", "-q", "--person", "me",
          str(write_export_zip(tmp_path / "s", SampleSpec(days=40, hr_interval_min=60)))])
    out = tmp_path / "r.html"
    assert main(["--data-dir", str(d), "report", "--person", "me", "--days", "30",
                 "--out", str(out)]) == 0
    assert out.exists() and "Mentve" in capsys.readouterr().out
    assert main(["--data-dir", str(d), "report", "--person", "nobody"]) == 1


def test_report_page(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    d = tmp_path / "data"
    monkeypatch.setenv("VITALMART_DATA_DIR", str(d))
    cfg = load_config()
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(tmp_path / "s", SampleSpec(days=40, hr_interval_min=60)),
               person="me")
    con.close()
    from vitalmart.ui.common import _connection

    _connection.clear()
    page = Path(__file__).resolve().parents[1] / "app" / "views" / "3_Riport.py"
    at = AppTest.from_file(str(page), default_timeout=120).run()
    assert not at.exception, at.exception
    at.button(key="rp_generate").click().run()
    assert not at.exception, at.exception
    assert "Egészségügyi riport" in at.session_state["rp_html"]
    assert any("Mentve" in s.value for s in at.success)
