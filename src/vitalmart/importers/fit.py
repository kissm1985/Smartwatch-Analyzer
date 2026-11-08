"""FIT file decoding (Garmin activity + monitoring files) via ``fitdecode``.

Every data message is returned with ALL decoded fields (raw layer, SPEC 3) and a
resolved UTC timestamp. Monitoring files mostly carry the compressed
``timestamp_16`` field, which is expanded relative to the last full timestamp
(FIT SDK: ts = last + ((ts16 − last) & 0xFFFF)).
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import fitdecode

FIT_EPOCH = datetime(1989, 12, 31)


@dataclass
class FitMessage:
    index: int
    name: str
    ts_utc: datetime | None
    fields: dict[str, Any]


@dataclass
class FitFile:
    file_type: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None
    time_created_utc: datetime | None = None
    messages: list[FitMessage] = field(default_factory=list)


def _naive_utc(v: datetime) -> datetime:
    return v.astimezone(UTC).replace(tzinfo=None) if v.tzinfo else v


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return _naive_utc(v).isoformat()
    if isinstance(v, bytes | bytearray):
        return v.hex()
    if isinstance(v, tuple | list):
        return [_jsonable(x) for x in v]
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


def _fit_seconds(dt: datetime) -> int:
    return int((dt - FIT_EPOCH).total_seconds())


def decode(data: bytes) -> FitFile:
    """Decode FIT bytes. Raises ``fitdecode.FitError`` (subclasses) on corrupt files."""
    out = FitFile()
    last_ts: datetime | None = None
    idx = 0
    with fitdecode.FitReader(
        io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE,
        error_handling=fitdecode.ErrorHandling.RAISE, keep_raw_chunks=False,
    ) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            fields: dict[str, Any] = {}
            for f in frame.fields:
                if f.name in fields and fields[f.name] is not None:
                    continue
                fields[f.name] = _jsonable(f.value)
            ts: datetime | None = None
            raw_ts = frame.get_value("timestamp", fallback=None) if frame.has_field(
                "timestamp") else None
            if isinstance(raw_ts, datetime):
                ts = _naive_utc(raw_ts)
                last_ts = ts
            elif frame.has_field("timestamp_16") and last_ts is not None:
                ts16 = frame.get_value("timestamp_16", fallback=None)
                if isinstance(ts16, int):
                    base = _fit_seconds(last_ts)
                    ts = last_ts + timedelta(seconds=(ts16 - (base & 0xFFFF)) & 0xFFFF)
                    last_ts = ts
            if frame.name == "file_id":
                out.file_type = _s(fields.get("type"))
                out.manufacturer = _s(fields.get("manufacturer"))
                out.product = _s(fields.get("garmin_product") or fields.get("product"))
                sn = fields.get("serial_number")
                out.serial_number = None if sn is None else str(sn)
                tc = frame.get_value("time_created", fallback=None) if frame.has_field(
                    "time_created") else None
                out.time_created_utc = _naive_utc(tc) if isinstance(tc, datetime) else None
            if frame.name == "stress_level" and ts is None:
                st = frame.get_value("stress_level_time", fallback=None) if frame.has_field(
                    "stress_level_time") else None
                if isinstance(st, datetime):
                    ts = _naive_utc(st)
            out.messages.append(FitMessage(idx, frame.name, ts, fields))
            idx += 1
    return out


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def messages_json(msgs: list[FitMessage]) -> Iterator[tuple[int, str, datetime | None, str]]:
    for m in msgs:
        yield m.index, m.name, m.ts_utc, json.dumps(m.fields, ensure_ascii=False, sort_keys=True)
