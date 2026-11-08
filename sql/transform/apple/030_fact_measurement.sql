-- Generic EAV store: every Apple record except dedicated-target ones
-- (heart_rate_sample, sleep) + the daily ActivitySummary rings.
DELETE FROM fact_measurement WHERE source_key LIKE 'apple:%';

INSERT INTO fact_measurement
SELECT metric_key, source_key, start_utc, start_local, start_off, end_utc, end_local,
       date_key, minute_of_day, value,
       CASE WHEN value_num IS NULL THEN value_text END,
       unit_out, quality_flag, import_batch_id, nk_hash
FROM _apple_rec
WHERE target = 'measurement';

-- ActivitySummary: a source-provided daily total, stored at local midnight.
INSERT INTO fact_measurement
WITH s AS (
    SELECT * FROM stg_apple_activity_summary
    QUALIFY row_number() OVER (PARTITION BY nk_hash ORDER BY import_batch_id DESC, row_hash) = 1
), d AS (
    SELECT s.*, try_strptime(date_components, '%Y-%m-%d')::TIMESTAMP AS local_midnight
    FROM s
), z AS (
    -- offset valid at local midnight (approximate the instant with the local value)
    SELECT d.*, o.offset_min
    FROM d ASOF LEFT JOIN tz_offset o ON d.local_midnight >= o.valid_from_utc
    WHERE d.local_midnight IS NOT NULL
), u AS (
    SELECT z.*, 'summary_active_energy' AS metric_key,
           active_energy * coalesce(mu.factor, 1) AS value, 'kcal' AS unit
    FROM z LEFT JOIN map_unit mu ON mu.from_unit = z.active_energy_unit AND mu.to_unit = 'kcal'
    UNION ALL
    SELECT z.*, 'summary_exercise_time', exercise_time, 'min' FROM z
    UNION ALL
    SELECT z.*, 'summary_stand_hours', stand_hours, 'count' FROM z
)
SELECT metric_key, 'apple:activity_summary',
       local_midnight - to_minutes(offset_min), local_midnight, offset_min, NULL, NULL,
       CAST(strftime(local_midnight, '%Y%m%d') AS INTEGER), 0, value, NULL, unit, 'ok',
       import_batch_id, nk_hash
FROM u
WHERE value IS NOT NULL;
