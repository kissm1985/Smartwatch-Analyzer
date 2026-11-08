"""Hungarian display formatting (SPEC 10.5): decimal comma, ÉÉÉÉ.HH.NN., 24 h."""

from __future__ import annotations

from datetime import date

import pandas as pd


def _missing(v: object) -> bool:
    try:
        return v is None or bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def fmt_num(x: float | int | None, digits: int = 0) -> str:
    """Decimal comma, narrow no-break space as thousands separator."""
    if _missing(x):
        return "–"
    s = f"{x:,.{digits}f}"
    return s.replace(",", " ").replace(".", ",")


def fmt_signed(x: float | None, digits: int = 0) -> str:
    if _missing(x):
        return "–"
    assert x is not None
    return ("+" if x > 0 else "") + fmt_num(x, digits)


def fmt_date(d: date | pd.Timestamp | None) -> str:
    if _missing(d):
        return "–"
    return pd.Timestamp(d).strftime("%Y.%m.%d.")


def fmt_datetime(v: object) -> str:
    if _missing(v):
        return "–"
    return pd.Timestamp(v).strftime("%Y.%m.%d. %H:%M")


def fmt_hm(minutes: float | None) -> str:
    if _missing(minutes):
        return "–"
    assert minutes is not None
    m = round(minutes)
    return f"{m // 60} ó {m % 60:02d} p"


def fmt_clock(hours: float | None) -> str:
    """Hours (may exceed 24) → HH:MM on a 24 h clock."""
    if _missing(hours):
        return "–"
    assert hours is not None
    m = round(hours * 60) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"
