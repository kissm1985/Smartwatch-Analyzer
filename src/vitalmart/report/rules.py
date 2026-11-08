"""Rule catalogue (config/rules.yaml) and its evaluation (SPEC 7.3).

Thresholds and texts live ONLY in rules.yaml; this module implements the generic
rule *types*. Every finding carries the measured value, the comparison basis, a
confidence level and the chart it refers to (SPEC 7.1).
"""

from __future__ import annotations

import copy
import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from vitalmart.fmt import fmt_date, fmt_num, fmt_signed
from vitalmart.report.data import ReportData
from vitalmart.settings import load_settings

SEVERITY_ORDER = {"consult": 0, "attention": 1, "info": 2}


@dataclass
class Catalog:
    meta: dict[str, Any]
    params: dict[str, float]
    param_labels: dict[str, dict[str, Any]]
    rules: list[dict[str, Any]]
    references: dict[str, str]
    correlations: list[dict[str, Any]]

    @property
    def version(self) -> int:
        return int(self.meta.get("version", 0))


@dataclass
class Finding:
    rule_id: str
    section: str
    severity: str                 # info | attention | consult
    positive: bool
    text: str
    focus: str | None
    value: float | None
    basis: str                    # comparison basis (threshold / baseline / previous period)
    confidence: str               # magas | közepes | alacsony
    n: int
    chart_id: str | None
    source: str | None
    sort_key: tuple[int, int, str] = field(default=(9, 9, ""))


def load_catalog(config_dir: Path, data_path: Path | None = None) -> Catalog:
    with (config_dir / "rules.yaml").open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    labels = copy.deepcopy(raw.get("parameters") or {})
    params = {k: float(v["value"]) for k, v in labels.items()}
    if data_path is not None:
        for k, v in (load_settings(data_path).get("rules_parameters") or {}).items():
            if k in params:
                params[k] = float(v)
    meta = {k: raw.get(k) for k in ("reviewed_by", "reviewed_at", "review_status", "version")}
    return Catalog(meta, params, labels, list(raw.get("rules") or []),
                   dict(raw.get("references") or {}), list(raw.get("correlations") or []))


def confidence(n: int, period_days: int, *, min_days: int = 14) -> str:
    """Data amount → confidence (documented in the methodology section):
    magas: >= 80 % coverage and >= 28 values; közepes: >= 50 % and >= min_days; else alacsony."""
    cov = n / max(period_days, 1)
    if cov >= 0.8 and n >= 28:
        return "magas"
    if cov >= 0.5 and n >= min_days:
        return "közepes"
    return "alacsony"


def longest_run(mask: pd.Series) -> tuple[int, pd.Timestamp | None]:
    """Longest run of consecutive calendar days where mask is True → (length, last day)."""
    days = mask[mask].index.sort_values()
    best, best_end, run, prev = 0, None, 0, None
    for d in days:
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        if run >= best:
            best, best_end = run, d
        prev = d
    return best, best_end


class _Ctx:
    def __init__(self, data: ReportData, cat: Catalog) -> None:
        self.d = data
        self.cat = cat
        self.min_days = int(cat.params.get("min_valid_days", 14))

    def p(self, spec: dict[str, Any] | None) -> float | None:
        if not spec:
            return None
        v = self.cat.params[spec["param"]]
        return v * float(spec.get("scale", 1))


def _fmt(v: float | None) -> str:
    if v is None:
        return "–"
    return fmt_num(v, 0 if abs(v) >= 100 or float(v).is_integer() else 1)


def _finding(rule: dict[str, Any], ctx: _Ctx, *, value: float | None, n: int, basis: str,
             chart: str | None, fields: dict[str, Any]) -> Finding:
    txt = rule["text"].format(**fields)
    focus = rule.get("focus", "").format(**fields) or None
    sev = rule.get("severity", "info")
    pos = bool(rule.get("positive", False))
    conf = confidence(n, ctx.d.period.days, min_days=ctx.min_days)
    src = ctx.cat.references.get(rule.get("source", ""), rule.get("source"))
    return Finding(rule["id"], rule["section"], sev, pos, txt, focus, value, basis, conf, n,
                   chart, src, (SEVERITY_ORDER.get(sev, 9), 0 if not pos else 1, rule["id"]))


CHART_FOR = {"sleep": "sleep_duration", "heart": "rhr_trend", "activity": "who_weekly",
             "stress": "stress_daily", "respiration": "spo2", "body": "body"}


def _eval_rule(rule: dict[str, Any], ctx: _Ctx) -> list[Finding]:
    d = ctx.d
    typ = rule["type"]
    thr = ctx.p(rule.get("threshold"))
    chart = CHART_FOR.get(rule["section"])
    metric = rule.get("metric", "")
    out: list[Finding] = []

    if typ in ("share_below", "share_at_least"):
        s = d.series(metric)
        if len(s) < ctx.min_days or thr is None:
            return []
        hit = (s < thr) if typ == "share_below" else (s >= thr)
        share = 100.0 * float(hit.mean())
        limit = ctx.p(rule.get("share")) or 0
        if (typ == "share_below" and share > limit) or (typ == "share_at_least" and share >= limit):
            out.append(_finding(rule, ctx, value=share, n=len(s),
                                basis=f"küszöb: {_fmt(thr / 60)} óra", chart=chart,
                                fields={"share": fmt_num(share, 0),
                                        "threshold_h": fmt_num(thr / 60, 1 if thr % 60 else 0)}))
        return out

    if typ in ("mean_above", "mean_below"):
        s = d.series(metric)
        if len(s) < ctx.min_days or thr is None:
            return []
        m = float(s.mean())
        if (typ == "mean_above" and m > thr) or (typ == "mean_below" and m < thr):
            out.append(_finding(rule, ctx, value=m, n=len(s), basis=f"küszöb: {_fmt(thr)}",
                                chart=chart, fields={"value": _fmt(m), "threshold": _fmt(thr)}))
        return out

    if typ in ("consecutive_z_above", "consecutive_z_below"):
        metrics = ([m for m in d.hrv_metrics() if fnmatch.fnmatch(m, metric)]
                   if "*" in metric else [metric])
        for mk in metrics:
            st = d.metric_stats(mk)
            if st.empty or len(d.series(mk)) < ctx.min_days or thr is None:
                continue
            z = st["z_score"]
            mask = (z >= thr) if typ == "consecutive_z_above" else (z <= -thr)
            run, end = longest_run(mask.fillna(False))
            need = int(ctx.p(rule.get("days")) or 1)
            if run >= need:
                method = mk.replace("hrv_", "").upper()
                out.append(_finding(rule, ctx, value=float(run), n=int(st["z_score"].notna().sum()),
                                    basis="személyes alapvonal (megelőző 60 nap)",
                                    chart="hrv_trend" if mk.startswith("hrv") else "rhr_trend",
                                    fields={"days": run, "last_date": fmt_date(end),
                                            "threshold": fmt_num(thr, 1), "method": method}))
        return out

    if typ in ("change_abs", "change_pct"):
        cur, prev = d.series(metric), d.series(metric, prev=True)
        if len(cur) < ctx.min_days or len(prev) < ctx.min_days or thr is None:
            return []
        delta = float(cur.mean() - prev.mean())
        sign = rule["threshold"].get("sign", 0)
        if typ == "change_abs":
            fire = ((delta >= thr) if sign > 0 else (delta <= -thr) if sign < 0
                    else abs(delta) >= thr)
            fields = {"delta": fmt_num(abs(delta), 1)}
        else:
            pct = 100.0 * delta / float(prev.mean()) if prev.mean() else 0.0
            fire = abs(pct) >= thr
            fields = {"delta_pct": fmt_signed(pct, 1)}
        if fire:
            out.append(_finding(rule, ctx, value=delta, n=min(len(cur), len(prev)),
                                basis=f"előző időszak ({fmt_date(d.cmp.start)} – "
                                      f"{fmt_date(d.cmp.end)})", chart=chart, fields=fields))
        return out

    if typ in ("weekly_mean_below", "weekly_mean_at_least"):
        w = d.weekly
        if w.empty or metric not in {"who_equiv_min", "strength_days"}:
            return []
        w = w[w["days_with_data"] >= 4]
        col = metric
        vals = w[col].dropna()
        if len(vals) < 2 or thr is None:
            return []
        m = float(vals.mean())
        if (typ == "weekly_mean_below" and m < thr) or (typ == "weekly_mean_at_least" and m >= thr):
            out.append(_finding(rule, ctx, value=m, n=int(w["days_with_data"].sum()),
                                basis=f"irányadó: {_fmt(thr)} / hét", chart="who_weekly",
                                fields={"value": fmt_num(m, 0 if m >= 10 else 1),
                                        "threshold": _fmt(thr)}))
        return out

    if typ == "days_above":
        ld = d.load
        if ld.empty or thr is None:
            return []
        valid = ld[ld["acwr_valid"]]
        hit = valid[valid["acwr"] > thr]
        need = int(ctx.p(rule.get("days")) or 1)
        if len(hit) >= need:
            out.append(_finding(rule, ctx, value=float(len(hit)), n=len(valid),
                                basis=f"küszöb: {fmt_num(thr, 1)} (közelítés)", chart="acwr",
                                fields={"days": len(hit), "threshold": fmt_num(thr, 1),
                                        "last_date": fmt_date(hit["date"].max())}))
        return out

    if typ == "count_below":
        s = d.series(metric)
        if len(s) < ctx.min_days or thr is None:
            return []
        n = int((s < thr).sum())
        if n >= int(ctx.p(rule.get("days")) or 1):
            out.append(_finding(rule, ctx, value=float(n), n=len(s), basis=f"küszöb: {_fmt(thr)} %",
                                chart="spo2", fields={"days": n, "threshold": _fmt(thr)}))
        return out

    if typ == "span_change_abs":
        s = d.series(metric)
        if len(s) < 2 or thr is None:
            return []
        delta = float(s.iloc[-1] - s.iloc[0])
        if abs(delta) >= thr:
            out.append(_finding(rule, ctx, value=delta, n=len(s), basis="első és utolsó mérés",
                                chart="body", fields={"delta": fmt_signed(delta, 1)}))
        return out

    if typ == "regularity_above":
        r = d.regularity
        vals = r["bedtime_std7"].dropna() if len(r) else pd.Series(dtype=float)
        if len(vals) < ctx.min_days or thr is None:
            return []
        m = float(vals.mean())
        if m > thr:
            out.append(_finding(rule, ctx, value=m, n=len(vals), basis=f"küszöb: {_fmt(thr)} perc",
                                chart="sleep_timing", fields={"value": _fmt(m),
                                                              "threshold": _fmt(thr)}))
        return out

    if typ == "jetlag_above":
        diff, n = social_jetlag(d)
        if diff is None or thr is None:
            return []
        if abs(diff) >= thr:
            out.append(_finding(rule, ctx, value=diff, n=n, basis=f"küszöb: {_fmt(thr)} perc",
                                chart="sleep_timing", fields={"value": _fmt(abs(diff))}))
        return out
    raise ValueError(f"unknown rule type: {typ}")


def social_jetlag(d: ReportData) -> tuple[float | None, int]:
    """Mid-sleep (minutes after noon) on nights ending on Sat/Sun minus other nights."""
    bed, wake = d.series("bedtime_min"), d.series("waketime_min")
    both = pd.concat([bed, wake], axis=1, keys=["b", "w"], sort=True).dropna()
    if both.empty:
        return None, 0
    mid = (both["b"] + both["w"] + 720.0) / 2.0
    weekend = mid.index.day_of_week >= 5
    if weekend.sum() < 4 or (~weekend).sum() < 4:
        return None, len(mid)
    return float(mid[weekend].mean() - mid[~weekend].mean()), len(mid)


def evaluate(data: ReportData, cat: Catalog) -> list[Finding]:
    ctx = _Ctx(data, cat)
    out: list[Finding] = []
    for rule in cat.rules:
        out += _eval_rule(rule, ctx)
    return sorted(out, key=lambda f: f.sort_key)


# ---------------------------------------------------------------- correlations

@dataclass
class Correlation:
    corr_id: str
    label: str
    x: str
    y: str
    lag: int
    n: int
    rho: float | None
    p: float | None
    strength: str


def strength_label(rho: float | None) -> str:
    if rho is None or np.isnan(rho):
        return "nem számolható"
    a = abs(rho)
    return ("elhanyagolható" if a < 0.1 else "gyenge" if a < 0.3
            else "közepes" if a < 0.5 else "erős")


def correlations(data: ReportData, cat: Catalog,
                 extra: Callable[[str], pd.Series] | None = None) -> list[Correlation]:
    from scipy.stats import spearmanr

    min_n = int(cat.params.get("correlation_min_n", 20))
    out: list[Correlation] = []
    for c in cat.correlations:
        ys = ([m for m in data.hrv_metrics() if fnmatch.fnmatch(m, c["y"])]
              if "*" in c["y"] else [c["y"]])
        for y in ys:
            xs = extra(c["x"]) if extra and c["x"] == "training_load" else data.series(c["x"])
            ysr = data.series(y)
            if xs is None or xs.empty or ysr.empty:
                continue
            shifted = ysr.copy()
            shifted.index = shifted.index - pd.Timedelta(days=int(c.get("lag", 0)))
            j = pd.concat([xs, shifted], axis=1, keys=["x", "y"], sort=True).dropna()
            label = c["label_hu"] + (f" ({y.replace('hrv_', '').upper()})" if "*" in c["y"]
                                     else "")
            if len(j) < min_n or j["x"].nunique() < 3 or j["y"].nunique() < 3:
                out.append(Correlation(c["id"], label, c["x"], y, c.get("lag", 0), len(j), None,
                                       None, "nincs elég adat"))
                continue
            res = spearmanr(j["x"], j["y"])
            rho, p = float(res.statistic), float(res.pvalue)
            out.append(Correlation(c["id"], label, c["x"], y, c.get("lag", 0), len(j), rho, p,
                                   strength_label(rho)))
    return out
