"""Analysis spec → parameterised SQL (identifiers only from the semantic model)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from vitalmart.semantic.model import AGG_SQL, SemanticModel, Topic
from vitalmart.semantic.spec import AnalysisSpec

AGG_LIMIT = 200_000
RAW_LIMIT = 500_000


@dataclass
class Query:
    sql: str
    params: list[Any]
    period: bool                         # result has a "period" column
    dims: list[str] = field(default_factory=list)       # d0.. → dimension keys
    measures: list[str] = field(default_factory=list)   # m0.. → measure keys
    limit: int = AGG_LIMIT


def _sub(expr: str, topic: Topic) -> str:
    return (expr.replace("{person}", topic.person_expr)
            .replace("{date_key}", topic.date_key)
            .replace("{date}", topic.date_expr)
            .replace("{minute}", topic.minute_expr))


def _period_expr(spec: AnalysisSpec, topic: Topic) -> str | None:
    g = spec.time_grain
    if g == "none":
        return None
    if g == "raw":
        return str(topic.ts_expr)
    if g == "hour":
        return f"date_trunc('hour', {topic.ts_expr})"
    if g == "day":
        return topic.date_expr
    return f"CAST(date_trunc('{g}', {topic.date_expr}) AS DATE)"


def _joins_for(exprs: list[str], topic: Topic, model: SemanticModel, extra: set[str]) -> list[str]:
    alias = {"date": "dd.", "source": "ds.", "time_of_day": "td.", "activity_type": "aty.",
             "metric": "dm.", "event": "ev."}
    needed = set(extra) | {name for e in exprs for name, a in alias.items() if a in e}
    while True:      # a join's ON clause may need another join (event → person via dim_source)
        more = {name for j in needed if j in model.joins
                for name, a in alias.items() if a in _sub(model.joins[j], topic)} - needed
        if not more:
            break
        needed |= more
    return [j for j in topic.joins if j in needed]


def _filters(
    spec: AnalysisSpec, topic: Topic, model: SemanticModel, date_from: date | None,
    date_to: date | None,
) -> tuple[list[str], list[Any], set[str]]:
    where: list[str] = []
    params: list[Any] = []
    joins: set[str] = set()
    if topic.base_where:
        where.append(topic.base_where)
    if topic.quality and spec.quality_ok_only:
        where.append("f.quality_flag = 'ok'")
    if date_from is not None:
        where.append(f"{topic.date_expr} >= ?")
        params.append(date_from)
    if date_to is not None:
        where.append(f"{topic.date_expr} <= ?")
        params.append(date_to)
    if spec.persons:
        where.append(f"{topic.person_expr} IN ({', '.join('?' * len(spec.persons))})")
        params += spec.persons
    for f in spec.filters:
        if not f.values:
            continue
        d = model.dimensions[f.dimension]
        if d.join:
            joins.add(d.join)
        expr = _sub(d.expr, topic)
        where.append(f"{expr} {'NOT IN' if f.exclude else 'IN'} "
                     f"({', '.join('?' * len(f.values))})")
        params += f.values
    if topic.long:
        conds = [topic.measures[m.key].where for m in spec.measures
                 if topic.measures[m.key].where]
        if conds:
            where.append("(" + " OR ".join(f"({c})" for c in conds) + ")")
    for vf in spec.value_filters:
        m = topic.measures[vf.measure]
        cond = []
        if vf.min is not None:
            cond.append(f"{m.expr} >= ?")
            params.append(vf.min)
        if vf.max is not None:
            cond.append(f"{m.expr} <= ?")
            params.append(vf.max)
        if cond:
            inner = " AND ".join(cond)
            where.append(f"(NOT ({m.where}) OR ({inner}))" if m.where else f"({inner})")
    return where, params, joins


def build_query(
    spec: AnalysisSpec, model: SemanticModel, date_from: date | None, date_to: date | None
) -> Query:
    topic = model.topics[spec.topic]
    raw = all(spec.agg_of(m, model) == "none" for m in spec.measures)
    select: list[str] = []
    order: list[str] = []
    period = _period_expr(spec, topic)
    if period:
        select.append(f"{period} AS period")
        order.append("period")
    dim_exprs: list[str] = []
    for i, dk in enumerate(spec.dimensions):
        d = model.dimensions[dk]
        expr = _sub(d.expr, topic)
        dim_exprs.append(expr)
        select.append(f"{expr} AS d{i}")
        if d.order:
            ord_expr = _sub(d.order, topic)
            dim_exprs.append(ord_expr)
            select.append(f"{'min(' + ord_expr + ')' if not raw else ord_expr} AS o{i}")
            order.append(f"o{i}")
        else:
            order.append(f"d{i}")
    m_exprs: list[str] = []
    for i, ms in enumerate(spec.measures):
        m = topic.measures[ms.key]
        x = f"CASE WHEN {m.where} THEN {m.expr} END" if (m.where and topic.long) else m.expr
        m_exprs.append(x)
        agg = spec.agg_of(ms, model)
        select.append(f"{x if agg == 'none' else AGG_SQL[agg].format(x=x)} AS m{i}")
    where, params, fjoins = _filters(spec, topic, model, date_from, date_to)
    used = [*dim_exprs, *m_exprs, topic.person_expr if spec.persons else "", *where]
    joins = _joins_for(used, topic, model, fjoins)
    join_sql = "".join(f"\nLEFT JOIN {_sub(model.joins[j], topic)}" for j in joins)
    sql = f"SELECT {', '.join(select)}\nFROM {topic.table} f{join_sql}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    if not raw:
        sql += "\nGROUP BY ALL"
    if order:
        sql += "\nORDER BY " + ", ".join(order)
    limit = RAW_LIMIT if raw else AGG_LIMIT
    sql += f"\nLIMIT {limit + 1}"
    return Query(sql, params, period is not None, list(spec.dimensions),
                 [m.key for m in spec.measures], limit)


def build_timeline_query(
    spec: AnalysisSpec, model: SemanticModel, date_from: date | None, date_to: date | None
) -> Query:
    topic = model.topics[spec.topic]
    tl = topic.timeline
    assert tl is not None
    where, params, fjoins = _filters(spec, topic, model, date_from, date_to)
    if tl.join:
        fjoins.add(tl.join)
    used = [tl.label, *where, topic.person_expr]
    joins = _joins_for(used, topic, model, fjoins)
    join_sql = "".join(f"\nLEFT JOIN {_sub(model.joins[j], topic)}" for j in joins)
    sql = (f"SELECT {tl.start} AS start, {tl.end} AS finish, {tl.label} AS label, "
           f"{topic.person_expr} AS person\nFROM {topic.table} f{join_sql}")
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    sql += f"\nORDER BY start\nLIMIT {AGG_LIMIT + 1}"
    return Query(sql, params, False, [], [], AGG_LIMIT)


def anchor_sql(spec: AnalysisSpec, model: SemanticModel) -> tuple[str, list[Any]]:
    """Last date with data in the topic (relative periods end there, not 'today')."""
    topic = model.topics[spec.topic]
    where, params = [], []
    if spec.persons:
        where.append(f"{topic.person_expr} IN ({', '.join('?' * len(spec.persons))})")
        params += spec.persons
    join = ""
    if "ds." in topic.person_expr and spec.persons:
        join = f" LEFT JOIN {_sub(model.joins['source'], topic)}"
    sql = f"SELECT max({topic.date_expr}) FROM {topic.table} f{join}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, params


def distinct_values_query(topic_key: str, dim_key: str, model: SemanticModel,
                          limit: int = 300) -> str:
    """Values offered in a dimension filter (identifiers from the model only)."""
    topic = model.topics[topic_key]
    d = model.dimensions[dim_key]
    expr = _sub(d.expr, topic)
    joins = _joins_for([expr], topic, model, {d.join} if d.join else set())
    join_sql = "".join(f"\nLEFT JOIN {_sub(model.joins[j], topic)}" for j in joins)
    return (f"SELECT DISTINCT {expr} AS v FROM {topic.table} f{join_sql} "
            f"WHERE {expr} IS NOT NULL ORDER BY 1 LIMIT {int(limit)}")
