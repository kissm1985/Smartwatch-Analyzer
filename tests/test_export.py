"""M7: PDF (report + analysis), XLSX (sheets, dictionary, row limit), CSV/Parquet, export
history with regeneration, UI buttons and CLI. Synthetic data only."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path

import openpyxl
import pandas as pd
import pyarrow.parquet as pq
import pytest
from PIL import Image
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

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
from vitalmart.semantic import store
from vitalmart.semantic.charts import build_figure
from vitalmart.semantic.engine import run
from vitalmart.semantic.spec import AnalysisSpec

APP = Path(__file__).resolve().parents[1] / "app"
PARTNER = ("partner", date(2024, 3, 1), date(2024, 5, 29))


@pytest.fixture(scope="module")
def hh(tmp_path_factory):
    d = tmp_path_factory.mktemp("exp")
    cfg = load_config(data_dir=str(d / "data"))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(d / "a", SampleSpec()), person="partner")
    run_import(con, cfg, write_garmin(d / "g", GarminSpec()), person="me")
    yield cfg, con
    con.close()


def _pdf_text(path: Path) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(path).pages)


# ---------------------------------------------------------------- charts → PNG

def test_every_report_and_explorer_chart_renders_at_200_dpi(hh):
    cfg, con = hh
    rep = engine.build(con, cfg, *PARTNER)
    figs = [c.fig for s in rep.sections for c in s.charts]
    figs += [build_figure(run(con, a.spec)) for a in store.list_analyses(con)
             if a.spec.chart != "table"]
    assert len(figs) > 25
    for fig in figs:
        img = Image.open(io.BytesIO(figure_png(fig)))
        assert round(img.info["dpi"][0]) == 200
        assert img.width >= 600           # ≥ 3 inch wide at 200 dpi (radar is square)


# ---------------------------------------------------------------- report PDF

def test_report_pdf_cover_toc_accents_pages_metadata(hh):
    cfg, con = hh
    out = service.export_report_pdf(con, cfg, *PARTNER)
    reader = PdfReader(out.path)
    n = len(reader.pages)
    assert n >= 6
    meta = reader.metadata
    assert "Egészségügyi riport" in meta.title and meta.author and meta.subject
    assert PdfReader(out.path).pages[0].mediabox.width == pytest.approx(595.28, abs=1)  # A4
    text = _pdf_text(out.path)
    for s in ("Tartalom", "Vezetői összegzés", "Szív és HRV", "Légzés és SpO₂",
              "Módszertan és korlátok", "Jogi / orvosi nyilatkozat", "Nem orvosi diagnózis",
              f"2 / {n}. oldal", "szabálykatalógus v"):
        assert s in text, s
    # table of contents: section headings with page numbers + PDF outline (bookmarks)
    toc_page = reader.pages[1].extract_text()
    assert "Alvás" in toc_page and "Összefüggések" in toc_page
    assert [o.title for o in reader.outline][:2] == ["Vezetői összegzés", "Alvás"]
    # embedded Unicode font, charts as images
    fonts = {f["/BaseFont"] for p in reader.pages
             for f in (p["/Resources"].get("/Font") or {}).values()}
    assert any("DejaVuSans" in str(f) for f in fonts)
    assert sum(len(p.images) for p in reader.pages) >= 10


def test_report_pdf_regeneration_is_byte_identical(hh):
    cfg, con = hh
    first = service.export_report_pdf(con, cfg, *PARTNER)
    again = service.regenerate(con, cfg, first.export_id)
    assert again.path != first.path
    assert hashlib.sha256(again.path.read_bytes()).digest() == \
        hashlib.sha256(first.path.read_bytes()).digest()


# ---------------------------------------------------------------- analysis exports

def test_analysis_pdf_xlsx_csv(hh):
    cfg, con = hh
    a = store.get(con, "builtin:daily_correlations")
    note = "Saját megjegyzés: árvíztűrő tükörfúrógép"
    pdf = service.export_analysis(con, cfg, a.spec, "pdf", title=a.name, note=note)
    text = _pdf_text(pdf.path)
    for s in (a.name, note, "Paraméterek", "Téma", "Adatok", "Nem orvosi diagnózis"):
        assert s in text, s
    assert "export." not in text                       # no missing i18n keys
    assert len(PdfReader(pdf.path).pages[0].images) == 1

    xl = service.export_analysis(con, cfg, a.spec, "xlsx", title=a.name, note=note)
    wb = openpyxl.load_workbook(xl.path)
    assert wb.sheetnames == ["Eredmény", "Leírás"]
    res = wb["Eredmény"]
    assert res.max_row == xl.rows + 1 and res.freeze_panes == "A2"
    assert any("[lépés]" in str(c.value) for c in res[1])
    desc = "\n".join(str(c.value) for row in wb["Leírás"].iter_rows() for c in row if c.value)
    assert note in desc and "SELECT" in desc and '"topic"' in desc

    csv = service.export_analysis(con, cfg, a.spec, "csv", title=a.name)
    df = pd.read_csv(csv.path)
    assert len(df) == xl.rows and "Lépésszám [lépés]" in df.columns


def test_analysis_pdf_cuts_long_tables(hh, monkeypatch):
    from vitalmart.export import pdf as pdf_mod

    monkeypatch.setattr(pdf_mod, "MAX_TABLE_ROWS", 50)
    cfg, con = hh
    spec = AnalysisSpec.model_validate({"topic": "daily", "measures": [{"key": "steps"}],
                                        "dimensions": ["person"], "time_grain": "day",
                                        "period": "all", "chart": "table"})
    pdf = service.export_analysis(con, cfg, spec, "pdf", title="Hosszú")
    assert pdf.rows > 50
    assert "az első 50 sort mutatja" in _pdf_text(pdf.path).replace("\n", " ")


# ---------------------------------------------------------------- raw XLSX

def test_raw_xlsx_sheets_headers_formats_dictionary(hh):
    cfg, con = hh
    sel = tabular.Selection(tables=["fact_daily_summary", "fact_sleep_session",
                                    "derived_person_daily"], persons=["me"],
                            date_from=date(2024, 10, 1), date_to=date(2024, 10, 31))
    out = service.export_raw(con, cfg, sel, "xlsx")
    wb = openpyxl.load_workbook(out.path)
    assert wb.sheetnames == ["Információ", "Napi összesítő", "Alvás", "Napi idősorok",
                             "Adatszótár", "Mérőszámok"]
    ws = wb["Napi összesítő"]
    head = [c.value for c in ws[1]]
    assert head[:2] == ["Dátum", "Személy"] and "Lépésszám [lépés]" in head
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:")
    assert ws["A2"].number_format == "yyyy.mm.dd."
    assert ws.max_row - 1 == out.rows - (wb["Alvás"].max_row - 1) - \
        (wb["Napi idősorok"].max_row - 1)
    assert {ws.cell(r, 2).value for r in range(2, ws.max_row + 1)} == {"me"}
    dates = [ws.cell(r, 1).value.date() for r in range(2, ws.max_row + 1)]
    assert min(dates) >= date(2024, 10, 1) and max(dates) <= date(2024, 10, 31)
    sleep = wb["Alvás"]
    start_col = [c.value for c in sleep[1]].index("Kezdet (UTC)") + 1
    assert sleep.cell(2, start_col).number_format == "yyyy.mm.dd. hh:mm:ss"
    # data dictionary covers every exported column, with units and descriptions
    dic = list(wb["Adatszótár"].iter_rows(values_only=True))
    assert dic[0] == ("Munkalap", "Tábla", "Fejléc", "Technikai név", "Mértékegység",
                      "Adattípus", "Leírás")
    for name in ("Napi összesítő", "Alvás", "Napi idősorok"):
        headers = {c.value for c in wb[name][1]}
        assert headers == {r[2] for r in dic[1:] if r[0] == name}
    assert all(r[6] for r in dic[1:])
    assert wb["Mérőszámok"].max_row > 30
    info = "\n".join(str(c.value) for r in wb["Információ"].iter_rows() for c in r if c.value)
    assert "2024.10.01. – 2024.10.31." in info and "Nem orvosi diagnózis" in info


def test_xlsx_row_limit_splits_into_sheets_with_warning(hh):
    cfg, con = hh
    sel = tabular.Selection(tables=["fact_heart_rate_sample"], persons=["me"])
    total = tabular.count_rows(con, tabular.load_registry(), sel)["fact_heart_rate_sample"]
    out = service.export_raw(con, cfg, sel, "xlsx", max_rows=total // 3 + 1)
    wb = openpyxl.load_workbook(out.path, read_only=True)
    parts = [n for n in wb.sheetnames if n.startswith("Pulzusminták")]
    assert parts == ["Pulzusminták", "Pulzusminták (2)", "Pulzusminták (3)"]
    assert sum(wb[n].max_row - 1 for n in parts) == total == out.rows
    assert out.warnings and "3 munkalapra" in out.warnings[0]
    hist = service.list_history(con)
    assert "3 munkalapra" in hist[hist.export_id == out.export_id].warnings.iloc[0]


# ---------------------------------------------------------------- CSV / Parquet

def test_csv_and_parquet_single_and_zip(hh):
    cfg, con = hh
    reg = tabular.load_registry()
    one = tabular.Selection(tables=["fact_activity"], persons=["partner"])
    out = service.export_raw(con, cfg, one, "csv")
    df = pd.read_csv(out.path)
    assert out.path.suffix == ".csv" and len(df) == out.rows > 0
    assert set(df["person_key"]) == {"partner"} and "activity_name" in df.columns

    many = tabular.Selection(tables=["fact_activity", "fact_hrv_nightly"],
                             date_from=date(2024, 4, 1), date_to=date(2024, 4, 30))
    z = service.export_raw(con, cfg, many, "parquet")
    counts = tabular.count_rows(con, reg, many)
    with zipfile.ZipFile(z.path) as zf:
        assert sorted(zf.namelist()) == ["adatszotar.csv", "fact_activity.parquet",
                                         "fact_hrv_nightly.parquet"]
        for key in many.tables:
            tbl = pq.read_table(io.BytesIO(zf.read(f"{key}.parquet")))
            assert tbl.num_rows == counts[key]
        hrv = pq.read_table(io.BytesIO(zf.read("fact_hrv_nightly.parquet"))).to_pandas()
        assert set(hrv["hrv_method"]) <= {"RMSSD", "SDNN"}
        assert hrv["sleep_date"].between(date(2024, 4, 1), date(2024, 4, 30)).all()


def test_selection_validation_and_registry(hh):
    cfg, con = hh
    with pytest.raises(ValueError, match="Ismeretlen tábla"):
        service.export_raw(con, cfg, tabular.Selection(tables=["stg_apple_record"]), "csv")
    with pytest.raises(ValueError, match="legalább egy"):
        service.export_raw(con, cfg, tabular.Selection(tables=[]), "xlsx")
    with pytest.raises(ValueError):
        service.export_raw(con, cfg, tabular.Selection(tables=["dim_source"],
                                                       date_from=date(2024, 2, 1),
                                                       date_to=date(2024, 1, 1)), "xlsx")
    with pytest.raises(ValueError):
        service.export_raw(con, cfg, tabular.Selection(tables=["dim_source"]), "json")
    # every registered table compiles with all filters, and every column is documented
    reg = tabular.load_registry()
    for key, tb in reg.tables.items():
        sql, params = tabular.table_query(tb, tabular.Selection(
            [key], ["me", "partner"], date(2024, 1, 1), date(2024, 12, 31)))
        cols = [c[0] for c in con.execute(f"SELECT * FROM ({sql}) LIMIT 0", params).description]
        assert set(cols) <= set(reg.columns), (key, set(cols) - set(reg.columns))
        assert len(tb.sheet) <= 27
    # raw staging tables are never exportable (they may hold identifiers)
    assert not any(k.startswith("stg_") for k in reg.tables)


# ---------------------------------------------------------------- history

def test_history_regenerate_and_delete(hh):
    cfg, con = hh
    sel = tabular.Selection(tables=["dim_source", "derived_weekly_activity"], persons=["me"])
    first = service.export_raw(con, cfg, sel, "xlsx")
    again = service.regenerate(con, cfg, first.export_id)
    assert again.path != first.path and again.rows == first.rows
    hist = service.list_history(con).set_index("export_id")
    assert hist.loc[again.export_id, "regenerated_from"] == first.export_id
    assert hist.loc[again.export_id, "params"] == hist.loc[first.export_id, "params"]
    assert Path(hist.loc[first.export_id, "file_path"]).parent == cfg.exports_path / "raw"
    service.delete_history(con, again.export_id)
    assert not again.path.exists()
    assert again.export_id not in set(service.list_history(con).export_id)
    with pytest.raises(KeyError):
        service.regenerate(con, cfg, "nincs-ilyen")


# ---------------------------------------------------------------- UI + CLI

@pytest.fixture
def ui_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("VITALMART_DATA_DIR", str(d))
    cfg = load_config(data_dir=str(d))
    con = db.connect(cfg)
    run_import(con, cfg, write_export_zip(tmp_path / "a", SampleSpec(days=60)), person="partner")
    con.close()
    return d


def _page(name: str) -> AppTest:
    return AppTest.from_file(str(APP / "views" / name), default_timeout=180)


def test_export_page_create_and_history(ui_dir):
    at = _page("6_Export.py").run()
    assert not at.exception
    assert any("Becsült méret" in c.value for c in at.caption)
    at.radio(key="xp_fmt").set_value("csv").run()
    at.button(key="xp_create").click().run()
    assert not at.exception
    assert any("Elkészült" in s.value for s in at.success)
    files = list((ui_dir / "exports" / "raw").glob("*.zip"))
    assert len(files) == 1
    # history tab: regenerate the export
    at.button(key="xp_regen").click().run()
    assert not at.exception
    assert len(list((ui_dir / "exports" / "raw").glob("*.zip"))) == 2


def test_report_page_pdf_button(ui_dir):
    at = _page("3_Riport.py").run()
    at.button(key="rp_generate").click().run()
    at.button(key="rp_pdf_make").click().run()
    assert not at.exception
    assert list((ui_dir / "exports" / "reports").glob("*.pdf"))


def test_explorer_export_buttons(ui_dir):
    at = _page("4_Elemzo.py").run()
    assert not at.exception
    at.text_area(key="ex_x_note").set_value("UI-megjegyzés").run()
    at.button(key="ex_x_pdf").click().run()
    assert not at.exception
    pdfs = list((ui_dir / "exports" / "analysis").glob("*.pdf"))
    assert len(pdfs) == 1 and "UI-megjegyzés" in _pdf_text(pdfs[0])
    at.button(key="ex_x_xlsx").click().run()
    assert list((ui_dir / "exports" / "analysis").glob("*.xlsx"))


def test_cli_export_and_report_pdf(tmp_path, capsys):
    d = tmp_path / "data"
    zip_path = write_export_zip(tmp_path / "a", SampleSpec(days=45))
    assert main(["--data-dir", str(d), "import", str(zip_path), "-q", "--person", "me"]) == 0
    assert main(["--data-dir", str(d), "export", "--list"]) == 0
    assert "fact_daily_summary" in capsys.readouterr().out
    assert main(["--data-dir", str(d), "export", "--format", "parquet", "--tables",
                 "fact_daily_summary"]) == 0
    assert list((d / "exports" / "raw").glob("*.parquet"))
    assert main(["--data-dir", str(d), "export", "--tables", "nincs"]) == 2
    out = tmp_path / "r" / "riport.html"
    assert main(["--data-dir", str(d), "report", "--pdf", "--out", str(out)]) == 0
    assert out.exists() and out.with_suffix(".pdf").exists()
    assert main(["--data-dir", str(d), "export", "--history"]) == 0
    assert "report" in capsys.readouterr().out
