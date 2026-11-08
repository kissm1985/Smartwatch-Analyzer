"""Time-zone helpers.

All timestamps are stored as naive UTC (``ts_utc``) plus naive local time
(``ts_local``) and the offset in minutes (``tz_offset_min``). Local time is
derived from UTC with the *configured* time zone (default Europe/Budapest),
so DST is handled per instant, independent of the offset written in the
source file (Apple writes every date with the phone's zone at export time).

The SQL layer converts via an offset-transition table (``tz_offset``) built
here with :mod:`zoneinfo`, joined with ``ASOF JOIN`` – no ICU dependency.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


def _offset_min(tz: ZoneInfo, ts_utc: datetime) -> int:
    off = ts_utc.replace(tzinfo=UTC).astimezone(tz).utcoffset()
    assert off is not None
    return int(off.total_seconds() // 60)


def offset_transitions(
    tz_name: str, start_year: int = 1990, end_year: int = 2060
) -> list[tuple[datetime, int]]:
    """Return ``[(valid_from_utc, offset_min), ...]`` covering the given years.

    The first row starts at ``start_year``-01-01 UTC; each further row marks a
    change of UTC offset (found by daily stepping + minute-level bisection).
    """
    tz = ZoneInfo(tz_name)
    cur = datetime(start_year, 1, 1)
    end = datetime(end_year + 1, 1, 1)
    cur_off = _offset_min(tz, cur)
    rows: list[tuple[datetime, int]] = [(cur, cur_off)]
    step = timedelta(days=1)
    while cur < end:
        nxt = cur + step
        nxt_off = _offset_min(tz, nxt)
        if nxt_off != cur_off:
            lo, hi = cur, nxt  # offset(lo) == cur_off, offset(hi) == nxt_off
            while hi - lo > timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                mid = mid.replace(second=0, microsecond=0)
                if mid <= lo:
                    break
                if _offset_min(tz, mid) == cur_off:
                    lo = mid
                else:
                    hi = mid
            rows.append((hi, nxt_off))
            cur_off = nxt_off
        cur = nxt
    return rows


def to_local(ts_utc: datetime, tz_name: str) -> tuple[datetime, int]:
    """Naive UTC → (naive local, offset minutes)."""
    tz = ZoneInfo(tz_name)
    loc = ts_utc.replace(tzinfo=UTC).astimezone(tz)
    off = loc.utcoffset()
    assert off is not None
    return loc.replace(tzinfo=None), int(off.total_seconds() // 60)


def format_apple(ts_local_aware: datetime) -> str:
    """Format an aware datetime the way Apple Health export.xml does."""
    return ts_local_aware.strftime("%Y-%m-%d %H:%M:%S %z")


def date_key(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day
