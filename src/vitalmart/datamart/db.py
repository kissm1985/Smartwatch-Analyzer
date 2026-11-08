"""DuckDB connection handling, versioned migrations, backup and wipe."""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

import duckdb

from vitalmart.config import AppConfig
from vitalmart.datamart.sqlutil import fetch_row
from vitalmart.paths import MIGRATIONS_DIR

log = logging.getLogger(__name__)

_MIGRATION_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


def connect(cfg: AppConfig, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the database (creating the data dir) and apply pending migrations."""
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.db_path), read_only=read_only)
    # Always UTC inside the engine: TIMESTAMPTZ -> TIMESTAMP casts must not
    # depend on the machine's zone. Local time comes from tz_offset.
    con.execute("SET TimeZone = 'UTC'")
    # Bounded memory: spill to <data>/tmp instead of growing (large Apple exports).
    con.execute(f"SET memory_limit = '{cfg.database.memory_limit}'")
    con.execute("SET temp_directory = ?", [str(cfg.data_path / "tmp")])
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET enable_progress_bar = false")  # we report our own progress
    if cfg.database.threads:
        con.execute(f"SET threads = {int(cfg.database.threads)}")
    if not read_only:
        migrate(con)
        from vitalmart.datamart.seed import ensure_static_dims

        ensure_static_dims(con, cfg)
    return con


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(directory.glob("*.sql")):
        m = _MIGRATION_RE.match(p.name)
        if not m:
            raise ValueError(f"invalid migration file name: {p.name}")
        out.append((int(m.group(1)), m.group(2), p))
    versions = [v for v, _, _ in out]
    if len(versions) != len(set(versions)):
        raise ValueError("duplicate migration version")
    return out


def schema_version(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)"
    )
    row = fetch_row(con, "SELECT value FROM meta WHERE key = 'schema_version'")
    return int(row[0]) if row else 0


def migrate(con: duckdb.DuckDBPyConnection, directory: Path = MIGRATIONS_DIR) -> int:
    """Apply migrations newer than the stored schema version, each in a transaction."""
    current = schema_version(con)
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name VARCHAR, applied_at TIMESTAMP)"
    )
    for version, name, path in list_migrations(directory):
        if version <= current:
            continue
        log.info("applying migration %03d_%s", version, name)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(path.read_text(encoding="utf-8"))
            con.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, now()::TIMESTAMP)", [version, name]
            )
            con.execute(
                "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", [str(version)]
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        current = version
    return current


def backup(cfg: AppConfig, con: duckdb.DuckDBPyConnection | None = None) -> Path:
    """Copy the database file to data/backups/ (checkpointing first)."""
    if con is not None:
        con.execute("CHECKPOINT")
    cfg.backups_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = cfg.backups_path / f"vitalmart_{stamp}.duckdb"
    shutil.copy2(cfg.db_path, target)
    return target


def wipe(cfg: AppConfig) -> list[Path]:
    """Delete ALL health data: database (+WAL), exports, uploads and backups.

    The caller must close open connections first. Returns the removed paths.
    """
    removed: list[Path] = []
    for p in (cfg.db_path, cfg.db_path.with_name(cfg.db_path.name + ".wal")):
        if p.exists():
            p.unlink()
            removed.append(p)
    for d in (cfg.exports_path, cfg.backups_path, cfg.data_path / "uploads",
              cfg.data_path / "tmp"):
        if d.exists():
            shutil.rmtree(d)
            removed.append(d)
    return removed
