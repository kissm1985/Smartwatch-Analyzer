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
from vitalmart.settings import deep_merge, load_settings


class SleepConfig(BaseModel):
    day_convention: Literal["wake", "start"] = "wake"
    goal_hours: float = Field(7.5, gt=0, le=24)
    session_gap_minutes: int = Field(90, ge=1, le=24 * 60)
    # single sleep intervals longer than this are implausible (kept, flagged, not used)
    max_interval_hours: float = Field(16, gt=0, le=48)


class ImportConfig(BaseModel):
    chunk_size: int = Field(50_000, ge=100)


class DerivedConfig(BaseModel):
    source_priority: list[str] = Field(
        default_factory=lambda: ["watch", "phone", "summary", "app", "other"])
    primary_source: dict[str, str] = Field(default_factory=dict)
    # sources left out of the derived layer, the report and the Explorer (data is kept)
    excluded_sources: list[str] = Field(default_factory=list)
    baseline_window_days: int = Field(60, ge=7, le=365)
    baseline_min_days: int = Field(28, ge=2, le=365)
    band_z: float = Field(1.5, gt=0)
    regularity_min_nights: int = Field(4, ge=2, le=14)
    acwr_acute_days: int = Field(7, ge=1, le=28)
    acwr_chronic_days: int = Field(28, ge=7, le=120)


class QualityConfig(BaseModel):
    gap_min_days: int = Field(3, ge=1)
    nonwear_gap_hours: float = Field(3, gt=0)
    constant_run_samples: int = Field(30, ge=3)
    switch_min_days: int = Field(3, ge=1)


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
    # Local UI port (127.0.0.1 only); if taken, the next free port is used.
    ui_port: int = Field(8610, ge=1024, le=65535)
    sleep: SleepConfig = Field(default_factory=lambda: SleepConfig.model_validate({}))
    import_: ImportConfig = Field(
        default_factory=lambda: ImportConfig.model_validate({}), alias="import"
    )
    database: DatabaseConfig = Field(default_factory=lambda: DatabaseConfig.model_validate({}))
    derived: DerivedConfig = Field(default_factory=lambda: DerivedConfig.model_validate({}))
    quality: QualityConfig = Field(default_factory=lambda: QualityConfig.model_validate({}))
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


def load_config(
    path: Path | None = None, *, use_user_settings: bool = True, **overrides: Any,
) -> AppConfig:
    """Load app config: config/app.yaml < env vars < <data_dir>/settings.yaml < overrides.

    ``VITALMART_CONFIG`` / ``VITALMART_DATA_DIR`` env vars select the files.
    """
    path = path or Path(os.environ.get("VITALMART_CONFIG", CONFIG_DIR / "app.yaml"))
    data: dict[str, Any] = load_yaml_file(path) if path.exists() else {}
    if env_dir := os.environ.get("VITALMART_DATA_DIR"):
        data["data_dir"] = env_dir
    if "data_dir" in overrides:
        data["data_dir"] = overrides["data_dir"]
    # user overlay from <data_dir>/settings.yaml (git-ignored), then explicit overrides
    if use_user_settings:
        probe = AppConfig.model_validate({"data_dir": data.get("data_dir", "data")})
        data = deep_merge(data, load_settings(probe.data_path))
    data = deep_merge(data, overrides)
    data.pop("rules_parameters", None)
    return AppConfig.model_validate(data)


@lru_cache(maxsize=1)
def default_config() -> AppConfig:
    return load_config()
