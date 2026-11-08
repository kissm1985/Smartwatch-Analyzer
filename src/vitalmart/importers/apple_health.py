"""Apple Health / Apple Watch export importer (export.zip, export folder or export.xml).

The XML is processed with ``lxml.etree.iterparse`` in streaming mode: each
element is cleared after use, so memory stays bounded regardless of file size.
Parsed rows are buffered in column lists and flushed to DuckDB temp tables via
Arrow every ``chunk_size`` rows; the merge into ``stg_apple_*`` (dedup by
``row_hash``) happens in SQL inside the caller's transaction.
"""

from __future__ import annotations

import io
import json
import logging
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb
import pyarrow as pa
from lxml import etree

from vitalmart.datamart.sqlutil import fetch_row
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
from vitalmart.importers.staging import classify_insert as _classify_insert
from vitalmart.importers.staging import sql_hash as _h

log = logging.getLogger(__name__)

MAIN_XML = "export.xml"          # English iOS; e.g. Hungarian iOS writes "exportálás.xml"
IGNORED_FILES = {"export_cda.xml"}


def _nfc(name: str) -> str:
    # zip members from iOS/macOS carry decomposed (NFD) accents
    return unicodedata.normalize("NFC", name).lower()


def is_main_name(name: str) -> bool:
    """Candidate for the main export XML: any .xml except the CDA document.

    The file name is localised by iOS (export.xml, exportálás.xml, ...), so the
    final decision is made by content (``<HealthData`` root) where possible.
    """
    n = _nfc(name)
    return n.endswith(".xml") and not n.endswith("_cda.xml") and "cda" not in n


SNIFF_BYTES = 64 * 1024


def _sniff(head: bytes) -> bool:
    # the root element follows a long internal DTD; the DOCTYPE comes first
    return b"<!DOCTYPE HealthData" in head or b"<HealthData" in head
TAGS = ("Record", "Workout", "ActivitySummary")
TS_FMT = "%Y-%m-%d %H:%M:%S %z"

REC_COLS = ["record_type", "source_name", "source_version", "device", "unit", "value_text",
            "creation_date_raw", "start_date_raw", "end_date_raw", "metadata"]
WK_COLS = ["seq", "activity_type", "duration", "duration_unit", "total_distance",
           "total_distance_unit", "total_energy", "total_energy_unit", "source_name",
           "source_version", "device", "creation_date_raw", "start_date_raw", "end_date_raw",
           "metadata", "events", "route_files", "stats_sig"]
WSTAT_COLS = ["wk_seq", "stat_type", "start_date_raw", "end_date_raw", "stat_sum", "stat_avg",
              "stat_min", "stat_max", "unit"]
SUM_COLS = ["date_components", "active_energy", "active_energy_goal", "active_energy_unit",
            "move_time", "move_time_goal", "exercise_time", "exercise_time_goal", "stand_hours",
            "stand_hours_goal"]


class _CountingReader(io.RawIOBase):
    """File-like wrapper reporting read progress (bytes consumed / total)."""

    def __init__(self, raw: Any, total: int, progress: ProgressFn | None) -> None:
        self._raw = raw
        self._total = max(total, 1)
        self._progress = progress
        self._pos = 0
        self._last = -1.0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data: bytes = self._raw.read(size)
        self._pos += len(data)
        if self._progress:
            frac = min(self._pos / self._total, 1.0)
            if frac - self._last >= 0.01 or frac == 1.0:
                self._last = frac
                self._progress(frac)
        return data


def _iter_elements(stream: Any) -> Iterator[Any]:
    ctx = etree.iterparse(
        stream, events=("end",), tag=TAGS, huge_tree=True,
        resolve_entities=False, no_network=True, load_dtd=False,
    )
    for _, elem in ctx:
        yield elem
        elem.clear(keep_tail=False)
        parent = elem.getparent()
        if parent is not None:
            while elem.getprevious() is not None:
                del parent[0]


def _metadata(elem: Any) -> str | None:
    md = {c.get("key"): c.get("value") for c in elem.iterchildren("MetadataEntry")}
    return json.dumps(md, ensure_ascii=False, sort_keys=True) if md else None


class AppleHealthAdapter(SourceAdapter):
    name = "apple_health"
    vendor = "Apple"

    # ------------------------------------------------------------ detection
    @classmethod
    def can_handle(cls, path: Path) -> bool:
        try:
            if path.is_dir():
                return cls._find_dir_xml(path) is not None
            if path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as zf:
                    return cls._find_zip_xml(zf) is not None
            if path.suffix.lower() == ".xml" and is_main_name(path.name):
                with path.open("rb") as fh:
                    return _sniff(fh.read(SNIFF_BYTES))
        except (OSError, zipfile.BadZipFile):
            return False
        return False

    @staticmethod
    def _find_zip_xml(zf: zipfile.ZipFile) -> zipfile.ZipInfo | None:
        cands = [i for i in zf.infolist()
                 if not i.is_dir() and is_main_name(PurePosixPath(i.filename).name)
                 and "workout-routes" not in i.filename]
        # prefer the canonical name, then the shallowest path
        cands.sort(key=lambda i: (_nfc(PurePosixPath(i.filename).name) != MAIN_XML,
                                  i.filename.count("/"), len(i.filename)))
        for info in cands:
            with zf.open(info) as fh:
                if _sniff(fh.read(SNIFF_BYTES)):
                    return info
        return None

    @staticmethod
    def _find_dir_xml(path: Path) -> Path | None:
        for folder in (path, path / "apple_health_export"):
            if not folder.is_dir():
                continue
            cands = sorted((f for f in folder.glob("*.xml") if is_main_name(f.name)),
                           key=lambda f: (_nfc(f.name) != MAIN_XML, f.name))
            for cand in cands:
                with cand.open("rb") as fh:
                    if _sniff(fh.read(SNIFF_BYTES)):
                        return cand
        return None

    def fingerprint(self, path: Path) -> str:
        if path.is_dir():
            xml = self._find_dir_xml(path)
            if xml is None:
                raise ImportFailure("import.unknown_source")
            return sha256_file(xml)
        return sha256_file(path)

    # ------------------------------------------------------------ discovery
    @staticmethod
    def _classify(rel: str) -> str:
        p = PurePosixPath(rel)
        if _nfc(p.name).endswith("_cda.xml") or p.name in IGNORED_FILES:
            return "ignored"
        if is_main_name(p.name) and "workout-routes" not in p.parts:
            return "main"
        if "workout-routes" in p.parts or p.suffix.lower() == ".gpx":
            return "route"
        if "electrocardiograms" in p.parts and p.suffix.lower() == ".csv":
            return "ecg"
        return "other"

    def discover(self, path: Path) -> Discovery:
        disc = Discovery(adapter=self.name, vendor=self.vendor)
        if path.is_dir():
            for f in sorted(p for p in path.rglob("*") if p.is_file()):
                rel = f.relative_to(path).as_posix()
                disc.files.append(DiscoveredFile(rel, self._classify(rel), f.stat().st_size))
        elif path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(path) as zf:
                    for info in zf.infolist():
                        if not info.is_dir():
                            disc.files.append(DiscoveredFile(
                                info.filename, self._classify(info.filename), info.file_size))
            except zipfile.BadZipFile as exc:
                raise ImportFailure("import.bad_zip", str(exc)) from exc
        else:
            disc.files.append(DiscoveredFile(path.name, "main", path.stat().st_size))
        if not any(f.kind == "main" for f in disc.files):
            raise ImportFailure("import.missing_export_xml")
        if any(f.kind == "ignored" for f in disc.files):
            disc.warnings.append("export_cda.xml figyelmen kívül hagyva (SPEC 4.1).")
        n_routes = sum(f.kind == "route" for f in disc.files)
        if n_routes:
            disc.warnings.append(
                f"{n_routes} útvonalfájl (GPX) – csak nyilvántartásba vétel, feldolgozás később.")
        return disc

    @contextmanager
    def _open_xml(self, path: Path, progress: ProgressFn | None) -> Iterator[Any]:
        try:
            if path.is_dir():
                xml = self._find_dir_xml(path)
                if xml is None:
                    raise ImportFailure("import.missing_export_xml")
                with xml.open("rb") as fh:
                    yield _CountingReader(fh, xml.stat().st_size, progress)
            elif path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as zf:
                    info = self._find_zip_xml(zf)
                    if info is None:
                        raise ImportFailure("import.missing_export_xml")
                    with zf.open(info) as fh:
                        yield _CountingReader(fh, info.file_size, progress)
            else:
                with path.open("rb") as fh:
                    yield _CountingReader(fh, path.stat().st_size, progress)
        except zipfile.BadZipFile as exc:
            raise ImportFailure("import.bad_zip", str(exc)) from exc

    def _elements(self, path: Path, progress: ProgressFn | None) -> Iterator[Any]:
        with self._open_xml(path, progress) as stream:
            try:
                yield from _iter_elements(stream)
            except etree.XMLSyntaxError as exc:
                raise ImportFailure("import.bad_xml", f"line {exc.lineno}") from exc
            except (zipfile.BadZipFile, EOFError) as exc:
                raise ImportFailure("import.bad_zip", type(exc).__name__) from exc

    # ------------------------------------------------------------ preview
    def preview(self, path: Path, progress: ProgressFn | None = None) -> Preview:
        pv = Preview(discovery=self.discover(path))
        lo: str | None = None
        hi: str | None = None
        for elem in self._elements(path, progress):
            key = elem.get("type") if elem.tag == "Record" else elem.tag
            if elem.tag == "Workout":
                key = f"Workout:{elem.get('workoutActivityType')}"
            pv.type_counts[key] = pv.type_counts.get(key, 0) + 1
            d = (elem.get("startDate") or elem.get("dateComponents") or "")[:10]
            if d:
                lo = d if lo is None or d < lo else lo
                hi = d if hi is None or d > hi else hi
        pv.total_records = sum(pv.type_counts.values())
        pv.date_from, pv.date_to = lo, hi
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
        _create_load_tables(con)
        buf = _Buffers()
        wk_seq = 0
        for elem in self._elements(path, progress):
            tag = elem.tag
            if tag == "Record":
                buf.add_record(elem)
            elif tag == "Workout":
                wk_seq += 1
                buf.add_workout(elem, wk_seq)
            elif tag == "ActivitySummary":
                buf.add_summary(elem)
            if buf.size >= chunk_size:
                buf.flush(con)
        buf.flush(con)

        counts = _merge_staging(con, batch_id)
        self._load_side_files(con, path, disc, batch_id)
        return counts

    def _load_side_files(
        self, con: duckdb.DuckDBPyConnection, path: Path, disc: Discovery, batch_id: int
    ) -> None:
        if disc.files:
            con.executemany(
                "INSERT INTO stg_source_file VALUES (?, ?, ?, ?)",
                [(batch_id, f.path, f.kind, f.size_bytes) for f in disc.files],
            )
        ecgs = [f for f in disc.files if f.kind == "ecg"]
        if not ecgs:
            return
        rows = []
        for f in ecgs:
            if path.is_dir():
                content = (path / f.path).read_text(encoding="utf-8", errors="replace")
            else:
                with zipfile.ZipFile(path) as zf:
                    content = zf.read(f.path).decode("utf-8", errors="replace")
            rows.append((batch_id, f.path, content))
        con.executemany(
            "INSERT INTO stg_apple_ecg SELECT md5(?3), ?1, ?2, ?3 "
            "WHERE NOT EXISTS (SELECT 1 FROM stg_apple_ecg WHERE row_hash = md5(?3))",
            rows,
        )


class _Buffers:
    def __init__(self) -> None:
        self.rec: dict[str, list[Any]] = {c: [] for c in REC_COLS}
        self.wk: dict[str, list[Any]] = {c: [] for c in WK_COLS}
        self.wstat: dict[str, list[Any]] = {c: [] for c in WSTAT_COLS}
        self.sum: dict[str, list[Any]] = {c: [] for c in SUM_COLS}
        self.size = 0

    def add_record(self, e: Any) -> None:
        g = e.get
        r = self.rec
        r["record_type"].append(g("type"))
        r["source_name"].append(g("sourceName"))
        r["source_version"].append(g("sourceVersion"))
        r["device"].append(g("device"))
        r["unit"].append(g("unit"))
        r["value_text"].append(g("value"))
        r["creation_date_raw"].append(g("creationDate"))
        r["start_date_raw"].append(g("startDate"))
        r["end_date_raw"].append(g("endDate"))
        r["metadata"].append(_metadata(e) if len(e) else None)
        self.size += 1

    def add_workout(self, e: Any, seq: int) -> None:
        g = e.get
        events = [dict(ev.attrib) for ev in e.iterchildren("WorkoutEvent")]
        routes = [
            fr.get("path")
            for wr in e.iterchildren("WorkoutRoute")
            for fr in wr.iterchildren("FileReference")
        ]
        stats = []
        for st in e.iterchildren("WorkoutStatistics"):
            sg = st.get
            vals = (seq, sg("type"), sg("startDate"), sg("endDate"), sg("sum"), sg("average"),
                    sg("minimum"), sg("maximum"), sg("unit"))
            for c, v in zip(WSTAT_COLS, vals, strict=True):
                self.wstat[c].append(v)
            stats.append(dict(st.attrib))
        w = self.wk
        for c, v in (
            ("seq", seq), ("activity_type", g("workoutActivityType")),
            ("duration", g("duration")), ("duration_unit", g("durationUnit")),
            ("total_distance", g("totalDistance")),
            ("total_distance_unit", g("totalDistanceUnit")),
            ("total_energy", g("totalEnergyBurned")),
            ("total_energy_unit", g("totalEnergyBurnedUnit")),
            ("source_name", g("sourceName")), ("source_version", g("sourceVersion")),
            ("device", g("device")), ("creation_date_raw", g("creationDate")),
            ("start_date_raw", g("startDate")), ("end_date_raw", g("endDate")),
            ("metadata", _metadata(e)),
            ("events", json.dumps(events, ensure_ascii=False) if events else None),
            ("route_files", json.dumps(routes, ensure_ascii=False) if routes else None),
            ("stats_sig", json.dumps(stats, sort_keys=True) if stats else None),
        ):
            w[c].append(v)
        self.size += 1 + len(stats)

    def add_summary(self, e: Any) -> None:
        g = e.get
        for c, attr in zip(SUM_COLS, (
            "dateComponents", "activeEnergyBurned", "activeEnergyBurnedGoal",
            "activeEnergyBurnedUnit", "appleMoveTime", "appleMoveTimeGoal", "appleExerciseTime",
            "appleExerciseTimeGoal", "appleStandHours", "appleStandHoursGoal",
        ), strict=True):
            self.sum[c].append(g(attr))
        self.size += 1

    def flush(self, con: duckdb.DuckDBPyConnection) -> None:
        for name, data, schema in (
            ("_load_rec", self.rec, None),
            ("_load_wk", self.wk, {"seq": pa.int64()}),
            ("_load_wstat", self.wstat, {"wk_seq": pa.int64()}),
            ("_load_sum", self.sum, None),
        ):
            first = next(iter(data.values()))
            if not first:
                continue
            arrays = {
                c: pa.array(v, type=(schema or {}).get(c, pa.string())) for c, v in data.items()
            }
            tbl = pa.table(arrays)
            con.register("_chunk", tbl)
            try:
                con.execute(f"INSERT INTO {name} SELECT * FROM _chunk")
            finally:
                con.unregister("_chunk")
            for v in data.values():
                v.clear()
        self.size = 0


def _create_load_tables(con: duckdb.DuckDBPyConnection) -> None:
    def cols(names: list[str], ints: tuple[str, ...] = ()) -> str:
        return ", ".join(f"{c} {'BIGINT' if c in ints else 'VARCHAR'}" for c in names)

    con.execute(f"CREATE OR REPLACE TEMP TABLE _load_rec ({cols(REC_COLS)})")
    con.execute(f"CREATE OR REPLACE TEMP TABLE _load_wk ({cols(WK_COLS, ('seq',))})")
    con.execute(f"CREATE OR REPLACE TEMP TABLE _load_wstat ({cols(WSTAT_COLS, ('wk_seq',))})")
    con.execute(f"CREATE OR REPLACE TEMP TABLE _load_sum ({cols(SUM_COLS)})")


def _ts(col: str) -> str:
    return f"try_strptime({col}, '{TS_FMT}')::TIMESTAMP"


def _merge_staging(con: duckdb.DuckDBPyConnection, batch_id: int) -> LoadCounts:
    """Hash, classify (new/updated/skipped) and insert loaded rows into staging."""
    total = LoadCounts()

    # ---- records
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _rec_h AS
        WITH p AS (
            SELECT *, {_ts('start_date_raw')} AS start_utc, {_ts('end_date_raw')} AS end_utc,
                   {_ts('creation_date_raw')} AS creation_utc,
                   try_cast(value_text AS DOUBLE) AS value_num
            FROM _load_rec
        ), k AS (
            SELECT *, {_h('record_type', 'source_name', 'start_utc', 'end_utc',
                          'CASE WHEN value_num IS NULL THEN value_text END')} AS nk_hash
            FROM p
        )
        SELECT *, {_h('nk_hash', 'value_text', 'unit', 'source_version', 'device',
                      'creation_utc', 'metadata')} AS row_hash
        FROM k
    """)
    con.execute("DROP TABLE _load_rec")  # free memory early (large exports)
    total.add(_classify_insert(con, "_rec_h", "stg_apple_record", batch_id, """
        row_hash, nk_hash, {batch}, record_type, source_name, source_version, device, unit,
        value_text, value_num, creation_date_raw, start_date_raw, end_date_raw, start_utc,
        end_utc, metadata
    """))
    rng = fetch_row(con, "SELECT min(start_utc), max(start_utc) FROM _rec_h")
    con.execute("DROP TABLE _rec_h")

    # ---- workouts (+ statistics)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _wk_h AS
        WITH p AS (
            SELECT *, {_ts('start_date_raw')} AS start_utc, {_ts('end_date_raw')} AS end_utc,
                   {_ts('creation_date_raw')} AS creation_utc
            FROM _load_wk
        ), k AS (
            SELECT *, {_h('activity_type', 'source_name', 'start_utc', 'end_utc')} AS nk_hash
            FROM p
        )
        SELECT *, {_h('nk_hash', 'duration', 'duration_unit', 'total_distance',
                      'total_distance_unit', 'total_energy', 'total_energy_unit',
                      'source_version', 'device', 'creation_utc', 'metadata', 'events',
                      'route_files', 'stats_sig')} AS row_hash
        FROM k
    """)
    total.add(_classify_insert(con, "_wk_h", "stg_apple_workout", batch_id, """
        row_hash, nk_hash, {batch}, activity_type, try_cast(duration AS DOUBLE), duration_unit,
        try_cast(total_distance AS DOUBLE), total_distance_unit,
        try_cast(total_energy AS DOUBLE), total_energy_unit, source_name, source_version,
        device, creation_date_raw, start_date_raw, end_date_raw, start_utc, end_utc, metadata,
        events, route_files
    """, after_insert=f"""
        INSERT INTO stg_apple_workout_stat
        SELECT n.row_hash, {batch_id}, s.stat_type, s.start_date_raw, s.end_date_raw,
               try_cast(s.stat_sum AS DOUBLE), try_cast(s.stat_avg AS DOUBLE),
               try_cast(s.stat_min AS DOUBLE), try_cast(s.stat_max AS DOUBLE), s.unit
        FROM _new n JOIN _load_wstat s ON s.wk_seq = n.seq
    """))
    wk_rng = fetch_row(con, "SELECT min(start_utc), max(start_utc) FROM _wk_h")
    starts = [x for x in (*(rng or ()), *(wk_rng or ())) if x is not None]
    if starts:
        total.first_utc, total.last_utc = min(starts), max(starts)

    # ---- activity summaries (daily rings)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _sum_h AS
        SELECT *, try_strptime(date_components, '%Y-%m-%d') AS start_utc,
               {_h('date_components')} AS nk_hash,
               {_h(*SUM_COLS)} AS row_hash
        FROM _load_sum
    """)
    total.add(_classify_insert(con, "_sum_h", "stg_apple_activity_summary", batch_id, """
        row_hash, nk_hash, {batch}, date_components, try_cast(active_energy AS DOUBLE),
        try_cast(active_energy_goal AS DOUBLE), active_energy_unit,
        try_cast(move_time AS DOUBLE), try_cast(move_time_goal AS DOUBLE),
        try_cast(exercise_time AS DOUBLE), try_cast(exercise_time_goal AS DOUBLE),
        try_cast(stand_hours AS DOUBLE), try_cast(stand_hours_goal AS DOUBLE)
    """))
    return total
