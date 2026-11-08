"""User settings overlay: ``<data_dir>/settings.yaml`` on top of ``config/app.yaml``.

The overlay lives in the (git-ignored) data directory, so personal choices never
end up in version control. Only whitelisted top-level keys can be overridden.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

SETTINGS_FILE = "settings.yaml"
OVERRIDABLE = {"timezone", "language", "default_person", "sleep", "derived", "quality",
               "database", "rules_parameters"}


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def settings_path(data_path: Path) -> Path:
    return data_path / SETTINGS_FILE


def load_settings(data_path: Path) -> dict[str, Any]:
    p = settings_path(data_path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in OVERRIDABLE}


def save_settings(data_path: Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Merge ``changes`` into the stored overlay and write it. Returns the new overlay."""
    unknown = set(changes) - OVERRIDABLE
    if unknown:
        raise ValueError(f"not overridable: {sorted(unknown)}")
    current = load_settings(data_path)
    merged = deep_merge(current, changes)
    merged = _drop_none(merged)  # a None value removes the key (reset to default)
    data_path.mkdir(parents=True, exist_ok=True)
    tmp = settings_path(data_path).with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(merged, fh, allow_unicode=True, sort_keys=True)
    tmp.replace(settings_path(data_path))
    return merged


def rules_parameters(config_dir: Path, data_path: Path) -> dict[str, dict[str, Any]]:
    """rules.yaml parameters with the user's overrides applied (``value`` only)."""
    with (config_dir / "rules.yaml").open(encoding="utf-8") as fh:
        rules = yaml.safe_load(fh) or {}
    params: dict[str, dict[str, Any]] = copy.deepcopy(rules.get("parameters") or {})
    for key, value in (load_settings(data_path).get("rules_parameters") or {}).items():
        if key in params:
            params[key]["default"] = params[key]["value"]
            params[key]["value"] = value
    for p in params.values():
        p.setdefault("default", p["value"])
    return params


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        out[k] = _drop_none(v) if isinstance(v, dict) else v
    return out
