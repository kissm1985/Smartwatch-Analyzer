"""Saved views ("Saját nézetek", SPEC 8.3 SHOULD): several saved analyses on one page, with
an optional common period and person override."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import duckdb

from vitalmart.semantic import store
from vitalmart.semantic.engine import Result, run
from vitalmart.semantic.model import SemanticModel
from vitalmart.semantic.spec import PERIOD_DAYS, AnalysisSpec, SpecError

MAX_ITEMS = 12
OVERRIDE_PERIODS = [*PERIOD_DAYS, "all"]


class ViewError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class SavedView:
    view_id: str
    name: str
    description: str
    items: list[str]
    columns: int = 2
    period: str | None = None
    persons: list[str] | None = None


@dataclass
class ViewItem:
    analysis: store.Saved | None
    analysis_id: str
    spec: AnalysisSpec | None = None
    result: Result | None = None
    error: str | None = None            # i18n key
    error_kwargs: dict[str, Any] = field(default_factory=dict)


def _row(r: tuple[Any, ...]) -> SavedView:
    return SavedView(r[0], r[1], r[2] or "", json.loads(r[3]), int(r[4]), r[5],
                     json.loads(r[6]) if r[6] else None)


_COLS = "view_id, name, description, items, columns, period, persons"


def list_views(con: duckdb.DuckDBPyConnection) -> list[SavedView]:
    return [_row(r) for r in con.execute(
        f"SELECT {_COLS} FROM saved_view ORDER BY lower(name), view_id").fetchall()]


def get_view(con: duckdb.DuckDBPyConnection, view_id: str) -> SavedView:
    rows = con.execute(f"SELECT {_COLS} FROM saved_view WHERE view_id = ?", [view_id]).fetchall()
    if not rows:
        raise ViewError("views.err.not_found")
    return _row(rows[0])


def save_view(con: duckdb.DuckDBPyConnection, name: str, items: list[str], *,
              description: str = "", columns: int = 2, period: str | None = None,
              persons: list[str] | None = None, view_id: str | None = None) -> str:
    if not name.strip():
        raise ViewError("views.err.name")
    if not items:
        raise ViewError("views.err.items")
    if len(items) > MAX_ITEMS:
        raise ViewError("views.err.too_many")
    if period is not None and period not in OVERRIDE_PERIODS:
        raise ViewError("views.err.period")
    known = {a.analysis_id for a in store.list_analyses(con)}
    if any(i not in known for i in items):
        raise ViewError("views.err.unknown_analysis")
    columns = max(1, min(3, int(columns)))
    now = datetime.now().replace(microsecond=0)
    if view_id:
        get_view(con, view_id)
        con.execute("""UPDATE saved_view SET name = ?, description = ?, items = ?, columns = ?,
                                             period = ?, persons = ?, updated_at = ?
                       WHERE view_id = ?""",
                    [name.strip(), description, json.dumps(items), columns, period,
                     json.dumps(persons) if persons else None, now, view_id])
        return view_id
    view_id = "view:" + hashlib.sha256(f"{name}{now.isoformat()}".encode()).hexdigest()[:10]
    con.execute("""INSERT INTO saved_view (view_id, name, description, items, columns, period,
                                           persons, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [view_id, name.strip(), description, json.dumps(items), columns, period,
                 json.dumps(persons) if persons else None, now, now])
    return view_id


def delete_view(con: duckdb.DuckDBPyConnection, view_id: str) -> None:
    get_view(con, view_id)
    con.execute("DELETE FROM saved_view WHERE view_id = ?", [view_id])


def effective_spec(spec: AnalysisSpec, view: SavedView) -> AnalysisSpec:
    upd: dict[str, Any] = {}
    if view.period:
        upd.update(period=view.period, date_from=None, date_to=None)
    if view.persons:
        upd["persons"] = list(view.persons)
    return spec.model_copy(update=upd, deep=True) if upd else spec


def run_view(con: duckdb.DuckDBPyConnection, view: SavedView,
             model: SemanticModel) -> list[ViewItem]:
    """Run every analysis of the view; a missing/invalid item does not break the others."""
    by_id = {a.analysis_id: a for a in store.list_analyses(con)}
    out: list[ViewItem] = []
    for aid in view.items:
        a = by_id.get(aid)
        if a is None:
            out.append(ViewItem(None, aid, error="views.err.missing_item"))
            continue
        spec = effective_spec(a.spec, view)
        try:
            out.append(ViewItem(a, aid, spec, run(con, spec, model)))
        except SpecError as exc:
            out.append(ViewItem(a, aid, spec, error=exc.key, error_kwargs=dict(exc.kwargs)))
    return out
