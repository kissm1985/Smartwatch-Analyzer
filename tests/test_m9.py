"""M9: self-check report (value-free), end-to-end integration (SPEC 10.4), regressions found
on a real export (fast re-import, all-None lag profile, empty histogram, many bars)."""

from __future__ import annotations

import io
import re
import time
from datetime import date
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import plotly.graph_objects as go
from PIL import Image
from pypdf import PdfReader

from vitalmart import diagnose
from vitalmart.cli import main
from vitalmart.config import load_config
from vitalmart.datamart import db
from vitalmart.export import service, tabular
from vitalmart.export.figures import figure_png
from vitalmart.importers.service import run_import
from vitalmart.report import engine
from vitalmart.sample_data import SampleSpec, write_export_zip
from vitalmart.sample_data_garmin import GarminSpec
from vitalmart.sample_data_garmin import write_export_zip as write_garmin
from vitalmart.semantic import lag

# ---------------------------------------------------------------- diagnose

def test_mask_removes_values():
    msg = "Could not convert '72.5 bpm' at 2024-03-01 12:00 for \"Kiss István Watch\" (7 rows)"
    out = diagnose.mask(msg)
    assert "72" not in out and "2024" not in out and "István" not in out
    assert "Could not convert" in out


def test_diagnose_report_is_value_free_and_complete(tmp_path):
    z = write_export_zip(tmp_path / "a", SampleSpec(days=60))
    d = diagnose.run(z, person="partner", work_dir=tmp_path / "work")
    assert d.ok, [(s.name, s.error) for s in d.stages if not s.ok]
    names = [s.name for s in d.stages]
    assert names[:3] == ["preview", "import + rebuild", "idempotent re-import"]
    assert "report PDF" in names and "exports (XLSX, CSV, Parquet)" in names
    md = diagnose.to_markdown(d, z)
    assert "**rendben**" in md and "HKQuantityTypeIdentifierHeartRate" in md
    # no dates, no source / device names, no measured values
    assert not re.search(r"\b20\d\d[-.]\d\d", md)
    cfg = load_config(data_dir=str(tmp_path / "work" / "data"))
    con = db.connect(cfg)
    names_db = {r[0] for r in con.execute(
        "SELECT DISTINCT source_name FROM stg_apple_record").fetchall()}
    con.close()
    assert names_db and not any(n in md for n in names_db)


def test_diagnose_reports_failure_without_crashing(tmp_path):
    bad = tmp_path / "rossz.zip"
    bad.write_bytes(b"PK\x03\x04 not really a zip")
    d = diagnose.run(bad, work_dir=tmp_path / "w")
    assert not d.ok and d.stages[0].error
    assert "HIBA" in diagnose.to_markdown(d, bad)


def test_cli_diagnose(tmp_path, capsys):
    z = write_garmin(tmp_path / "g", GarminSpec())
    out = tmp_path / "diag.md"
    assert main(["diagnose", str(z), "--quick", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "garmin" in text and "report PDF" not in text
    assert main(["diagnose", str(tmp_path / "nincs.zip")]) == 2


# ---------------------------------------------------------------- integration (SPEC 10.4)

def test_end_to_end_import_rebuild_report_pdf_xlsx(tmp_path):
    d = tmp_path / "data"
    assert main(["--data-dir", str(d), "import", "-q", "--person", "partner",
                 str(write_export_zip(tmp_path / "a", SampleSpec()))]) == 0
    assert main(["--data-dir", str(d), "import", "-q", "--person", "me",
                 str(write_garmin(tmp_path / "g", GarminSpec()))]) == 0
    assert main(["--data-dir", str(d), "rebuild"]) == 0
    cfg = load_config(data_dir=str(d))
    con = db.connect(cfg)
    for person in ("partner", "me"):
        start, end = engine.default_period(con, person, 90)
        pdf = service.export_report_pdf(con, cfg, person, start, end)
        reader = PdfReader(pdf.path)
        assert len(reader.pages) >= 6 and "Tartalom" in reader.pages[1].extract_text()
    sel = tabular.Selection(tables=tabular.load_registry().default_tables)
    xl = service.export_raw(con, cfg, sel, "xlsx")
    wb = openpyxl.load_workbook(xl.path, read_only=True)
    assert {"Információ", "Adatszótár", "Mérőszámok", "Napi összesítő"} <= set(wb.sheetnames)
    assert len(service.list_history(con)) == 3
    con.close()


# ---------------------------------------------------------------- regressions from real data

def test_forced_reimport_is_fast_and_idempotent(tmp_path):
    cfg = load_config(data_dir=str(tmp_path / "data"))
    con = db.connect(cfg)
    z = write_export_zip(tmp_path / "a", SampleSpec(days=200, hr_interval_min=2))
    t0 = time.perf_counter()
    first = run_import(con, cfg, z)
    t_first = time.perf_counter() - t0
    counts = con.execute("SELECT count(*) FROM stg_apple_record").fetchall()
    t0 = time.perf_counter()
    again = run_import(con, cfg, z, force=True)
    t_again = time.perf_counter() - t0
    assert again.status == "success" and again.counts.new == 0
    assert again.counts.skipped == first.counts.read
    assert con.execute("SELECT count(*) FROM stg_apple_record").fetchall() == counts
    # an OR join here used to make this quadratic (minutes on real exports)
    assert t_again < max(3 * t_first, 5.0)
    con.close()


def test_lag_profile_all_none_stays_numeric():
    x = pd.Series([1.0, 2.0], index=pd.date_range("2024-01-01", periods=2))
    prof = lag.lag_profile(x, x, range(-2, 3))
    assert prof["spearman"].dtype == float and prof["spearman"].isna().all()
    assert lag.best_lag(prof, "spearman") is None
    figure_png(lag.profile_figure(prof, "spearman"))


def test_png_renderer_edge_cases():
    empty_hist = go.Figure(go.Histogram(x=[None, None]))
    assert Image.open(io.BytesIO(figure_png(empty_hist))).width > 0
    days = pd.date_range("2018-01-01", periods=3000)
    many = go.Figure([go.Bar(x=days, y=np.arange(3000), name="a"),
                      go.Bar(x=days, y=np.arange(3000)[::-1], name="b")])
    many.update_layout(barmode="stack")
    t0 = time.perf_counter()
    figure_png(many)
    assert time.perf_counter() - t0 < 3.0
    single = go.Figure(go.Heatmap(z=[[1.0], [2.0]], x=["a"], y=["x", "y"]))
    figure_png(single)


def test_report_with_very_short_watch_history(tmp_path):
    """Real case: long phone history, watch only a few days → sections say 'not enough
    data' instead of failing, PDF still builds."""
    cfg = load_config(data_dir=str(tmp_path / "data"))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(tmp_path / "a", SampleSpec(days=6)))
    start, end = engine.default_period(con, cfg.default_person, 90)
    rep = engine.build(con, cfg, cfg.default_person, start, end)
    assert not rep.section_map["sleep"].available
    assert "elegendő" in rep.section_map["sleep"].reason
    out = service.export_report_pdf(con, cfg, cfg.default_person, start, end)
    assert out.path.stat().st_size > 10_000
    assert date(2024, 1, 1) < end
    con.close()


def test_benchmark_tool_runs_small(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys_executable(), str(root / "tools" / "benchmark.py"), "--days", "20",
                        "--hr-interval", "10", "--work", str(tmp_path / "b")],
                       capture_output=True, text=True, timeout=600, cwd=root)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "| first import (+ core + derived) |" in r.stdout
    assert not (tmp_path / "b").exists()


def sys_executable() -> str:
    import sys
    return sys.executable
