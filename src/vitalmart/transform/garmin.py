"""Garmin pre-transform: YAML field mapping → typed temp tables (pure SQL).

Builds, from ``stg_garmin_record`` (newest batch wins per natural key):

* ``_g_<kind>``        one typed table per mapped file kind (fields of the YAML),
* ``_g_account_device`` the export's main device per Garmin account,
* ``_g_measure``        daily EAV values: mapped ``metric:`` fields + every unknown
                        numeric top-level field (auto metric ``garmin_<kind>_<field>``),
* ``_g_daily``          the source-provided daily summary (``daily:`` fields),
* ``_g_activity_type_map``.

The static SQL in ``sql/transform/garmin/*.sql`` then fills dims and facts.
"""

from __future__ import annotations

import re
from typing import Any

import duckdb

from vitalmart.config import AppConfig
from vitalmart.datamart.seed import insert_rows

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_FILTER = re.compile(r"^([A-Za-z0-9_]+)\[([A-Za-z0-9_]+)=([A-Za-z0-9_-]+)\]$")

DAILY_COLUMNS = [
    "steps", "distance_km", "active_kcal", "basal_kcal", "total_kcal", "flights_climbed",
    "exercise_min", "stand_min", "moderate_min", "vigorous_min", "resting_hr",
    "walking_hr_avg", "hr_min", "hr_max", "hr_avg", "stress_avg", "body_battery_max",
    "body_battery_min", "spo2_avg", "spo2_min", "respiratory_rate_avg",
]


def _jp(parts: list[str]) -> str:
    for p in parts:
        if not _IDENT.match(p):
            raise ValueError(f"invalid path segment: {p!r}")
    return "$" + "".join(f'."{p}"' for p in parts)


def json_value_sql(path: str, col: str = "payload") -> str:
    """Mapping path → SQL expression returning VARCHAR (or NULL)."""
    expr: str = col
    pending: list[str] = []
    for part in path.split("."):
        m = _FILTER.match(part)
        if m:
            name, key, val = m.groups()
            arr = f"json_extract({expr}, '{_jp([*pending, name])}[*]')"
            expr = (f"list_filter({arr}, lambda x: json_extract_string(x, '{_jp([key])}') = "
                    f"'{val}')[1]")
            pending = []
        else:
            pending.append(part)
    return f"json_extract_string({expr}, '{_jp(pending) if pending else '$'}')"


def field_sql(spec: dict[str, Any]) -> str:
    paths = spec["path"] if isinstance(spec["path"], list) else [spec["path"]]
    raws = [json_value_sql(p) for p in paths]
    raw = raws[0] if len(raws) == 1 else f"coalesce({', '.join(raws)})"
    typ = spec.get("type", "number")
    factor = float(spec.get("factor", 1))
    divide = float(spec.get("divide", 1))
    if typ == "number":
        out = f"TRY_CAST({raw} AS DOUBLE)"
        if factor != 1:
            out += f" * {factor!r}"
        if divide != 1:
            out = f"({out}) / {divide!r}"
        return out
    if typ == "string":
        return raw
    if typ == "date":
        return f"TRY_CAST(left({raw}, 10) AS DATE)"
    if typ == "timestamp":
        return f"TRY_CAST({raw} AS TIMESTAMP)"
    if typ == "epoch_ms":
        return f"to_timestamp(TRY_CAST({raw} AS DOUBLE) / 1000.0)::TIMESTAMP"
    raise ValueError(f"unknown field type: {typ}")


def _user_pk_sql(mapping: dict[str, Any]) -> str:
    exprs = [json_value_sql(p) for p in mapping.get("user_pk_paths", [])]
    rx = mapping.get("user_pk_from_filename")
    if rx:
        exprs.append(f"nullif(regexp_extract(file_name, '{rx}', 1), '')")
    return f"coalesce({', '.join(exprs)})" if exprs else "NULL"


def _snake(name_sql: str) -> str:
    return f"lower(regexp_replace({name_sql}, '([a-z0-9])([A-Z])', '\\1_\\2', 'g'))"


def prepare(con: duckdb.DuckDBPyConnection, cfg: AppConfig) -> None:
    mapping = cfg.load_yaml("garmin_mapping.yaml")
    files: dict[str, dict[str, Any]] = mapping["files"]
    user_pk = _user_pk_sql(mapping)

    # ---- typed table per mapped kind
    for kind, spec in files.items():
        cols = [f"{field_sql(f)} AS {name}" for name, f in (spec.get("fields") or {}).items()
                if _IDENT.match(name)]
        if kind == "activities" and spec.get("hr_zones"):
            hz = spec["hr_zones"]
            for z in hz["zones"]:
                cols.append(field_sql({"path": hz["path_template"].format(zone=z),
                                       "factor": hz.get("factor", 1),
                                       "divide": hz.get("divide", 1)}) + f" AS hr_zone_{z}_sec")
        select_cols = (", " + ", ".join(cols)) if cols else ""
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE _g_{kind} AS
            SELECT row_hash, nk_hash, import_batch_id, file_name, payload,
                   {user_pk} AS user_pk {select_cols}
            FROM stg_garmin_record
            WHERE file_kind = '{kind}'
            QUALIFY row_number() OVER (PARTITION BY nk_hash
                                       ORDER BY import_batch_id DESC, row_hash) = 1
        """)

    # ---- generic kinds (unmapped files): raw + date + user
    date_sql = "coalesce(" + ", ".join(
        f"TRY_CAST(left({json_value_sql(p)}, 10) AS DATE)"
        for p in mapping.get("date_paths", ["calendarDate"])) + ")"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _g_generic AS
        SELECT row_hash, nk_hash, import_batch_id, file_name, payload, file_kind,
               {user_pk} AS user_pk, {date_sql} AS calendar_date
        FROM stg_garmin_record
        WHERE file_kind LIKE 'generic:%'
        QUALIFY row_number() OVER (PARTITION BY nk_hash ORDER BY import_batch_id DESC,
                                   row_hash) = 1
    """)

    # ---- account → main device (mode of device ids seen in the export)
    dev_parts = [f"SELECT user_pk, device_id FROM _g_{k}" for k, s in files.items()
                 if "device_id" in (s.get("fields") or {})]
    dev_union = " UNION ALL ".join(dev_parts) if dev_parts else \
        "SELECT NULL::VARCHAR AS user_pk, NULL::VARCHAR AS device_id"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _g_account_device AS
        SELECT user_pk, mode(device_id) AS device_id
        FROM ({dev_union}) WHERE device_id IS NOT NULL AND user_pk IS NOT NULL
        GROUP BY user_pk
    """)

    # ---- daily EAV: mapped metric fields
    parts: list[str] = []
    for kind, spec in files.items():
        fields = spec.get("fields") or {}
        if "calendar_date" not in fields or spec.get("eav", True) is False:
            continue
        for name, f in fields.items():
            if f.get("metric"):
                parts.append(f"""
                    SELECT '{kind}' AS kind, user_pk, {'device_id' if 'device_id' in fields
                                                       else 'NULL::VARCHAR'} AS device_id,
                           calendar_date, '{f['metric']}' AS metric_key, {name} AS value,
                           import_batch_id, FALSE AS is_auto, '{name}' AS source_field
                    FROM _g_{kind} WHERE {name} IS NOT NULL AND calendar_date IS NOT NULL""")

    # ---- daily EAV: unknown numeric top-level fields (SPEC 4.1/B, M2 acceptance)
    ignore = mapping.get("ignore_field_patterns", [])
    ign_sql = " OR ".join(f"regexp_matches(k, '{p}')" for p in ignore) or "FALSE"
    for kind, spec in files.items():
        fields = spec.get("fields") or {}
        if "calendar_date" not in fields or spec.get("eav", True) is False:
            continue
        known = sorted({(p if isinstance(p, str) else p[0]).split(".")[0].split("[")[0]
                        for f in fields.values()
                        for p in (f["path"] if isinstance(f["path"], list) else [f["path"]])})
        known_sql = ", ".join(f"'{k}'" for k in known) or "''"
        dev = "device_id" if "device_id" in fields else "NULL::VARCHAR"
        parts.append(f"""
            SELECT '{kind}', user_pk, device_id, calendar_date,
                   'garmin_{kind}_' || {_snake('k')}, TRY_CAST(v AS DOUBLE), import_batch_id,
                   TRUE, k
            FROM (SELECT user_pk, {dev} AS device_id, calendar_date, import_batch_id,
                         unnest(json_keys(payload)) AS k, payload FROM _g_{kind})
            CROSS JOIN LATERAL (SELECT json_extract(payload, '$."' || k || '"') AS jv,
                                       json_extract_string(payload, '$."' || k || '"') AS v)
            WHERE calendar_date IS NOT NULL AND k NOT IN ({known_sql})
              AND json_type(jv) IN ('BIGINT', 'UBIGINT', 'DOUBLE', 'HUGEINT')
              AND NOT ({ign_sql})""")
    parts.append(f"""
        SELECT replace(file_kind, 'generic:', ''), user_pk, NULL::VARCHAR, calendar_date,
               'garmin_' || replace(file_kind, 'generic:', '') || '_' || {_snake('k')},
               TRY_CAST(v AS DOUBLE), import_batch_id, TRUE, k
        FROM (SELECT file_kind, user_pk, calendar_date, import_batch_id,
                     unnest(json_keys(payload)) AS k, payload
              FROM _g_generic WHERE json_type(payload) = 'OBJECT')
        CROSS JOIN LATERAL (SELECT json_extract(payload, '$."' || k || '"') AS jv,
                                   json_extract_string(payload, '$."' || k || '"') AS v)
        WHERE calendar_date IS NOT NULL
          AND json_type(jv) IN ('BIGINT', 'UBIGINT', 'DOUBLE', 'HUGEINT')
          AND NOT ({ign_sql})""")
    con.execute("CREATE OR REPLACE TEMP TABLE _g_measure AS " + " UNION ALL ".join(parts))

    # ---- source-provided daily summary
    uds_fields = (files.get("uds") or {}).get("fields") or {}
    by_col = {f["daily"]: name for name, f in uds_fields.items() if f.get("daily")}
    unknown = set(by_col) - set(DAILY_COLUMNS)
    if unknown:
        raise ValueError(f"garmin_mapping.yaml: unknown daily columns {sorted(unknown)}")
    daily_cols = ", ".join(f"{by_col.get(c, 'NULL::DOUBLE')} AS {c}"
                           for c in DAILY_COLUMNS)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _g_daily AS
        SELECT user_pk, calendar_date, import_batch_id, {daily_cols} FROM _g_uds
        WHERE calendar_date IS NOT NULL
    """)

    con.execute("CREATE OR REPLACE TEMP TABLE _g_activity_type_map "
                "(type_key VARCHAR, unified_type VARCHAR, category VARCHAR)")
    insert_rows(con, "_g_activity_type_map", ["type_key", "unified_type", "category"],
                [(k, v["unified"], v["category"])
                 for k, v in (mapping.get("activity_types") or {}).items()])
    cats = mapping.get("stress_categories") or []
    con.execute("CREATE OR REPLACE TEMP TABLE _g_stress_cat (max_value DOUBLE, cat VARCHAR)")
    insert_rows(con, "_g_stress_cat", ["max_value", "cat"],
                [(float(c["max"]), c["key"]) for c in cats])
