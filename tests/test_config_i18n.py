from pathlib import Path

import pytest
from pydantic import ValidationError

from vitalmart.config import load_config
from vitalmart.i18n import t


def test_defaults(cfg):
    assert cfg.timezone == "Europe/Budapest"
    assert cfg.sleep.day_convention == "wake"
    assert cfg.sleep.goal_hours == 7.5
    assert cfg.db_path.name == "vitalmart.duckdb"


def test_invalid_timezone_rejected():
    with pytest.raises(ValidationError):
        load_config(timezone="Mars/Olympus")


def test_env_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("VITALMART_DATA_DIR", str(tmp_path / "x"))
    assert load_config().data_path == tmp_path / "x"


def test_relative_data_dir_is_project_relative():
    cfg = load_config(data_dir="data")
    assert cfg.data_path.is_absolute()


def test_i18n_hu_default_and_en():
    assert "112" in t("app.disclaimer")
    assert "orvosi diagnózis" in t("app.disclaimer")
    assert "emergency" in t("app.disclaimer", lang="en")
    assert t("import.duplicate_file", batch=3).count("3") >= 1
    assert t("no.such.key") == "no.such.key"
