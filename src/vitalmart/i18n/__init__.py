"""Minimal i18n: user-facing strings live in hu.yaml / en.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).resolve().parent
DEFAULT_LANG = "hu"
_current = DEFAULT_LANG


@lru_cache(maxsize=4)
def _catalog(lang: str) -> dict[str, str]:
    path = _DIR / f"{lang}.yaml"
    with path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    flat: dict[str, str] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)
        else:
            flat[prefix] = str(node)

    walk("", raw)
    return flat


def set_language(lang: str) -> None:
    global _current
    _catalog(lang)  # validate it exists
    _current = lang


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate ``key``; falls back to Hungarian, then to the key itself."""
    lang = lang or _current
    text = _catalog(lang).get(key) or _catalog(DEFAULT_LANG).get(key) or key
    return text.format(**kwargs) if kwargs else text


def has(key: str, lang: str | None = None) -> bool:
    return key in _catalog(lang or _current) or key in _catalog(DEFAULT_LANG)
