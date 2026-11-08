"""Export orchestration + history (SPEC 8.3): every export is recorded with the parameters
needed to regenerate it; files live under ``<data_dir>/exports``.

Only counts and file names are logged – never health values.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from vitalmart.config import AppConfig
from vitalmart.export import tabular
from vitalmart.export.describe import describe_spec
from vitalmart.i18n import t
from vitalmart.semantic.engine import Result, display_frame, run
from vitalmart.semantic.model import SemanticModel, load_model
from vitalmart.semantic.spec import AnalysisSpec

log = logging.getLogger(__name__)

RAW_FORMATS = ("xlsx", "csv", "parquet")
ANALYSIS_FORMATS = ("xlsx", "csv", "pdf")


@dataclass
class ExportOutcome:
    export_id: str
    path: Path
    rows: int = 0
    warnings: list[str] = field(default_factory=list)


def slug(text: str, max_len: int = 40) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return (s or "export")[:max_len]


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _target(cfg: AppConfig, sub: str, stem: str, ext: str, created: datetime) -> Path:
    folder = cfg.exports_path / sub
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{stem}_{created:%Y%m%d_%H%M%S}"
    out = folder / f"{base}.{ext}"
    i = 2
    while out.exists():
        out = folder / f"{base}_{i}.{ext}"
        i += 1
    return out


def _record(con: duckdb.DuckDBPyConnection, *, created: datetime, kind: str, fmt: str,
            title: str, params: dict[str, Any], path: Path, rows: int, warnings: list[str],
            regenerated_from: str | None) -> str:
    data = path.read_bytes()
    export_id = hashlib.sha256(f"{kind}{fmt}{path}{created}".encode()).hexdigest()[:12]
    con.execute("""INSERT INTO export_history (export_id, created_at, kind, fmt, title, params,
                                               file_path, size_bytes, row_count, sha256,
                                               warnings, regenerated_from)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [export_id, created, kind, fmt, title, json.dumps(params, sort_keys=True,
                                                                  ensure_ascii=False),
                 str(path), len(data), rows, hashlib.sha256(data).hexdigest(),
                 "\n".join(warnings) or None, regenerated_from])
    log.info("export %s: kind=%s fmt=%s rows=%d file=%s", export_id, kind, fmt, rows, path.name)
    return export_id


# ---------------------------------------------------------------- raw data

def raw_title(sel: tabular.Selection, reg: tabular.ExportRegistry) -> str:
    names = ", ".join(reg.tables[k].label_hu for k in sel.tables[:3])
    more = f" (+{len(sel.tables) - 3})" if len(sel.tables) > 3 else ""
    return f"{t('export.kind.raw')}: {names}{more}"


def export_raw(con: duckdb.DuckDBPyConnection, cfg: AppConfig, sel: tabular.Selection,
               fmt: str, *, max_rows: int = tabular.XLSX_MAX_ROWS,
               regenerated_from: str | None = None) -> ExportOutcome:
    if fmt not in RAW_FORMATS:
        raise ValueError(t("export.err.format", fmt=fmt))
    reg = tabular.load_registry()
    created = _now()
    persons = "_".join(sel.persons) if sel.persons else "mind"
    multi_flat = fmt in ("csv", "parquet") and len(sel.tables) > 1
    out = _target(cfg, "raw", f"nyers_{slug(persons)}", "zip" if multi_flat else fmt, created)
    if fmt == "xlsx":
        res = tabular.write_xlsx(con, sel, out, reg=reg, max_rows=max_rows, generated_at=created)
    else:
        res = tabular.write_flat(con, sel, out, fmt, reg=reg)
    params = {**sel.as_params(), "fmt": fmt}
    eid = _record(con, created=created, kind="raw", fmt=fmt, title=raw_title(sel, reg),
                  params=params, path=out, rows=res.total_rows, warnings=res.warnings,
                  regenerated_from=regenerated_from)
    return ExportOutcome(eid, out, res.total_rows, res.warnings)


# ---------------------------------------------------------------- explorer analysis

def analysis_files(res: Result, model: SemanticModel, *, title: str, note: str, fmt: str,
                   out: Path, generated_at: datetime, fig: Any = None) -> int:
    """Write one analysis export to ``out``; returns the number of table rows."""
    from vitalmart.semantic.charts import build_figure

    table = display_frame(res)
    desc = describe_spec(res.spec, model, res)
    if fmt == "csv":
        table.to_csv(out, index=False, encoding="utf-8")
    elif fmt == "xlsx":
        tabular.analysis_xlsx(table, desc, out, title=title, note=note, sql=res.query.sql,
                              params=json.dumps([str(p) for p in res.query.params],
                                                ensure_ascii=False),
                              spec_json=res.spec.model_dump_json(exclude_defaults=True, indent=2),
                              generated_at=generated_at)
    elif fmt == "pdf":
        from vitalmart.export.pdf import analysis_pdf

        if fig is None and res.spec.chart != "table" and len(res.df):
            fig = build_figure(res)
        out.write_bytes(analysis_pdf(title=title, description=desc, table=table, fig=fig,
                                     note=note, generated_at=generated_at, sql=res.query.sql))
    else:
        raise ValueError(t("export.err.format", fmt=fmt))
    return len(table)


def export_analysis(con: duckdb.DuckDBPyConnection, cfg: AppConfig, spec: AnalysisSpec,
                    fmt: str, *, title: str, note: str = "", model: SemanticModel | None = None,
                    regenerated_from: str | None = None) -> ExportOutcome:
    if fmt not in ANALYSIS_FORMATS:
        raise ValueError(t("export.err.format", fmt=fmt))
    model = model or load_model()
    res = run(con, spec, model)
    created = _now()
    out = _target(cfg, "analysis", f"elemzes_{slug(title)}", fmt, created)
    n = analysis_files(res, model, title=title, note=note, fmt=fmt, out=out,
                       generated_at=created)
    warnings = [t("explorer.info_truncated")] if res.truncated else []
    params = {"spec": json.loads(spec.model_dump_json(exclude_defaults=True)), "title": title,
              "note": note, "fmt": fmt}
    eid = _record(con, created=created, kind="analysis", fmt=fmt, title=title, params=params,
                  path=out, rows=n, warnings=warnings, regenerated_from=regenerated_from)
    return ExportOutcome(eid, out, n, warnings)


# ---------------------------------------------------------------- report PDF

def export_report_pdf(con: duckdb.DuckDBPyConnection, cfg: AppConfig, person: str, start: date,
                      end: date, *, generated_at: datetime | None = None,
                      regenerated_from: str | None = None, out: Path | None = None
                      ) -> ExportOutcome:
    from vitalmart.export.pdf import write_report_pdf
    from vitalmart.report.engine import build, period_text

    created = _now()
    rep = build(con, cfg, person, start, end, generated_at=generated_at or created)
    out = out or _target(cfg, "reports", f"riport_{slug(person)}_{start:%Y%m%d}_{end:%Y%m%d}",
                         "pdf", created)
    write_report_pdf(rep, out)
    params = {"person": person, "start": start.isoformat(), "end": end.isoformat(),
              "generated_at": rep.generated_at.isoformat(), "data_version": rep.data_version,
              "rules_version": rep.rules_version}
    title = f"{t('report.title')} – {rep.person_name} – {period_text(rep)}"
    eid = _record(con, created=created, kind="report", fmt="pdf", title=title, params=params,
                  path=out, rows=0, warnings=[], regenerated_from=regenerated_from)
    return ExportOutcome(eid, out)


# ---------------------------------------------------------------- history

def list_history(con: duckdb.DuckDBPyConnection, limit: int = 200) -> pd.DataFrame:
    return con.execute("""SELECT export_id, created_at, kind, fmt, title, row_count, size_bytes,
                                 file_path, warnings, regenerated_from, params
                          FROM export_history ORDER BY created_at DESC, export_id
                          LIMIT ?""", [limit]).df()


def get_history(con: duckdb.DuckDBPyConnection, export_id: str) -> dict[str, Any]:
    rows = con.execute("""SELECT kind, fmt, title, params, file_path, created_at
                          FROM export_history WHERE export_id = ?""", [export_id]).fetchall()
    if not rows:
        raise KeyError(export_id)
    kind, fmt, title, params, path, created = rows[0]
    return {"kind": kind, "fmt": fmt, "title": title, "params": json.loads(params),
            "file_path": Path(path), "created_at": created}


def regenerate(con: duckdb.DuckDBPyConnection, cfg: AppConfig, export_id: str
               ) -> ExportOutcome:
    """Run an earlier export again with exactly the same parameters (new file)."""
    h = get_history(con, export_id)
    p = h["params"]
    if h["kind"] == "raw":
        return export_raw(con, cfg, tabular.Selection.from_params(p), h["fmt"],
                          regenerated_from=export_id)
    if h["kind"] == "analysis":
        return export_analysis(con, cfg, AnalysisSpec.model_validate(p["spec"]), h["fmt"],
                               title=p["title"], note=p.get("note", ""),
                               regenerated_from=export_id)
    if h["kind"] == "report":
        return export_report_pdf(con, cfg, p["person"], date.fromisoformat(p["start"]),
                                 date.fromisoformat(p["end"]),
                                 generated_at=datetime.fromisoformat(p["generated_at"]),
                                 regenerated_from=export_id)
    raise ValueError(h["kind"])


def delete_history(con: duckdb.DuckDBPyConnection, export_id: str, *,
                   delete_file: bool = True) -> None:
    h = get_history(con, export_id)
    con.execute("DELETE FROM export_history WHERE export_id = ?", [export_id])
    path: Path = h["file_path"]
    if delete_file and path.exists():
        path.unlink()
