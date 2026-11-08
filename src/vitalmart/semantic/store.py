"""Saved analyses (SPEC 8.3): save / open / duplicate / delete, built-ins from YAML."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb
import yaml

from vitalmart.paths import CONFIG_DIR
from vitalmart.semantic.model import SemanticModel, load_model
from vitalmart.semantic.spec import AnalysisSpec, validate


class StoreError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class Saved:
    analysis_id: str
    name: str
    description: str
    tags: list[str]
    spec: AnalysisSpec
    is_builtin: bool


def _row(r: tuple) -> Saved:
    return Saved(r[0], r[1], r[2] or "", list(r[3] or []),
                 AnalysisSpec.model_validate(json.loads(r[4])), bool(r[5]))


def seed_builtins(con: duckdb.DuckDBPyConnection, path: Path = CONFIG_DIR / "saved_analyses.yaml",
                  model: SemanticModel | None = None) -> int:
    """(Re)load the built-in analyses; user analyses are untouched."""
    model = model or load_model()
    with path.open(encoding="utf-8") as fh:
        items = (yaml.safe_load(fh) or {}).get("analyses", {})
    con.execute("DELETE FROM saved_analysis WHERE is_builtin")
    for key, a in items.items():
        spec = AnalysisSpec.model_validate(a["spec"])
        validate(spec, model)
        con.execute("""INSERT INTO saved_analysis (analysis_id, name, description, tags, spec,
                       is_builtin) VALUES (?, ?, ?, ?, ?, TRUE)""",
                    [f"builtin:{key}", a["name"], a.get("description"), a.get("tags", []),
                     spec.model_dump_json(exclude_defaults=True)])
    return len(items)


def list_analyses(con: duckdb.DuckDBPyConnection, tag: str | None = None) -> list[Saved]:
    rows = con.execute("""SELECT analysis_id, name, description, tags, spec, is_builtin
                          FROM saved_analysis
                          WHERE ? IS NULL OR list_contains(tags, ?)
                          ORDER BY is_builtin, lower(name)""", [tag, tag]).fetchall()
    return [_row(r) for r in rows]


def get(con: duckdb.DuckDBPyConnection, analysis_id: str) -> Saved:
    rows = con.execute("""SELECT analysis_id, name, description, tags, spec, is_builtin
                          FROM saved_analysis WHERE analysis_id = ?""", [analysis_id]).fetchall()
    if not rows:
        raise StoreError("explorer.store.not_found")
    return _row(rows[0])


def save(con: duckdb.DuckDBPyConnection, name: str, spec: AnalysisSpec, *,
         description: str = "", tags: list[str] | None = None,
         analysis_id: str | None = None, model: SemanticModel | None = None) -> str:
    """Create or update a USER analysis (built-ins are read-only → duplicate them)."""
    validate(spec, model or load_model())
    name = name.strip()
    if not name:
        raise StoreError("explorer.store.name_required")
    if analysis_id and analysis_id.startswith("builtin:"):
        raise StoreError("explorer.store.builtin_readonly")
    payload = spec.model_dump_json(exclude_defaults=True)
    tags = sorted({t.strip() for t in (tags or []) if t.strip()})
    if analysis_id:
        con.execute("""UPDATE saved_analysis SET name = ?, description = ?, tags = ?, spec = ?,
                       updated_at = current_timestamp WHERE analysis_id = ? AND NOT is_builtin""",
                    [name, description, tags, payload, analysis_id])
        return analysis_id
    new_id = f"user:{uuid.uuid4().hex[:12]}"
    con.execute("""INSERT INTO saved_analysis (analysis_id, name, description, tags, spec)
                   VALUES (?, ?, ?, ?, ?)""", [new_id, name, description, tags, payload])
    return new_id


def duplicate(con: duckdb.DuckDBPyConnection, analysis_id: str, new_name: str | None = None) -> str:
    src = get(con, analysis_id)
    return save(con, new_name or f"{src.name} (másolat)", src.spec,
                description=src.description, tags=src.tags)


def delete(con: duckdb.DuckDBPyConnection, analysis_id: str) -> None:
    if analysis_id.startswith("builtin:"):
        raise StoreError("explorer.store.builtin_readonly")
    con.execute("DELETE FROM saved_analysis WHERE analysis_id = ? AND NOT is_builtin",
                [analysis_id])


def all_tags(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT unnest(tags) AS t FROM saved_analysis ORDER BY 1").fetchall()]
