"""Shared fixtures. Only synthetic data is used (CLAUDE.md / SPEC 10.4)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from xml.sax.saxutils import quoteattr

import duckdb
import pytest

from vitalmart.config import AppConfig, load_config
from vitalmart.datamart import db
from vitalmart.sample_data import SampleSpec, write_export_zip

# Small but DST-spanning sample: 2024-03-20 .. 2024-04-08 (spring forward on 03-31)
SMALL_SPEC = SampleSpec(start=date(2024, 3, 20), days=20, seed=7, hr_interval_min=30)


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return load_config(data_dir=str(tmp_path / "data"))


@pytest.fixture
def con(cfg: AppConfig) -> Iterator[duckdb.DuckDBPyConnection]:
    c = db.connect(cfg)
    yield c
    c.close()


@pytest.fixture(scope="session")
def sample_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_export_zip(tmp_path_factory.mktemp("sample"), SMALL_SPEC)


WATCH = "Watch"
DEV = "<<HKDevice: 0x0>, name:Apple Watch, manufacturer:Apple Inc., model:Watch, hardware:Watch6,2, software:10.3>"


def rec(rtype: str, start: str, end: str | None = None, value: object = None,
        unit: str | None = None, source: str = WATCH, metadata: dict[str, str] | None = None,
        device: str | None = DEV) -> str:
    """One <Record> element. Short type names get the HKQuantityTypeIdentifier prefix."""
    if not rtype.startswith("HK"):
        rtype = f"HKQuantityTypeIdentifier{rtype}"
    attrs = {"type": rtype, "sourceName": source, "sourceVersion": "10.3"}
    if device:
        attrs["device"] = device
    if unit:
        attrs["unit"] = unit
    attrs.update(creationDate=end or start, startDate=start, endDate=end or start)
    if value is not None:
        attrs["value"] = str(value)
    a = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items())
    if metadata:
        md = "".join(f'<MetadataEntry key="{k}" value="{v}"/>' for k, v in metadata.items())
        return f"<Record {a}>{md}</Record>"
    return f"<Record {a}/>"


def sleep(stage: str, start: str, end: str, source: str = WATCH) -> str:
    return rec("HKCategoryTypeIdentifierSleepAnalysis", start, end,
               value=f"HKCategoryValueSleepAnalysis{stage}", source=source)


def apple_xml(*elements: str) -> str:
    body = "\n".join(elements)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE HealthData [\n<!ELEMENT HealthData ANY>\n]>\n"
        '<HealthData locale="hu_HU">\n<ExportDate value="2024-12-01 10:00:00 +0100"/>\n'
        f"{body}\n</HealthData>\n"
    )


@pytest.fixture
def write_xml(tmp_path: Path):
    counter = {"n": 0}

    def _write(*elements: str, name: str | None = None) -> Path:
        counter["n"] += 1
        p = tmp_path / (name or f"export_{counter['n']}.xml")
        p.write_text(apple_xml(*elements), encoding="utf-8")
        return p

    return _write


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):
    rows = con.execute(sql, params or []).fetchall()
    return rows[0][0] if rows else None
