"""Application configuration (config/app.yaml) validated with pydantic."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, field_validator

from vitalmart.paths import CONFIG_DIR, PROJECT_ROOT


class SleepConfig(BaseModel):
    day_convention: Literal["wake", "start"] = "wake"
    goal_hours: float = Field(7.5, gt=0, le=24)
    session_gap_minutes: int = Field(90, ge=1, le=24 * 60)


class ImportConfig(BaseModel):
    chunk_size: int = Field(50_000, ge=100)


class DatabaseConfig(BaseModel):
    memory_limit: str = Field("2GB", pattern=r"^\d+(\.\d+)?\s*(KB|MB|GB|KiB|MiB|GiB)$")
    threads: int | None = Field(None, ge=1)


class AppConfig(BaseModel):
    data_dir: Path = Path("data")
    db_file: str = "vitalmart.duckdb"
    timezone: str = "Europe/Budapest"
    language: Literal["hu", "en"] = "hu"
    # Household: each export is attributed to a person (import --person); default below.
    default_person: str = Field("me", pattern=r"^[a-z0-9_]{1,32}$")
    sleep: SleepConfig = Field(default_factory=lambda: SleepConfig.model_validate({}))
    import_: ImportConfig = Field(
        default_factory=lambda: ImportConfig.model_validate({}), alias="import"
    )
    database: DatabaseConfig = Field(default_factory=lambda: DatabaseConfig.model_validate({}))
    config_dir: Path = CONFIG_DIR

    model_config = {"populate_by_name": True}

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {v}") from exc
        return v

    @property
    def data_path(self) -> Path:
        p = self.data_dir
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def db_path(self) -> Path:
        return self.data_path / self.db_file

    @property
    def exports_path(self) -> Path:
        return self.data_path / "exports"

    @property
    def backups_path(self) -> Path:
        return self.data_path / "backups"

    def load_yaml(self, name: str) -> dict[str, Any]:
        return load_yaml_file(self.config_dir / name)


def load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping at top level")
    return data


def load_config(path: Path | None = None, **overrides: Any) -> AppConfig:
    """Load app config. ``VITALMART_CONFIG`` / ``VITALMART_DATA_DIR`` env vars override."""
    path = path or Path(os.environ.get("VITALMART_CONFIG", CONFIG_DIR / "app.yaml"))
    data: dict[str, Any] = load_yaml_file(path) if path.exists() else {}
    if env_dir := os.environ.get("VITALMART_DATA_DIR"):
        data["data_dir"] = env_dir
    data.update(overrides)
    return AppConfig.model_validate(data)


@lru_cache(maxsize=1)
def default_config() -> AppConfig:
    return load_config()
