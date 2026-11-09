"""Semantic model: topics, measures, dimensions, joins (validated from YAML)."""

from __future__ import annotations

import fnmatch
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from vitalmart.paths import CONFIG_DIR

AGG_SQL = {
    "sum": "sum({x})", "avg": "avg({x})", "median": "median({x})", "min": "min({x})",
    "max": "max({x})", "std": "stddev_samp({x})", "count": "count({x})",
    "p10": "quantile_cont({x}, 0.10)", "p25": "quantile_cont({x}, 0.25)",
    "p75": "quantile_cont({x}, 0.75)", "p90": "quantile_cont({x}, 0.90)",
}


class Measure(BaseModel):
    key: str = ""
    label_hu: str
    unit: str = ""
    expr: str
    where: str | None = None
    agg: str = "avg"


class Dimension(BaseModel):
    key: str = ""
    label_hu: str
    expr: str
    order: str | None = None
    join: str | None = None


class Timeline(BaseModel):
    start: str
    end: str
    label: str
    join: str | None = None


class Topic(BaseModel):
    key: str = ""
    label_hu: str
    table: str
    long: bool = False
    quality: bool = False
    intraday: bool = False
    base_where: str | None = None
    date_expr: str
    date_key: str
    ts_expr: str | None = None
    person_expr: str
    minute_expr: str = "f.minute_of_day"
    # fact topics: source column; excluded sources (map_excluded_source) are filtered out
    source_col: str | None = None
    joins: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)      # may contain "date.*" patterns
    required_dimensions: list[str] = Field(default_factory=list)
    required_filters: list[str] = Field(default_factory=list)
    timeline: Timeline | None = None
    measures: dict[str, Measure]


class SemanticModel(BaseModel):
    joins: dict[str, str]
    dimensions: dict[str, Dimension]
    hierarchies: dict[str, list[str]] = Field(default_factory=dict)
    aggregations: dict[str, dict[str, str]]
    topics: dict[str, Topic]

    @model_validator(mode="after")
    def _link(self) -> SemanticModel:
        for k, d in self.dimensions.items():
            d.key = k
            if d.join and d.join not in self.joins:
                raise ValueError(f"dimension {k}: unknown join {d.join}")
        unknown_aggs = set(self.aggregations) - set(AGG_SQL) - {"none"}
        if unknown_aggs:
            raise ValueError(f"unknown aggregations {unknown_aggs}")
        for tk, t in self.topics.items():
            t.key = tk
            for j in t.joins:
                if j not in self.joins:
                    raise ValueError(f"topic {tk}: unknown join {j}")
            for mk, m in t.measures.items():
                m.key = mk
                if m.agg not in AGG_SQL:
                    raise ValueError(f"{tk}.{mk}: bad default agg {m.agg}")
            for dk in self.topic_dimensions(tk):
                d = self.dimensions[dk]
                if d.join and d.join not in t.joins:
                    raise ValueError(f"topic {tk}: dimension {dk} needs join {d.join}")
            for dk in t.required_dimensions + t.required_filters:
                if dk not in self.topic_dimensions(tk):
                    raise ValueError(f"topic {tk}: required {dk} is not one of its dimensions")
        return self

    def topic_dimensions(self, topic: str) -> list[str]:
        out: list[str] = []
        for pat in self.topics[topic].dimensions:
            for k in self.dimensions:
                if fnmatch.fnmatchcase(k, pat) and k not in out:
                    out.append(k)
        return out


@lru_cache(maxsize=4)
def load_model(path: Path = CONFIG_DIR / "semantic.yaml") -> SemanticModel:
    with path.open(encoding="utf-8") as fh:
        return SemanticModel.model_validate(yaml.safe_load(fh))
