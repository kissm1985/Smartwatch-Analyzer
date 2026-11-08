"""Import orchestration: detection, preview, transactional load + core rebuild."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import duckdb

from vitalmart.config import AppConfig
from vitalmart.datamart.sqlutil import fetch_row
from vitalmart.i18n import t
from vitalmart.importers import ADAPTERS
from vitalmart.importers.base import ImportFailure, LoadCounts, Preview, ProgressFn, SourceAdapter
from vitalmart.timeutil import to_local
from vitalmart.transform.rebuild import rebuild_core

log = logging.getLogger(__name__)


PERSON_RE = re.compile(r"^[a-z0-9_]{1,32}$")


@dataclass
class ImportResult:
    status: str                      # success | skipped | failed
    adapter: str
    batch_id: int | None = None
    counts: LoadCounts = field(default_factory=LoadCounts)
    date_from: date | None = None
    date_to: date | None = None
    message: str = ""                # user-facing (i18n) message
    previous_batch_id: int | None = None
    person: str | None = None


def get_adapter(name: str) -> SourceAdapter:
    for cls in ADAPTERS:
        if cls.name == name:
            return cls()
    raise ValueError(f"unknown adapter: {name}")


def detect_adapter(path: Path) -> SourceAdapter:
    if not path.exists():
        raise ImportFailure("import.not_found", path=str(path))
    for cls in ADAPTERS:
        if cls.can_handle(path):
            return cls()
    raise ImportFailure("import.unknown_source")


def preview(
    path: Path, adapter_name: str | None = None, progress: ProgressFn | None = None
) -> Preview:
    adapter = get_adapter(adapter_name) if adapter_name else detect_adapter(path)
    return adapter.preview(path, progress)


def _local_date(ts: datetime | None, tz: str) -> date | None:
    return to_local(ts, tz)[0].date() if ts else None


def run_import(
    con: duckdb.DuckDBPyConnection,
    cfg: AppConfig,
    path: Path,
    *,
    force: bool = False,
    adapter_name: str | None = None,
    progress: ProgressFn | None = None,
    person: str | None = None,
) -> ImportResult:
    """Load ``path`` into staging and rebuild the core layer in ONE transaction.

    ``person``: whose device(s) the export belongs to (default: ``cfg.default_person``).
    A device already attributed to another person makes the import fail (rollback).

    Same file (SHA-256) twice → skipped unless ``force``. Any error rolls back
    everything; the failure is then logged in dim_import_batch on its own.
    """
    path = Path(path)
    person = person or cfg.default_person
    if not PERSON_RE.match(person):
        raise ImportFailure("import.invalid_person", person=person)
    adapter = get_adapter(adapter_name) if adapter_name else detect_adapter(path)
    sha = adapter.fingerprint(path)

    prev = fetch_row(
        con,
        "SELECT max(import_batch_id) FROM dim_import_batch WHERE sha256 = ? AND status = 'success'",
        [sha],
    )
    prev_id = prev[0] if prev else None
    if prev_id is not None and not force:
        batch_id = _next_batch_id(con)
        con.execute(
            "INSERT INTO dim_import_batch (import_batch_id, started_at, finished_at, vendor, "
            "adapter, file_name, file_path, sha256, status, message, person_key) "
            "VALUES (?, now()::TIMESTAMP, now()::TIMESTAMP, ?, ?, ?, ?, ?, 'skipped', ?, ?)",
            [batch_id, adapter.vendor, adapter.name, path.name, str(path), sha,
             f"duplicate of batch {prev_id}", person],
        )
        log.warning("import skipped: identical file already loaded (batch %s)", prev_id)
        return ImportResult(
            status="skipped", adapter=adapter.name, batch_id=batch_id,
            previous_batch_id=prev_id, message=t("import.duplicate_file", batch=prev_id),
        )

    batch_id = _next_batch_id(con)
    log.info("import batch %s started (adapter=%s)", batch_id, adapter.name)
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            "INSERT INTO dim_person (person_key) SELECT ? "
            "WHERE NOT EXISTS (SELECT 1 FROM dim_person WHERE person_key = ?)",
            [person, person],
        )
        con.execute(
            "INSERT INTO dim_import_batch (import_batch_id, started_at, vendor, adapter, "
            "file_name, file_path, sha256, status, person_key) "
            "VALUES (?, now()::TIMESTAMP, ?, ?, ?, ?, ?, 'running', ?)",
            [batch_id, adapter.vendor, adapter.name, path.name, str(path), sha, person],
        )
        counts = adapter.load_staging(con, path, batch_id, cfg.import_.chunk_size, progress)
        rebuild_core(con, cfg)
        conflicts = [r[0] for r in con.execute(
            "SELECT source_key FROM dim_source WHERE person_conflict ORDER BY 1").fetchall()]
        if counts.matched_batches and not conflicts:
            ids = ",".join(str(int(b)) for b in sorted(counts.matched_batches))
            conflicts = [r[0] for r in con.execute(f"""
                SELECT DISTINCT s.source_key FROM dim_source s
                JOIN dim_import_batch b ON b.person_key = s.person_key
                WHERE b.import_batch_id IN ({ids}) AND b.person_key IS DISTINCT FROM ?
                  AND s.vendor = ?
                ORDER BY 1""", [person, adapter.vendor]).fetchall()] or [
                r[0] for r in con.execute(f"""
                SELECT DISTINCT 'batch ' || import_batch_id FROM dim_import_batch
                WHERE import_batch_id IN ({ids}) AND person_key IS DISTINCT FROM ?""",
                [person]).fetchall()]
        if conflicts:
            raise ImportFailure("import.person_conflict", sources=", ".join(conflicts))
        d_from = _local_date(counts.first_utc, cfg.timezone)
        d_to = _local_date(counts.last_utc, cfg.timezone)
        con.execute(
            "UPDATE dim_import_batch SET status = 'success', finished_at = now()::TIMESTAMP, "
            "records_read = ?, records_new = ?, records_updated = ?, records_skipped = ?, "
            "records_error = ?, date_from = ?, date_to = ? WHERE import_batch_id = ?",
            [counts.read, counts.new, counts.updated, counts.skipped, counts.errors,
             d_from, d_to, batch_id],
        )
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")
        reason_key = exc.i18n_key if isinstance(exc, ImportFailure) else None
        reason = t(reason_key, **exc.kwargs) if isinstance(exc, ImportFailure) and reason_key \
            else type(exc).__name__
        # technical detail only – never data values
        log.error("import batch %s failed: %s", batch_id, type(exc).__name__)
        con.execute(
            "INSERT INTO dim_import_batch (import_batch_id, started_at, finished_at, vendor, "
            "adapter, file_name, file_path, sha256, status, message, person_key) "
            "VALUES (?, now()::TIMESTAMP, now()::TIMESTAMP, ?, ?, ?, ?, ?, 'failed', ?, ?)",
            [batch_id, adapter.vendor, adapter.name, path.name, str(path), sha,
             type(exc).__name__ if reason_key is None else reason_key, person],
        )
        if isinstance(exc, ImportFailure):
            raise
        raise ImportFailure("import.failed", type(exc).__name__, reason=reason) from exc

    log.info("import batch %s finished: read=%s new=%s updated=%s skipped=%s errors=%s",
             batch_id, counts.read, counts.new, counts.updated, counts.skipped, counts.errors)
    return ImportResult(
        status="success", adapter=adapter.name, batch_id=batch_id, counts=counts,
        date_from=d_from, date_to=d_to, person=person,
        message=t("import.summary", batch=batch_id, read=counts.read, new=counts.new,
                  updated=counts.updated, skipped=counts.skipped, errors=counts.errors,
                  date_from=_fmt(d_from), date_to=_fmt(d_to)),
    )


def _next_batch_id(con: duckdb.DuckDBPyConnection) -> int:
    row = fetch_row(con, "SELECT nextval('seq_import_batch')")
    assert row is not None
    return int(row[0])


def _fmt(d: date | None) -> str:
    """Hungarian date format ÉÉÉÉ.HH.NN. (SPEC 10.5)."""
    return d.strftime("%Y.%m.%d.") if d else "–"
