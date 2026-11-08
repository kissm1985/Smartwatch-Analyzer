"""Human-readable (Hungarian) description of an Explorer analysis for PDF / XLSX exports."""

from __future__ import annotations

from vitalmart.fmt import fmt_date, fmt_num
from vitalmart.i18n import t
from vitalmart.semantic.engine import Result
from vitalmart.semantic.model import SemanticModel
from vitalmart.semantic.spec import AnalysisSpec


def describe_spec(spec: AnalysisSpec, model: SemanticModel,
                  res: Result | None = None) -> list[tuple[str, str]]:
    topic = model.topics[spec.topic]
    measures = []
    for ms in spec.measures:
        m = topic.measures[ms.key]
        agg = spec.agg_of(ms, model)
        agg_label = model.aggregations.get(agg, {}).get("label_hu", agg)
        measures.append(f"{m.label_hu} [{m.unit}] – {agg_label}" if m.unit
                        else f"{m.label_hu} – {agg_label}")
    rows: list[tuple[str, str]] = [
        (t("explorer.topic"), topic.label_hu),
        (t("explorer.measures"), "; ".join(measures)),
        (t("explorer.dimensions"), ", ".join(model.dimensions[d].label_hu
                                             for d in spec.dimensions) or t("explorer.none")),
        (t("explorer.grain"), t(f"explorer.grain_opt.{spec.time_grain}")),
        (t("explorer.period"), t(f"explorer.period_opt.{spec.period}")),
    ]
    if res is not None:
        rows.append((t("export.desc.date_range"),
                     f"{fmt_date(res.date_from)} – {fmt_date(res.date_to)}"))
    rows.append((t("explorer.persons"), ", ".join(spec.persons) or t("explorer.all_persons")))
    for f in spec.filters:
        label = model.dimensions[f.dimension].label_hu if f.dimension in model.dimensions \
            else f.dimension
        vals = ", ".join(str(v) for v in f.values)
        rows.append((t("explorer.filters"),
                     f"{label} {'≠' if f.exclude else '='} {vals}"))
    for vf in spec.value_filters:
        m = topic.measures[vf.measure]
        rng = f"{fmt_num(vf.min, 2) if vf.min is not None else '–∞'} … " \
              f"{fmt_num(vf.max, 2) if vf.max is not None else '∞'}"
        rows.append((t("explorer.value_filter"), f"{m.label_hu}: {rng}"))
    rows.append((t("explorer.quality_ok"), t("export.desc.yes") if spec.quality_ok_only
                 else t("export.desc.no")))
    tr = t(f"explorer.transform_opt.{spec.transform}")
    if spec.transform == "moving_avg":
        tr += f" ({spec.transform_window})"
    rows.append((t("explorer.transform"), tr))
    rows.append((t("explorer.chart"), t(f"explorer.chart_opt.{spec.chart}")))
    if spec.chart == "corr":
        rows.append((t("explorer.corr_method"), spec.corr_method.capitalize()))
    if res is not None:
        rows.append((t("export.desc.rows"), fmt_num(res.n_rows)))
    return rows
