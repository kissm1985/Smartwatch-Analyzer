"""Garmin importer: Garmin Connect data export (DI_CONNECT JSON) + FIT files.

Accepted inputs
* the export ZIP from Garmin Connect ("Adatok exportálása") or its extracted folder,
* nested ``UploadedFiles_*.zip`` archives inside it (uploaded activity FIT files),
* FIT files copied from the watch over USB (``GARMIN/Activity``, ``GARMIN/Monitor``)
  – a single ``.fit`` file or any folder containing them.

Recognition is filename pattern + content (SPEC 4.1/B) driven by
``config/garmin_mapping.yaml``. JSON records are staged verbatim (unknown fields
included) in ``stg_garmin_record``; FIT messages with all decoded fields in
``stg_garmin_fit_message``. Personal-profile folders (DI-Connect-User …) are only
inventoried, never read.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb
import fitdecode
import pyarrow as pa

from vitalmart.config import load_yaml_file
from vitalmart.datamart.sqlutil import fetch_row
from vitalmart.importers import fit
from vitalmart.importers.base import (
    DiscoveredFile,
    Discovery,
    ImportFailure,
    LoadCounts,
    Preview,
    ProgressFn,
    SourceAdapter,
    sha256_file,
)
from vitalmart.importers.staging import classify_insert
from vitalmart.paths import CONFIG_DIR

log = logging.getLogger(__name__)

MAPPING_FILE = "garmin_mapping.yaml"


@dataclass
class Entry:
    rel: str                      # path inside the export ("a.zip!/b.fit" for nested)
    size: int
    read: Callable[[], bytes]


def load_mapping(config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    return load_yaml_file(config_dir / MAPPING_FILE)


def get_path(obj: Any, path: str) -> Any:
    """Plain dotted lookup (natural keys / content checks; no filters)."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def generic_kind(rel: str) -> str:
    stem = PurePosixPath(rel).stem
    stem = re.sub(r"^[^_]*@[^_]*_", "", stem)                 # e-mail prefix
    stem = re.sub(r"[^_]*@[^_]*", "", stem)
    stem = re.sub(r"\d{4}-\d{2}-\d{2}|\d{5,}", "", stem)  # dates / ids (ids, yyyymmdd)
    stem = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", stem)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    return stem or "unknown"


def extract_records(doc: Any, unwrap: str | None) -> list[dict[str, Any]]:
    if unwrap:
        out: list[dict[str, Any]] = []
        items = doc if isinstance(doc, list) else [doc]
        for it in items:
            if isinstance(it, dict) and isinstance(it.get(unwrap), list):
                out += [x for x in it[unwrap] if isinstance(x, dict)]
            elif isinstance(it, dict) and unwrap not in it:
                out.append(it)
        return out
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        lists = [v for v in doc.values() if isinstance(v, list)]
        if len(doc) == 1 and lists and all(isinstance(x, dict) for x in lists[0]):
            return list(lists[0])
        return [doc]
    return []


class GarminAdapter(SourceAdapter):
    name = "garmin"
    vendor = "Garmin"

    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        self.mapping = mapping or load_mapping()
        self._file_re = {k: re.compile(v["match"]["filename"])
                         for k, v in self.mapping["files"].items()}
        self._fit_zip_re = [re.compile(p) for p in self.mapping.get("fit_zip_patterns", [])]

    # ------------------------------------------------------------ entries
    @staticmethod
    def _zip_entries(zf: zipfile.ZipFile, prefix: str = "") -> Iterator[Entry]:
        for info in zf.infolist():
            if info.is_dir():
                continue
            yield Entry(prefix + info.filename, info.file_size, partial(zf.read, info))

    def _entries(self, path: Path, zf: zipfile.ZipFile | None) -> Iterator[Entry]:
        if zf is not None:
            yield from self._zip_entries(zf)
        elif path.is_dir():
            for f in sorted(p for p in path.rglob("*") if p.is_file()):
                yield Entry(f.relative_to(path).as_posix(), f.stat().st_size, f.read_bytes)
        else:
            yield Entry(path.name, path.stat().st_size, lambda: path.read_bytes())

    def _expand(self, entries: Iterator[Entry]) -> Iterator[Entry]:
        """Yield entries, descending into nested FIT zips (UploadedFiles_*.zip)."""
        for e in entries:
            name = PurePosixPath(e.rel).name
            if name.lower().endswith(".zip") and any(r.match(name) for r in self._fit_zip_re):
                try:
                    inner = zipfile.ZipFile(io.BytesIO(e.read()))
                except zipfile.BadZipFile as exc:
                    raise ImportFailure("import.bad_zip", e.rel) from exc
                yield from self._zip_entries(inner, prefix=e.rel + "!/")
            else:
                yield e

    def _open(self, path: Path) -> zipfile.ZipFile | None:
        if path.is_file() and path.suffix.lower() == ".zip":
            try:
                return zipfile.ZipFile(path)
            except zipfile.BadZipFile as exc:
                raise ImportFailure("import.bad_zip", str(exc)) from exc
        return None

    # ------------------------------------------------------------ classification
    def _folder_flags(self, rel: str) -> tuple[bool, bool]:
        parts = set(PurePosixPath(rel.split("!/")[0]).parts)
        excluded = bool(parts & set(self.mapping.get("exclude_folders", [])))
        included = bool(parts & set(self.mapping.get("include_folders", [])))
        return included, excluded

    def classify_name(self, rel: str) -> str:
        """Name-based kind: json:<kind> | json:generic | fit | ignored | other."""
        name = PurePosixPath(rel).name
        included, excluded = self._folder_flags(rel)
        if excluded:
            return "ignored"
        if name.lower().endswith(".fit"):
            return "fit"
        if name.lower().endswith(".json"):
            for kind, rx in self._file_re.items():
                if rx.match(name):
                    return f"json:{kind}"
            return "json:generic" if included else "ignored"
        return "other"

    def content_kind(self, name_kind: str, records: list[dict[str, Any]]) -> str | None:
        """Confirm a name-based match by content; returns the final kind or None."""
        if name_kind == "json:generic":
            return "generic"
        kind = name_kind.split(":", 1)[1]
        keys = self.mapping["files"][kind]["match"].get("any_keys", [])
        if not records:
            return kind  # empty file: nothing to check, nothing to load
        first = records[0]
        if not keys or any(get_path(first, k) is not None for k in keys):
            return kind
        return None

    @classmethod
    def can_handle(cls, path: Path) -> bool:
        try:
            inst = cls()
            zf = inst._open(path)
            for i, e in enumerate(inst._entries(path, zf)):
                kind = inst.classify_name(e.rel)
                if kind == "fit" or (kind.startswith("json:") and kind != "json:generic") \
                        or "DI_CONNECT" in e.rel:
                    return True
                if i > 20000:
                    break
        except (OSError, ImportFailure):
            return False
        return False

    def fingerprint(self, path: Path) -> str:
        if path.is_dir():
            h = hashlib.sha256()
            for f in sorted(p for p in path.rglob("*") if p.is_file()):
                h.update(f.relative_to(path).as_posix().encode())
                h.update(sha256_file(f).encode())
            return h.hexdigest()
        return sha256_file(path)

    # ------------------------------------------------------------ discovery / preview
    def discover(self, path: Path) -> Discovery:
        disc = Discovery(adapter=self.name, vendor=self.vendor)
        zf = self._open(path)
        for e in self._expand(self._entries(path, zf)):
            disc.files.append(DiscoveredFile(e.rel, self.classify_name(e.rel), e.size))
        if not any(f.kind.startswith("json:") or f.kind == "fit" for f in disc.files):
            raise ImportFailure("import.unknown_source")
        n_ign = sum(f.kind == "ignored" for f in disc.files)
        if n_ign:
            disc.warnings.append(
                f"{n_ign} fájl kihagyva (személyes profil / nem egészségügyi adat).")
        return disc

    def _parsed_json(self, e: Entry) -> tuple[str | None, list[dict[str, Any]]]:
        name_kind = self.classify_name(e.rel)
        try:
            doc = json.loads(e.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImportFailure("import.bad_json", e.rel, file=PurePosixPath(e.rel).name) \
                from exc
        unwrap = None
        if name_kind != "json:generic":
            unwrap = self.mapping["files"][name_kind.split(":", 1)[1]].get("unwrap")
        records = extract_records(doc, unwrap)
        kind = self.content_kind(name_kind, records)
        if kind == "generic":
            kind = "generic:" + generic_kind(e.rel)
        return kind, records

    def preview(self, path: Path, progress: ProgressFn | None = None) -> Preview:
        pv = Preview(discovery=self.discover(path))
        zf = self._open(path)
        dates: list[str] = []
        entries = list(self._expand(self._entries(path, zf)))
        n_fit = sum(self.classify_name(e.rel) == "fit" for e in entries)
        for i, e in enumerate(entries):
            nk = self.classify_name(e.rel)
            if nk.startswith("json:"):
                kind, recs = self._parsed_json(e)
                key = kind or f"unrecognised:{PurePosixPath(e.rel).name}"
                pv.type_counts[key] = pv.type_counts.get(key, 0) + len(recs)
                for r in recs:
                    for p in self.mapping.get("date_paths", []):
                        v = get_path(r, p)
                        if isinstance(v, str) and len(v) >= 10:
                            dates.append(v[:10])
                            break
            elif nk == "fit":
                try:
                    ff = fit.decode(e.read())
                except fitdecode.FitError as exc:
                    if n_fit == 1:
                        raise ImportFailure("import.bad_fit", e.rel,
                                            file=PurePosixPath(e.rel).name) from exc
                    pv.type_counts["fit:corrupt"] = pv.type_counts.get("fit:corrupt", 0) + 1
                    continue
                key = f"fit:{ff.file_type}"
                pv.type_counts[key] = pv.type_counts.get(key, 0) + 1
                dates += [m.ts_utc.date().isoformat() for m in ff.messages[:1] if m.ts_utc]
            if progress:
                progress((i + 1) / max(len(entries), 1))
        pv.total_records = sum(pv.type_counts.values())
        if dates:
            pv.date_from, pv.date_to = min(dates), max(dates)
        return pv

    # ------------------------------------------------------------ load
    def load_staging(
        self,
        con: duckdb.DuckDBPyConnection,
        path: Path,
        batch_id: int,
        chunk_size: int,
        progress: ProgressFn | None = None,
    ) -> LoadCounts:
        disc = self.discover(path)
        con.execute("""CREATE OR REPLACE TEMP TABLE _g_load (
            row_hash VARCHAR, nk_hash VARCHAR, file_kind VARCHAR, file_name VARCHAR,
            natural_key VARCHAR, payload VARCHAR)""")
        buf: dict[str, list[Any]] = {c: [] for c in (
            "row_hash", "nk_hash", "file_kind", "file_name", "natural_key", "payload")}
        total = LoadCounts()
        fit_files: list[tuple[str, bytes]] = []
        zf = self._open(path)
        entries = list(self._expand(self._entries(path, zf)))
        for i, e in enumerate(entries):
            nk = self.classify_name(e.rel)
            if nk.startswith("json:"):
                kind, recs = self._parsed_json(e)
                if kind is None:
                    log.warning("garmin file not recognised by content: %s", PurePosixPath(
                        e.rel).name)
                    total.errors += 1
                    continue
                base_kind = kind.split(":", 1)[0]
                nk_paths = (self.mapping["files"][base_kind].get("natural_key")
                            if base_kind in self.mapping["files"] else None)
                for r in recs:
                    payload = json.dumps(r, ensure_ascii=False, sort_keys=True)
                    natural = ("|".join(str(get_path(r, p)) for p in nk_paths)
                               if nk_paths else None)
                    buf["row_hash"].append(_md5(kind + "|" + payload))
                    buf["nk_hash"].append(_md5(kind + "|" + (natural or payload)))
                    buf["file_kind"].append(kind)
                    buf["file_name"].append(PurePosixPath(e.rel).name)
                    buf["natural_key"].append(natural)
                    buf["payload"].append(payload)
                if len(buf["row_hash"]) >= chunk_size:
                    _flush(con, "_g_load", buf)
            elif nk == "fit":
                fit_files.append((e.rel, e.read()))
            if progress:
                progress((i + 1) / max(len(entries), 1) * 0.5)
        _flush(con, "_g_load", buf)

        total.add(classify_insert(con, "_g_load", "stg_garmin_record", batch_id, """
            row_hash, nk_hash, {batch}, file_kind, file_name, natural_key, payload
        """, error_condition="false"))

        for j, (rel, data) in enumerate(fit_files):
            try:
                total.add(self._load_fit(con, rel, data, batch_id))
            except ImportFailure as exc:
                # a single standalone FIT must fail loudly; inside a multi-file export one
                # broken file (e.g. an interrupted upload) must not block everything else
                if exc.i18n_key != "import.bad_fit" or len(fit_files) == 1:
                    raise
                log.warning("corrupt FIT skipped: %s", PurePosixPath(rel).name)
                total.errors += 1
            if progress:
                progress(0.5 + (j + 1) / max(len(fit_files), 1) * 0.5)
        if progress:
            progress(1.0)

        # range: JSON calendar dates (as local noon) + FIT instants (UTC)
        rng = fetch_row(con, f"""
            SELECT min(t), max(t) FROM (
                SELECT CAST(try_cast(json_extract_string(payload, '$.calendarDate') AS DATE)
                            AS TIMESTAMP) + INTERVAL 12 HOUR AS t
                FROM stg_garmin_record WHERE import_batch_id = {batch_id}
                UNION ALL
                SELECT m.ts_utc FROM stg_garmin_fit_message m
                JOIN stg_garmin_fit_file f USING (file_hash)
                WHERE f.import_batch_id = {batch_id} AND m.ts_utc IS NOT NULL)
        """)
        if rng and rng[0] is not None:
            total.first_utc, total.last_utc = rng
        con.executemany(
            "INSERT INTO stg_source_file VALUES (?, ?, ?, ?)",
            [(batch_id, f.path, f.kind, f.size_bytes) for f in disc.files],
        )
        return total

    def _load_fit(
        self, con: duckdb.DuckDBPyConnection, rel: str, data: bytes, batch_id: int
    ) -> LoadCounts:
        file_hash = hashlib.sha256(data).hexdigest()
        exists = fetch_row(con, "SELECT import_batch_id FROM stg_garmin_fit_file "
                                "WHERE file_hash = ?", [file_hash])
        try:
            ff = fit.decode(data)
        except fitdecode.FitError as exc:
            raise ImportFailure("import.bad_fit", rel, file=PurePosixPath(rel).name) from exc
        n = len(ff.messages)
        if exists:
            return LoadCounts(read=n, skipped=n, matched_batches={int(exists[0])})
        con.execute(
            "INSERT INTO stg_garmin_fit_file VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [file_hash, batch_id, PurePosixPath(rel).name, ff.file_type, ff.manufacturer,
             ff.product, ff.serial_number, ff.time_created_utc],
        )
        cols: dict[str, list[Any]] = {"file_hash": [], "msg_index": [], "message": [],
                                      "ts_utc": [], "fields": []}
        for idx, name, ts, fields in fit.messages_json(ff.messages):
            cols["file_hash"].append(file_hash)
            cols["msg_index"].append(idx)
            cols["message"].append(name)
            cols["ts_utc"].append(ts)
            cols["fields"].append(fields)
        if n:
            tbl = pa.table({
                "file_hash": pa.array(cols["file_hash"], pa.string()),
                "msg_index": pa.array(cols["msg_index"], pa.int32()),
                "message": pa.array(cols["message"], pa.string()),
                "ts_utc": pa.array(cols["ts_utc"], pa.timestamp("us")),
                "fields": pa.array(cols["fields"], pa.string()),
            })
            con.register("_fitmsg", tbl)
            try:
                con.execute("INSERT INTO stg_garmin_fit_message SELECT * FROM _fitmsg")
            finally:
                con.unregister("_fitmsg")
        return LoadCounts(read=n, new=n)


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _flush(con: duckdb.DuckDBPyConnection, table: str, buf: dict[str, list[Any]]) -> None:
    if not buf["row_hash"]:
        return
    tbl = pa.table({k: pa.array(v, pa.string()) for k, v in buf.items()})
    con.register("_gchunk", tbl)
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM _gchunk")
    finally:
        con.unregister("_gchunk")
    for v in buf.values():
        v.clear()
