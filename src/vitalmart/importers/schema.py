"""Structure dump of an export WITHOUT values (field paths, types, counts).

Safe to share for parser maintenance (SPEC 14.1): no data values are printed,
only JSON paths, value types and occurrence counts – plus FIT message/field names.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from vitalmart.importers import fit
from vitalmart.importers.garmin import GarminAdapter


def _walk(x: Any, path: str, stats: dict[str, list[Any]], limit: int = 500) -> None:
    if isinstance(x, dict):
        for k, v in x.items():
            _walk(v, f"{path}.{k}", stats, limit)
    elif isinstance(x, list):
        stats[path + "[]"][0] += 1
        for v in x[:limit]:
            _walk(v, path + "[]", stats, limit)
    else:
        s = stats[path]
        s[0] += 1
        s[1].add(type(x).__name__)


def describe(path: Path) -> Iterator[str]:
    ad = GarminAdapter()
    zf = ad._open(path)
    for e in ad._expand(ad._entries(path, zf)):
        name = PurePosixPath(e.rel).name
        kind = ad.classify_name(e.rel)
        if kind == "ignored":
            yield f"# {e.rel}  [kihagyva]"
            continue
        if name.lower().endswith(".json"):
            stats: dict[str, list[Any]] = defaultdict(lambda: [0, set()])
            try:
                _walk(json.loads(e.read().decode("utf-8")), "$", stats)
            except (UnicodeDecodeError, json.JSONDecodeError):
                yield f"# {e.rel}  [nem olvasható JSON]"
                continue
            yield f"# {e.rel}  [{kind}]"
            for k, (n, types) in stats.items():
                yield f"  {k}  {'/'.join(sorted(types)) or 'list'}  n={n}"
        elif kind == "fit":
            try:
                ff = fit.decode(e.read())
            except Exception:
                yield f"# {e.rel}  [nem olvasható FIT]"
                continue
            msgs: dict[str, set[str]] = defaultdict(set)
            counts: dict[str, int] = defaultdict(int)
            for m in ff.messages:
                msgs[m.name] |= set(m.fields)
                counts[m.name] += 1
            yield f"# {e.rel}  [fit:{ff.file_type}]"
            for name_, fields in sorted(msgs.items()):
                yield f"  {name_} (n={counts[name_]}): {', '.join(sorted(fields))}"
        else:
            yield f"# {e.rel}  [{kind}]"
