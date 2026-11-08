"""Project-relative locations of bundled resources (config, sql)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
SQL_DIR = PROJECT_ROOT / "sql"
MIGRATIONS_DIR = SQL_DIR / "migrations"
TRANSFORM_DIR = SQL_DIR / "transform"
