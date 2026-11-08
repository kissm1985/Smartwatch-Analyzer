"""Analysis definition (saved as JSON) and its validation against the semantic model."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from vitalmart.semantic.model import SemanticModel

Grain = Literal["none", "raw", "hour", "day", "week", "month", "quarter", "year"]
Period = Literal["last_7", "last_30", "last_90", "last_180", "last_365", "all", "custom"]
Transform = Literal["none", "moving_avg", "cumsum", "diff", "pct_change", "index100", "zscore",
                    "minmax"]
Chart = Literal["line", "bar", "bar_stacked", "area", "scatter", "bubble", "histogram", "box",
                "violin", "heatmap", "calendar", "corr", "radar", "timeline", "table"]

PERIOD_DAYS = {"last_7": 7, "last_30": 30, "last_90": 90, "last_180": 180, "last_365": 365}
MAX_MEASURES = 4
MAX_DIMENSIONS = 3


class SpecError(ValueError):
    """Invalid analysis definition; ``key`` is an i18n key for the UI."""

    def __init__(self, key: str, **kwargs: object) -> None:
        super().__init__(f"{key} {kwargs}")
        self.key = key
        self.kwargs = kwargs


class MeasureSel(BaseModel):
    key: str
    agg: str | None = None


class DimFilter(BaseModel):
    dimension: str
    values: list[str | int | float | bool]
    exclude: bool = False


class ValueFilter(BaseModel):
    measure: str
    min: float | None = None
    max: float | None = None


class AnalysisSpec(BaseModel):
    version: int = 1
    topic: str
    measures: list[MeasureSel] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    time_grain: Grain = "day"
    period: Period = "last_90"
    date_from: date | None = None
    date_to: date | None = None
    persons: list[str] = Field(default_factory=list)
    filters: list[DimFilter] = Field(default_factory=list)
    value_filters: list[ValueFilter] = Field(default_factory=list)
    quality_ok_only: bool = True
    transform: Transform = "none"
    transform_window: int = Field(7, ge=2, le=365)
    chart: Chart = "line"
    secondary_measure: str | None = None
    facet: str | None = None
    corr_method: Literal["spearman", "pearson"] = "spearman"
    show_events: bool = True            # event bands on time-axis charts (SPEC 8.2/11)

    def agg_of(self, m: MeasureSel, model: SemanticModel) -> str:
        return m.agg or model.topics[self.topic].measures[m.key].agg


def validate(spec: AnalysisSpec, model: SemanticModel) -> None:
    if spec.topic not in model.topics:
        raise SpecError("explorer.err.topic", topic=spec.topic)
    topic = model.topics[spec.topic]
    dims_allowed = model.topic_dimensions(spec.topic)
    if not 1 <= len(spec.measures) <= MAX_MEASURES:
        raise SpecError("explorer.err.measure_count", max=MAX_MEASURES)
    if len(spec.dimensions) > MAX_DIMENSIONS:
        raise SpecError("explorer.err.dim_count", max=MAX_DIMENSIONS)
    keys = [m.key for m in spec.measures]
    if len(set(keys)) != len(keys):
        raise SpecError("explorer.err.duplicate_measure")
    for m in spec.measures:
        if m.key not in topic.measures:
            raise SpecError("explorer.err.measure", measure=m.key)
        agg = spec.agg_of(m, model)
        if agg not in model.aggregations:
            raise SpecError("explorer.err.agg", agg=agg)
    aggs = {spec.agg_of(m, model) for m in spec.measures}
    raw = "none" in aggs
    if raw and aggs != {"none"}:
        raise SpecError("explorer.err.mixed_raw")
    if raw and topic.long and len(spec.measures) > 1:
        raise SpecError("explorer.err.raw_long")
    if spec.time_grain in ("raw", "hour") and not topic.ts_expr:
        raise SpecError("explorer.err.grain_intraday")
    if spec.time_grain == "raw" and not raw:
        raise SpecError("explorer.err.raw_needs_none")
    for d in spec.dimensions:
        if d not in dims_allowed:
            raise SpecError("explorer.err.dimension", dimension=d)
    if len(set(spec.dimensions)) != len(spec.dimensions):
        raise SpecError("explorer.err.duplicate_dimension")
    for f in spec.filters:
        if f.dimension not in dims_allowed:
            raise SpecError("explorer.err.dimension", dimension=f.dimension)
    for vf in spec.value_filters:
        if vf.measure not in keys:
            raise SpecError("explorer.err.value_filter", measure=vf.measure)
    for req in topic.required_dimensions:
        single = any(f.dimension == req and not f.exclude and len(f.values) == 1
                     for f in spec.filters)
        if req not in spec.dimensions and not single:
            raise SpecError("explorer.err.required_dimension",
                            dimension=model.dimensions[req].label_hu)
    for req in topic.required_filters:
        has = any(f.dimension == req and f.values and not f.exclude for f in spec.filters)
        if not has:
            raise SpecError("explorer.err.required_filter",
                            dimension=model.dimensions[req].label_hu)
    if spec.period == "custom" and (spec.date_from is None or spec.date_to is None):
        raise SpecError("explorer.err.custom_period")
    if spec.secondary_measure and spec.secondary_measure not in keys:
        raise SpecError("explorer.err.secondary")
    if spec.facet and spec.facet not in spec.dimensions:
        raise SpecError("explorer.err.facet")
    chart = spec.chart
    if chart in ("scatter", "corr") and len(spec.measures) < 2:
        raise SpecError("explorer.err.need_two", chart=chart)
    if chart == "bubble" and len(spec.measures) < 3:
        raise SpecError("explorer.err.need_three")
    if chart == "heatmap" and len(spec.dimensions) + (spec.time_grain not in ("none",)) < 2:
        raise SpecError("explorer.err.heatmap")
    if chart == "calendar" and spec.time_grain != "day":
        raise SpecError("explorer.err.calendar")
    if chart == "radar" and (not spec.dimensions or len(spec.measures) < 3):
        raise SpecError("explorer.err.radar")
    if chart == "timeline" and topic.timeline is None:
        raise SpecError("explorer.err.timeline")
