"""Human-readable (Hungarian) data-quality summary from the dq_* tables (SPEC 4.5).

Only counts, dates and percentages are printed – technical metadata, no health values.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb

from vitalmart.i18n import t


def _fmt_date(d: object) -> str:
    return d.strftime("%Y.%m.%d.") if hasattr(d, "strftime") else "–"


def _num(x: float | int | None, digits: int = 1) -> str:
    if x is None:
        return "–"
    return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


def quality_report(con: duckdb.DuckDBPyConnection, person: str | None = None) -> Iterator[str]:
    persons = [r[0] for r in con.execute(
        "SELECT DISTINCT coalesce(person_key, 'unassigned') AS p FROM dim_source "
        "WHERE ? IS NULL OR person_key = ? ORDER BY 1", [person, person]).fetchall()]
    if not persons:
        yield t("quality.no_data")
        return
    for p in persons:
        yield t("quality.person", person=p)
        yield "  " + t("quality.sources")
        for src, n_metrics, d0, d1, cov in con.execute("""
                SELECT source_key, count(*), min(first_date), max(last_date),
                       avg(coverage_pct) FILTER (WHERE coverage_pct >= 50)
                FROM dq_coverage WHERE person_key = ? GROUP BY 1 ORDER BY 3""", [p]).fetchall():
            yield "    " + t("quality.source_row", source=src, metrics=n_metrics,
                             date_from=_fmt_date(d0), date_to=_fmt_date(d1),
                             coverage=_num(cov))
        gaps = con.execute("""SELECT count(*), coalesce(max(gap_days), 0) FROM dq_gap
                              WHERE person_key = ?""", [p]).fetchall()[0]
        yield "  " + t("quality.gaps", n=gaps[0], longest=gaps[1])
        for src, metric, g0, g1, days in con.execute("""
                SELECT source_key, metric_key, gap_start, gap_end, gap_days FROM dq_gap
                WHERE person_key = ? ORDER BY gap_days DESC, gap_start LIMIT 5""",
                [p]).fetchall():
            yield "    " + t("quality.gap_row", source=src, metric=metric,
                             date_from=_fmt_date(g0), date_to=_fmt_date(g1), days=days)
        yield "  " + t("quality.flags")
        rows = con.execute("""
            SELECT fact_table, quality_flag, sum(n), sum(total) FROM dq_flag_summary
            WHERE person_key = ? GROUP BY 1, 2 ORDER BY 3 DESC""", [p]).fetchall()
        if not rows:
            yield "    " + t("quality.none")
        for table, flag, n, total in rows:
            yield "    " + t("quality.flag_row", table=table, flag=flag, n=n, total=total)
        yield "  " + t("quality.nonwear")
        rows = con.execute("""
            SELECT kind, count(*), sum(minutes) / 60.0 FROM dq_nonwear WHERE person_key = ?
            GROUP BY 1 ORDER BY 1""", [p]).fetchall()
        if not rows:
            yield "    " + t("quality.none")
        for kind, n, hours in rows:
            yield "    " + t(f"quality.kind_{kind}", n=n, hours=_num(hours))
        yield "  " + t("quality.switches")
        rows = con.execute("""
            SELECT metric_key, date, from_source, to_source FROM dq_source_switch
            WHERE person_key = ? ORDER BY date, metric_key LIMIT 20""", [p]).fetchall()
        if not rows:
            yield "    " + t("quality.none")
        for metric, d, a, b in rows:
            yield "    " + t("quality.switch_row", metric=metric, date=_fmt_date(d),
                             from_source=a, to_source=b)
