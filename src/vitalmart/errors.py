"""Value-free technical error descriptions for logs, the import log and diagnostics.

Health data must never reach a log (CLAUDE.md), but "TypeError" alone is useless for
support. ``technical_reason`` returns the exception type, the innermost VitalMart code
location and a masked message: quoted strings and numbers are replaced, only Python type
names are kept (e.g. ``unsupported operand type(s) for +: 'NoneType' and 'int'``).
"""

from __future__ import annotations

import re
import traceback
from pathlib import Path

TYPE_NAMES = {"NoneType", "int", "float", "str", "bytes", "bool", "list", "dict", "tuple",
              "set", "date", "time", "datetime", "timedelta", "Decimal", "Timestamp",
              "NaTType", "ndarray", "Series", "DataFrame"}


def mask(text: str) -> str:
    """Remove anything value-like from an error message (keeps quoted type names)."""
    def quoted(m: re.Match[str]) -> str:
        inner = m.group(0)[1:-1]
        return m.group(0) if inner in TYPE_NAMES else m.group(0)[0] + "…" + m.group(0)[0]
    text = re.sub(r"'[^']*'", quoted, text)
    text = re.sub(r'"[^"]*"', quoted, text)
    text = re.sub(r"(?<![A-Za-z_])\d+([.,:\-/]\d+)*", "#", text)
    return text[:300]


def location(exc: BaseException, depth: int = 3) -> str:
    frames = [f"{Path(f.filename).name}:{f.lineno}"
              for f in traceback.extract_tb(exc.__traceback__)
              if "vitalmart" in f.filename.replace("\\", "/")]
    return " ← ".join(reversed(frames[-depth:]))


def technical_reason(exc: BaseException) -> str:
    """E.g. ``TypeError @ fit.py:132: Object of type time is not JSON serializable``."""
    msg = str(exc).splitlines()[0] if str(exc) else ""
    loc = location(exc)
    return f"{type(exc).__name__}" + (f" @ {loc}" if loc else "") + (f": {mask(msg)}" if msg
                                                                      else "")
