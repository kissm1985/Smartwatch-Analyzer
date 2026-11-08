"""User events / annotations (SPEC 8.2/11): travel, illness, diet change, … stored locally in
``dim_event``. ``person_key`` NULL means the whole household. Free text – never logged."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb
import pandas as pd

from vitalmart.i18n import t


class EventError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(t(key))
        self.key = key


@dataclass
class Event:
    event_id: int
    start_date: date
    end_date: date
    label: str
    category: str
    note: str
    person_key: str | None

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


def _validate(start: date, end: date | None, label: str, person: str | None,
              con: duckdb.DuckDBPyConnection) -> date:
    if not label.strip():
        raise EventError("events.err.label")
    end = end or start
    if end < start:
        raise EventError("events.err.period")
    if person and not con.execute("SELECT 1 FROM dim_person WHERE person_key = ?",
                                  [person]).fetchall():
        raise EventError("events.err.person")
    return end


def add_event(con: duckdb.DuckDBPyConnection, start: date, end: date | None, label: str, *,
              category: str = "", note: str = "", person: str | None = None) -> int:
    end = _validate(start, end, label, person, con)
    row = con.execute("""INSERT INTO dim_event (start_date, end_date, category, label, note,
                                                person_key, created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING event_id""",
                      [start, end, category.strip(), label.strip(), note.strip(),
                       person or None, datetime.now(), datetime.now()]).fetchall()
    return int(row[0][0])


def update_event(con: duckdb.DuckDBPyConnection, event_id: int, start: date, end: date | None,
                 label: str, *, category: str = "", note: str = "",
                 person: str | None = None) -> None:
    end = _validate(start, end, label, person, con)
    get_event(con, event_id)
    con.execute("""UPDATE dim_event SET start_date = ?, end_date = ?, category = ?, label = ?,
                                        note = ?, person_key = ?, updated_at = ?
                   WHERE event_id = ?""",
                [start, end, category.strip(), label.strip(), note.strip(), person or None,
                 datetime.now(), event_id])


def delete_event(con: duckdb.DuckDBPyConnection, event_id: int) -> None:
    get_event(con, event_id)
    con.execute("DELETE FROM dim_event WHERE event_id = ?", [event_id])


def _row(r: tuple[Any, ...]) -> Event:
    return Event(int(r[0]), r[1], r[2] or r[1], r[3], r[4] or "", r[5] or "", r[6])


_COLS = "event_id, start_date, end_date, label, category, note, person_key"


def get_event(con: duckdb.DuckDBPyConnection, event_id: int) -> Event:
    rows = con.execute(f"SELECT {_COLS} FROM dim_event WHERE event_id = ?",
                       [event_id]).fetchall()
    if not rows:
        raise EventError("events.err.not_found")
    return _row(rows[0])


def list_events(con: duckdb.DuckDBPyConnection, persons: list[str] | None = None,
                date_from: date | None = None, date_to: date | None = None) -> list[Event]:
    """Events overlapping the period; for ``persons`` the household-wide ones are included."""
    where: list[str] = []
    params: list[object] = []
    if persons:
        where.append(f"(person_key IS NULL OR person_key IN ({', '.join('?' * len(persons))}))")
        params += persons
    if date_from:
        where.append("coalesce(end_date, start_date) >= ?")
        params.append(date_from)
    if date_to:
        where.append("start_date <= ?")
        params.append(date_to)
    sql = f"SELECT {_COLS} FROM dim_event"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY start_date, event_id"
    return [_row(r) for r in con.execute(sql, params).fetchall()]


def events_frame(events: list[Event]) -> pd.DataFrame:
    return pd.DataFrame([{"event_id": e.event_id, "start_date": e.start_date,
                          "end_date": e.end_date, "days": e.days, "label": e.label,
                          "category": e.category, "person_key": e.person_key or "",
                          "note": e.note} for e in events],
                        columns=["event_id", "start_date", "end_date", "days", "label",
                                 "category", "person_key", "note"])


def categories(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Suggested categories (i18n) + those already used."""
    base = [x.strip() for x in t("events.category_suggestions").split(",")]
    used = [r[0] for r in con.execute("""SELECT DISTINCT category FROM dim_event
                                         WHERE coalesce(category, '') <> ''
                                         ORDER BY 1""").fetchall()]
    return list(dict.fromkeys([*base, *used]))
