"""Tabular exports (SPEC 8.2): XLSX (one sheet per table, data dictionary), CSV, Parquet.

The table registry lives in ``config/export_tables.yaml``; user input (period, persons)
is always bound as SQL parameters. Large tables are streamed in Arrow record batches.
"""

from __future__ import annotations

import io
import itertools
import math
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import xlsxwriter
import yaml
from pydantic import BaseModel, Field
from xlsxwriter.worksheet import Worksheet

from vitalmart import __version__
from vitalmart.config import CONFIG_DIR
from vitalmart.fmt import fmt_date, fmt_datetime
from vitalmart.i18n import t

XLSX_MAX_ROWS = 1_048_575          # Excel: 1 048 576 rows incl. the header row
BATCH_ROWS = 50_000
SHEET_NAME_MAX = 31


class ExportTable(BaseModel):
    key: str = ""
    label_hu: str
    sheet: str
    group: str = "fact"
    large: bool = False
    sql: str
    date: str = ""
    person: str = ""
    order: str = ""


class ColumnDoc(BaseModel):
    label: str
    unit: str = ""
    desc: str = ""


class ExportRegistry(BaseModel):
    version: int = 1
    tables: dict[str, ExportTable]
    default_tables: list[str] = Field(default_factory=list)
    suffix_units: dict[str, str] = Field(default_factory=dict)
    columns: dict[str, ColumnDoc] = Field(default_factory=dict)

    def column(self, name: str) -> ColumnDoc:
        if name in self.columns:
            return self.columns[name]
        unit = next((u for sfx, u in sorted(self.suffix_units.items(), key=lambda kv: -len(kv[0]))
                     if name.endswith(sfx)), "")
        return ColumnDoc(label=name, unit=unit)

    def header(self, name: str) -> str:
        c = self.column(name)
        return f"{c.label} [{c.unit}]" if c.unit else c.label


def load_registry(path: Path = CONFIG_DIR / "export_tables.yaml") -> ExportRegistry:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    reg = ExportRegistry.model_validate(data)
    for k, tb in reg.tables.items():
        tb.key = k
        if len(tb.sheet) > SHEET_NAME_MAX - 4:
            raise ValueError(f"export table {k}: sheet name too long")
    unknown = set(reg.default_tables) - set(reg.tables)
    if unknown:
        raise ValueError(f"unknown default export tables: {unknown}")
    return reg


@dataclass
class Selection:
    """What to export: tables, persons (empty = everyone), inclusive period (None = all)."""

    tables: list[str]
    persons: list[str] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None

    def as_params(self) -> dict[str, Any]:
        return {"tables": self.tables, "persons": self.persons,
                "date_from": self.date_from.isoformat() if self.date_from else None,
                "date_to": self.date_to.isoformat() if self.date_to else None}

    @classmethod
    def from_params(cls, d: dict[str, Any]) -> Selection:
        return cls(tables=list(d["tables"]), persons=list(d.get("persons") or []),
                   date_from=date.fromisoformat(d["date_from"]) if d.get("date_from") else None,
                   date_to=date.fromisoformat(d["date_to"]) if d.get("date_to") else None)


@dataclass
class TabularResult:
    path: Path
    rows: dict[str, int]
    warnings: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


def table_query(tb: ExportTable, sel: Selection) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if tb.date and sel.date_from:
        where.append(f"{tb.date} >= ?")
        params.append(sel.date_from)
    if tb.date and sel.date_to:
        where.append(f"{tb.date} <= ?")
        params.append(sel.date_to)
    if tb.person and sel.persons:
        where.append(f"{tb.person} IN ({', '.join('?' for _ in sel.persons)})")
        params.extend(sel.persons)
    sql = tb.sql.strip()
    if where:
        sql += "\nWHERE " + " AND ".join(where)
    if tb.order:
        sql += f"\nORDER BY {tb.order}"
    return sql, params


def _validate(reg: ExportRegistry, sel: Selection) -> None:
    if not sel.tables:
        raise ValueError(t("export.err.no_tables"))
    unknown = [x for x in sel.tables if x not in reg.tables]
    if unknown:
        raise ValueError(t("export.err.unknown_table", table=", ".join(unknown)))
    if sel.date_from and sel.date_to and sel.date_from > sel.date_to:
        raise ValueError(t("export.err.period"))


def count_rows(con: duckdb.DuckDBPyConnection, reg: ExportRegistry, sel: Selection
               ) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in sel.tables:
        sql, params = table_query(reg.tables[key], sel)
        out[key] = int(con.execute(f"SELECT count(*) FROM ({sql})", params).fetchall()[0][0])
    return out


def _batches(con: duckdb.DuckDBPyConnection, reg: ExportRegistry, key: str, sel: Selection
             ) -> pa.RecordBatchReader:
    sql, params = table_query(reg.tables[key], sel)
    return con.sql(sql, params=params).to_arrow_reader(BATCH_ROWS)


# ---------------------------------------------------------------- CSV / Parquet

def _write_one(reader: pa.RecordBatchReader, sink: Any, fmt: str) -> int:
    n = 0
    if fmt == "csv":
        with pacsv.CSVWriter(sink, reader.schema) as w:
            for b in reader:
                w.write_batch(b)
                n += b.num_rows
    else:
        with pq.ParquetWriter(sink, reader.schema, compression="zstd") as w:
            for b in reader:
                w.write_batch(b)
                n += b.num_rows
    return n


def write_flat(con: duckdb.DuckDBPyConnection, sel: Selection, out: Path, fmt: str,
               reg: ExportRegistry | None = None) -> TabularResult:
    """CSV or Parquet. One table → that file; several → a ZIP with one file per table."""
    reg = reg or load_registry()
    _validate(reg, sel)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[str, int] = {}
    if len(sel.tables) == 1:
        key = sel.tables[0]
        rows[key] = _write_one(_batches(con, reg, key, sel), str(out), fmt)
        return TabularResult(out, rows)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for key in sel.tables:
            buf = io.BytesIO()
            rows[key] = _write_one(_batches(con, reg, key, sel), buf, fmt)
            zf.writestr(f"{key}.{fmt}", buf.getvalue())
        zf.writestr("adatszotar.csv", _dictionary_frame(con, reg, sel.tables)
                    .to_csv(index=False))
    return TabularResult(out, rows)


# ---------------------------------------------------------------- XLSX

class _Formats:
    def __init__(self, wb: xlsxwriter.Workbook) -> None:
        self.header = wb.add_format({"bold": True, "bg_color": "#F1F5F9", "bottom": 1,
                                     "text_wrap": True, "valign": "top"})
        self.date = wb.add_format({"num_format": "yyyy.mm.dd."})
        self.ts = wb.add_format({"num_format": "yyyy.mm.dd. hh:mm:ss"})
        self.num = wb.add_format({"num_format": "0.###"})
        self.int = wb.add_format({"num_format": "0"})
        self.wrap = wb.add_format({"text_wrap": True, "valign": "top"})
        self.bold = wb.add_format({"bold": True})
        self.title = wb.add_format({"bold": True, "font_size": 14})
        self.warn = wb.add_format({"font_color": "#B91C1C", "text_wrap": True, "valign": "top"})


def _cell_writer(typ: pa.DataType, f: _Formats, ws: Worksheet) -> Callable[[int, int, Any], Any]:
    if pa.types.is_timestamp(typ):
        return lambda r, c, v: ws.write_datetime(r, c, v.replace(tzinfo=None), f.ts)
    if pa.types.is_date(typ):
        return lambda r, c, v: ws.write_datetime(r, c, datetime(v.year, v.month, v.day), f.date)
    if pa.types.is_boolean(typ):
        return lambda r, c, v: ws.write_boolean(r, c, v)
    if pa.types.is_integer(typ):
        return lambda r, c, v: ws.write_number(r, c, v, f.int)
    if pa.types.is_floating(typ) or pa.types.is_decimal(typ):
        def _num(r: int, c: int, v: Any) -> Any:
            v = float(v)
            if math.isnan(v) or math.isinf(v):
                return None
            return ws.write_number(r, c, v, f.num)
        return _num
    return lambda r, c, v: ws.write_string(r, c, str(v)[:32_000])


def _width(header: str, typ: pa.DataType, sample: Iterable[Any]) -> float:
    if pa.types.is_timestamp(typ):
        base = 19.0
    elif pa.types.is_date(typ):
        base = 11.0
    else:
        base = max((len(str(v)) for v in sample if v is not None), default=4) + 1
    return float(min(max(base, min(len(header), 28) * 0.9, 6), 50))


def _sheet_name(base: str, part: int, used: set[str]) -> str:
    name = base if part == 1 else f"{base} ({part})"
    name = name[:SHEET_NAME_MAX]
    for ch in "[]:*?/\\":
        name = name.replace(ch, "_")
    i = 2
    while name in used:
        name = f"{name[:SHEET_NAME_MAX - 3]}~{i}"
        i += 1
    used.add(name)
    return name


def _new_sheet(wb: xlsxwriter.Workbook, name: str, schema: pa.Schema, headers: list[str],
               f: _Formats, first: pa.RecordBatch | None) -> tuple[Worksheet, list[Callable]]:
    ws = wb.add_worksheet(name)
    ws.write_row(0, 0, headers, f.header)
    ws.set_row(0, 30)
    ws.freeze_panes(1, 0)
    writers = []
    for c, fld in enumerate(schema):
        sample = first.column(c).to_pylist()[:200] if first is not None else []
        ws.set_column(c, c, _width(headers[c], fld.type, sample))
        writers.append(_cell_writer(fld.type, f, ws))
    return ws, writers


def _write_table(wb: xlsxwriter.Workbook, f: _Formats, reg: ExportRegistry, key: str,
                 reader: pa.RecordBatchReader, used: set[str], max_rows: int
                 ) -> tuple[int, list[str], list[str]]:
    """Returns (rows, sheet names, warnings)."""
    tb = reg.tables[key]
    schema = reader.schema
    headers = [reg.header(n) for n in schema.names]
    batches = iter(reader)
    first = next(batches, None)
    part = 1
    sheets = [_sheet_name(tb.sheet, part, used)]
    ws, writers = _new_sheet(wb, sheets[-1], schema, headers, f, first)
    row = 0
    total = 0

    def finish(ws_: Worksheet, nrows: int) -> None:
        ws_.autofilter(0, 0, max(nrows, 1), max(len(headers) - 1, 0))

    for batch in itertools.chain([first] if first is not None else [], batches):
        cols = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
        for r in range(batch.num_rows):
            if row >= max_rows:
                finish(ws, row)
                part += 1
                sheets.append(_sheet_name(tb.sheet, part, used))
                ws, writers = _new_sheet(wb, sheets[-1], schema, headers, f, batch)
                row = 0
            row += 1
            for c, w in enumerate(writers):
                v = cols[c][r]
                if v is not None:
                    w(row, c, v)
            total += 1
    finish(ws, row)
    warnings = []
    if part > 1:
        warnings.append(t("export.warn.split", table=tb.label_hu, rows=total, sheets=part,
                          limit=max_rows))
    return total, sheets, warnings


def _dictionary_frame(con: duckdb.DuckDBPyConnection, reg: ExportRegistry,
                      tables: Sequence[str]) -> pd.DataFrame:
    rows = []
    for key in tables:
        tb = reg.tables[key]
        sql, params = table_query(tb, Selection(tables=[key]))
        desc = con.execute(f"SELECT * FROM ({sql}) LIMIT 0", params).description or []
        for col in desc:
            name = col[0]
            c = reg.column(name)
            rows.append({t("export.dict.sheet"): tb.sheet, t("export.dict.table"): key,
                         t("export.dict.header"): reg.header(name),
                         t("export.dict.column"): name, t("export.dict.unit"): c.unit,
                         t("export.dict.type"): str(col[1]),
                         t("export.dict.desc"): c.desc})
    return pd.DataFrame(rows)


def _write_frame(ws: Worksheet, df: pd.DataFrame, f: _Formats, start_row: int = 0,
                 widths: Sequence[float] | None = None) -> int:
    ws.write_row(start_row, 0, [str(c) for c in df.columns], f.header)
    for r, rec in enumerate(df.itertuples(index=False), start=start_row + 1):
        for c, v in enumerate(rec):
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                continue
            if isinstance(v, pd.Timestamp | datetime):
                ts = pd.Timestamp(v).to_pydatetime().replace(tzinfo=None)
                ws.write_datetime(r, c, ts, f.date if ts == ts.replace(hour=0, minute=0,
                                                                         second=0, microsecond=0)
                                  else f.ts)
            elif isinstance(v, date):
                ws.write_datetime(r, c, datetime(v.year, v.month, v.day), f.date)
            elif isinstance(v, bool):
                ws.write_boolean(r, c, v)
            elif isinstance(v, int | float):
                ws.write_number(r, c, float(v), f.num)
            else:
                ws.write_string(r, c, str(v)[:32_000], f.wrap)
    for c, col in enumerate(df.columns):
        w = widths[c] if widths else min(max(len(str(col)), *(len(str(v)) for v in
                                                                 df[col].head(200)), 8) + 2, 60)
        ws.set_column(c, c, w)
    return start_row + len(df) + 1


def _workbook(out: Path, *, constant_memory: bool = False) -> xlsxwriter.Workbook:
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(out), {"constant_memory": constant_memory,
                                        "strings_to_numbers": False,
                                        "strings_to_formulas": False, "strings_to_urls": False,
                                        "remove_timezone": True})
    wb.set_properties({"title": "VitalMart export", "author": "VitalMart",
                       "comments": f"VitalMart {__version__}"})
    return wb


def write_xlsx(con: duckdb.DuckDBPyConnection, sel: Selection, out: Path, *,
               reg: ExportRegistry | None = None, max_rows: int = XLSX_MAX_ROWS,
               generated_at: datetime | None = None) -> TabularResult:
    reg = reg or load_registry()
    _validate(reg, sel)
    wb = _workbook(out, constant_memory=True)
    f = _Formats(wb)
    rows: dict[str, int] = {}
    sheets: dict[str, list[str]] = {}
    warnings: list[str] = []
    used: set[str] = {t("export.sheet.info"), t("export.sheet.dictionary"),
                      t("export.sheet.metrics")}
    # info sheet first (content written after the data so counts are known)
    placeholder = wb.add_worksheet(t("export.sheet.info"))
    for key in sel.tables:
        n, names, warn = _write_table(wb, f, reg, key, _batches(con, reg, key, sel), used,
                                      max_rows)
        rows[key], sheets[key] = n, names
        warnings += warn
    ws = wb.add_worksheet(t("export.sheet.dictionary"))
    ws.freeze_panes(1, 0)
    dict_df = _dictionary_frame(con, reg, sel.tables)
    end = _write_frame(ws, dict_df, f, widths=[20, 24, 30, 22, 12, 12, 80])
    ws.autofilter(0, 0, max(end - 1, 1), len(dict_df.columns) - 1)
    metrics = con.execute("""SELECT metric_key, name_hu, category, unit, agg_rule,
                                    better_direction, note
                             FROM dim_metric ORDER BY category, metric_key""").df()
    metrics.columns = [t("export.metrics.key"), t("export.metrics.name"),
                       t("export.metrics.category"), t("export.metrics.unit"),
                       t("export.metrics.agg"), t("export.metrics.direction"),
                       t("export.metrics.note")]
    wm = wb.add_worksheet(t("export.sheet.metrics"))
    wm.freeze_panes(1, 0)
    _write_frame(wm, metrics, f, widths=[26, 34, 14, 12, 10, 12, 60])

    info_rows = [
        (t("export.info.generated"), fmt_datetime(generated_at or datetime.now())),
        (t("export.info.persons"), ", ".join(sel.persons) or t("explorer.all_persons")),
        (t("export.info.period"), f"{fmt_date(sel.date_from) if sel.date_from else '…'} – "
                                  f"{fmt_date(sel.date_to) if sel.date_to else '…'}"),
        (t("export.info.app"), __version__),
        *[(reg.tables[k].label_hu, t("export.info.rows", rows=rows[k],
                                     sheets=", ".join(sheets[k]))) for k in sel.tables],
        (t("export.info.formats"), t("export.info.formats_text")),
    ]
    _fill_info(placeholder, f, t("export.info.title_raw"), info_rows, warnings)
    placeholder.activate()
    wb.close()
    return TabularResult(out, rows, warnings)


def _fill_info(ws: Worksheet, f: _Formats, title: str, rows: Sequence[tuple[str, str]],
               warnings: Sequence[str]) -> None:
    ws.write(0, 0, title, f.title)
    r = 2
    for k, v in rows:
        ws.write(r, 0, k, f.bold)
        ws.write(r, 1, v, f.wrap)
        r += 1
    for w in warnings:
        r += 1
        ws.write(r, 0, t("export.warn.title"), f.bold)
        ws.write(r, 1, w, f.warn)
    r += 2
    ws.write(r, 0, t("report.section.disclaimer"), f.bold)
    ws.write(r, 1, t("app.disclaimer"), f.wrap)
    ws.set_column(0, 0, 34)
    ws.set_column(1, 1, 100)


# ---------------------------------------------------------------- analysis result

def analysis_xlsx(table: pd.DataFrame, description: Sequence[tuple[str, str]], out: Path, *,
                  title: str, note: str = "", sql: str = "", params: str = "",
                  spec_json: str = "", generated_at: datetime | None = None) -> Path:
    """Explorer result: 'Eredmény' sheet + 'Leírás' sheet (definition, parameters, SQL)."""
    wb = _workbook(out)
    f = _Formats(wb)
    ws = wb.add_worksheet(t("export.sheet.result"))
    ws.freeze_panes(1, 0)
    rows = min(len(table), XLSX_MAX_ROWS)
    end = _write_frame(ws, table.head(rows), f)
    ws.autofilter(0, 0, max(end - 1, 1), max(len(table.columns) - 1, 0))
    ws.set_row(0, 30)
    warnings = ([t("export.warn.analysis_cut", rows=len(table), limit=XLSX_MAX_ROWS)]
                if len(table) > XLSX_MAX_ROWS else [])
    wd = wb.add_worksheet(t("export.sheet.description"))
    wd.write(0, 0, title, f.title)
    r = 2
    wd.write(r, 0, t("export.info.generated"), f.bold)
    wd.write(r, 1, fmt_datetime(generated_at or datetime.now()))
    r += 1
    for k, v in description:
        wd.write(r, 0, k, f.bold)
        wd.write(r, 1, v, f.wrap)
        r += 1
    if note.strip():
        r += 1
        wd.write(r, 0, t("export.pdf.note"), f.bold)
        wd.write(r, 1, note, f.wrap)
        r += 1
    for w in warnings:
        r += 1
        wd.write(r, 0, t("export.warn.title"), f.bold)
        wd.write(r, 1, w, f.warn)
        r += 1
    for label, text in ((t("explorer.sql"), sql), (t("explorer.params"), params),
                        (t("export.desc.definition"), spec_json)):
        if text:
            r += 1
            wd.write(r, 0, label, f.bold)
            wd.write(r, 1, text, f.wrap)
            r += 1
    r += 1
    wd.write(r, 0, t("report.section.disclaimer"), f.bold)
    wd.write(r, 1, t("app.disclaimer"), f.wrap)
    wd.set_column(0, 0, 34)
    wd.set_column(1, 1, 110)
    wb.close()
    return out
