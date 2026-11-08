"""Static dimension content: dim_date, dim_time_of_day, dim_sleep_stage, dim_hr_zone."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pyarrow as pa

from vitalmart.config import AppConfig
from vitalmart.datamart.sqlutil import fetch_scalar

DATE_START = date(2015, 1, 1)
DATE_END = date(2040, 12, 31)

MONTHS_HU = [
    "január", "február", "március", "április", "május", "június",
    "július", "augusztus", "szeptember", "október", "november", "december",
]
DAYS_HU = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
SEASONS_HU = {"winter": "tél", "spring": "tavasz", "summer": "nyár", "autumn": "ősz"}

SLEEP_STAGES = [
    # key, hu, en, is_asleep, order, note
    ("awake", "Ébren", "Awake", False, 1, None),
    ("light", "Könnyű / Core", "Light / Core", True, 2,
     "Apple 'Core' ≈ Garmin 'Light'; forrásközi összevetés csak figyelmeztetéssel."),
    ("deep", "Mély", "Deep", True, 3, None),
    ("rem", "REM", "REM", True, 4, None),
    ("in_bed", "Ágyban", "In bed", False, 5, "Ágyban töltött idő (nem feltétlenül alvás)."),
    ("unspecified", "Nem megadott alvás", "Asleep (unspecified)", True, 6,
     "Fázis nélküli alvás (pl. régebbi Apple adatok)."),
]

# Default 5-zone model as % of max HR; user-specific bounds come with the profile (M4).
HR_ZONES = [
    (1, "1. zóna – nagyon könnyű", 50.0, 60.0),
    (2, "2. zóna – könnyű", 60.0, 70.0),
    (3, "3. zóna – mérsékelt", 70.0, 80.0),
    (4, "4. zóna – nehéz", 80.0, 90.0),
    (5, "5. zóna – maximális", 90.0, 100.0),
]


def easter_sunday(year: int) -> date:
    """Gregorian Easter (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def hungarian_holidays(year: int) -> dict[date, str]:
    """Statutory public holidays in Hungary (Munka Törvénykönyve 102. §)."""
    easter = easter_sunday(year)
    hol = {
        date(year, 1, 1): "Újév",
        date(year, 3, 15): "Nemzeti ünnep",
        easter: "Húsvétvasárnap",
        easter + timedelta(days=1): "Húsvéthétfő",
        date(year, 5, 1): "A munka ünnepe",
        easter + timedelta(days=49): "Pünkösdvasárnap",
        easter + timedelta(days=50): "Pünkösdhétfő",
        date(year, 8, 20): "Államalapítás ünnepe",
        date(year, 10, 23): "Nemzeti ünnep",
        date(year, 11, 1): "Mindenszentek",
        date(year, 12, 25): "Karácsony",
        date(year, 12, 26): "Karácsony",
    }
    if year >= 2017:
        hol[easter - timedelta(days=2)] = "Nagypéntek"
    return hol


def part_of_day(hour: int) -> tuple[str, str]:
    if hour < 6:
        return "night", "éjszaka"
    if hour < 9:
        return "morning", "reggel"
    if hour < 12:
        return "forenoon", "délelőtt"
    if hour < 18:
        return "afternoon", "délután"
    return "evening", "este"


def _date_rows() -> pa.Table:
    cols: dict[str, list] = {k: [] for k in (
        "date_key", "date", "year", "quarter", "month", "month_name_hu", "iso_year", "iso_week",
        "day_of_month", "day_of_week", "day_name_hu", "is_weekend", "is_holiday_hu",
        "holiday_name_hu", "is_workday_hu", "season", "season_hu")}
    holidays: dict[date, str] = {}
    for y in range(DATE_START.year, DATE_END.year + 1):
        holidays.update(hungarian_holidays(y))
    d = DATE_START
    while d <= DATE_END:
        iso = d.isocalendar()
        weekend = iso.weekday >= 6
        hol = holidays.get(d)
        season = SEASONS[d.month]
        for k, v in (
            ("date_key", d.year * 10000 + d.month * 100 + d.day), ("date", d),
            ("year", d.year), ("quarter", (d.month - 1) // 3 + 1), ("month", d.month),
            ("month_name_hu", MONTHS_HU[d.month - 1]), ("iso_year", iso.year),
            ("iso_week", iso.week), ("day_of_month", d.day), ("day_of_week", iso.weekday),
            ("day_name_hu", DAYS_HU[iso.weekday - 1]), ("is_weekend", weekend),
            ("is_holiday_hu", hol is not None), ("holiday_name_hu", hol),
            ("is_workday_hu", not weekend and hol is None), ("season", season),
            ("season_hu", SEASONS_HU[season]),
        ):
            cols[k].append(v)
        d += timedelta(days=1)
    return pa.table(cols)


def insert_rows(
    con: duckdb.DuckDBPyConnection, table: str, columns: list[str], rows: list[tuple]
) -> None:
    """Bulk insert via Arrow (``executemany`` is row-by-row and slow in DuckDB)."""
    if not rows:
        return
    data = pa.table({c: [r[i] for r in rows] for i, c in enumerate(columns)})
    con.register("_rows", data)
    try:
        con.execute(f"INSERT INTO {table} ({', '.join(columns)}) SELECT * FROM _rows")
    finally:
        con.unregister("_rows")


def ensure_static_dims(con: duckdb.DuckDBPyConnection, cfg: AppConfig | None = None) -> None:
    """Populate static dimensions if empty (idempotent)."""
    if fetch_scalar(con, "SELECT count(*) FROM dim_date") == 0:
        tbl = _date_rows()
        con.register("_dates", tbl)
        try:
            con.execute("INSERT INTO dim_date SELECT * FROM _dates")
        finally:
            con.unregister("_dates")
    if fetch_scalar(con, "SELECT count(*) FROM dim_time_of_day") == 0:
        rows = []
        for m in range(1440):
            h = m // 60
            pod, pod_hu = part_of_day(h)
            rows.append((m, h, m % 60, pod, pod_hu))
        insert_rows(con, "dim_time_of_day",
                    ["minute_of_day", "hour", "minute", "part_of_day", "part_of_day_hu"], rows)
    if fetch_scalar(con, "SELECT count(*) FROM dim_sleep_stage") == 0:
        insert_rows(con, "dim_sleep_stage",
                    ["stage_key", "name_hu", "name_en", "is_asleep", "sort_order", "note"],
                    SLEEP_STAGES)
    if fetch_scalar(con, "SELECT count(*) FROM dim_hr_zone WHERE zone BETWEEN 1 AND 5") == 0:
        insert_rows(con, "dim_hr_zone", ["zone", "name_hu", "lower_pct", "upper_pct"], HR_ZONES)
