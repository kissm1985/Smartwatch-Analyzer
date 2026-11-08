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


def test_i18n_files_have_no_duplicate_keys_and_same_structure():
    import yaml

    class Strict(yaml.SafeLoader):
        pass

    def no_dupes(loader, node, deep=False):
        keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
        assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"
        return loader.construct_mapping(node, deep)

    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_dupes)
    root = Path(__file__).resolve().parents[1] / "src" / "vitalmart" / "i18n"
    cats = {lang: yaml.load((root / f"{lang}.yaml").read_text(encoding="utf-8"), Strict)
            for lang in ("hu", "en")}

    def flat(d, p=""):
        return {f"{p}{k}" for k, v in d.items() for _ in [0]} | {
            x for k, v in d.items() if isinstance(v, dict) for x in flat(v, f"{p}{k}.")}

    assert flat(cats["hu"]) == flat(cats["en"])
