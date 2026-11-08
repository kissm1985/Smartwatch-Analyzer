"""Common interface for source adapters (SPEC 3: SourceAdapter.discover/.parse)."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

ProgressFn = Callable[[float], None]


class ImportFailure(Exception):
    """Handled import error; ``i18n_key`` names a user-facing (Hungarian) message."""

    def __init__(self, i18n_key: str, detail: str = "", **kwargs: str) -> None:
        super().__init__(detail or i18n_key)
        self.i18n_key = i18n_key
        self.kwargs = kwargs


@dataclass
class DiscoveredFile:
    path: str           # path inside the export (zip member or relative path)
    kind: str           # main | route | ecg | ignored | other
    size_bytes: int


@dataclass
class Discovery:
    adapter: str
    vendor: str
    files: list[DiscoveredFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Preview:
    discovery: Discovery
    type_counts: dict[str, int] = field(default_factory=dict)
    date_from: str | None = None
    date_to: str | None = None
    total_records: int = 0


@dataclass
class LoadCounts:
    read: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    first_utc: datetime | None = None
    last_utc: datetime | None = None

    def add(self, other: LoadCounts) -> None:
        self.read += other.read
        self.new += other.new
        self.updated += other.updated
        self.skipped += other.skipped
        self.errors += other.errors


class SourceAdapter(ABC):
    """An export format. Implementations must be streaming and side-effect free
    except for writing into ``stg_*`` tables inside the caller's transaction."""

    name: str = "base"
    vendor: str = "unknown"

    @classmethod
    @abstractmethod
    def can_handle(cls, path: Path) -> bool: ...

    @abstractmethod
    def discover(self, path: Path) -> Discovery: ...

    @abstractmethod
    def preview(self, path: Path, progress: ProgressFn | None = None) -> Preview: ...

    @abstractmethod
    def load_staging(
        self,
        con: duckdb.DuckDBPyConnection,
        path: Path,
        batch_id: int,
        chunk_size: int,
        progress: ProgressFn | None = None,
    ) -> LoadCounts:
        """Parse ``path`` and merge into staging. Must not commit."""

    def fingerprint(self, path: Path) -> str:
        """SHA-256 of the input (file, or the main file of a directory export)."""
        return sha256_file(path)


def sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(bufsize):
            h.update(chunk)
    return h.hexdigest()
