-- Garmin EAV (daily values incl. unknown fields), FIT heart rate and stress samples.
DELETE FROM fact_measurement WHERE source_key LIKE 'garmin:%';

INSERT INTO dim_metric
SELECT metric_key, any_value(source_field), any_value(source_field), 'other', NULL, 'AVG',
       'neutral', NULL, NULL, 'Automatikusan felvett metrika ismeretlen Garmin mezőből.', TRUE,
       any_value(kind) || '.' || any_value(source_field)
FROM _g_measure
WHERE is_auto AND metric_key NOT IN (SELECT metric_key FROM dim_metric)
GROUP BY metric_key;

INSERT INTO fact_measurement
WITH m AS (
    SELECT g.*, g_source(device_id, user_pk) AS source_key,
           CAST(calendar_date AS TIMESTAMP) AS local_midnight
    FROM _g_measure g WHERE value IS NOT NULL
), z AS (
    SELECT m.*, o.offset_min FROM m ASOF LEFT JOIN tz_offset o ON m.local_midnight >= o.valid_from_utc
)
SELECT DISTINCT ON (z.metric_key, z.source_key, z.calendar_date)
       z.metric_key, z.source_key, z.local_midnight - to_minutes(z.offset_min), z.local_midnight,
       z.offset_min, NULL, NULL, yyyymmdd(z.calendar_date), 0, z.value, NULL, dm.unit,
       CASE WHEN z.value < dm.valid_min OR z.value > dm.valid_max THEN 'out_of_range'
            ELSE 'ok' END,
       z.import_batch_id, z.kind || ':' || CAST(z.calendar_date AS VARCHAR)
FROM z LEFT JOIN dim_metric dm ON dm.metric_key = z.metric_key
ORDER BY z.metric_key, z.source_key, z.calendar_date, z.import_batch_id DESC;

-- height (biometrics) as a timestamped measurement
INSERT INTO fact_measurement
WITH b AS (
    SELECT g_source(NULL, user_pk) AS source_key, record_local, height_m, import_batch_id, nk_hash
    FROM _g_biometrics WHERE height_m IS NOT NULL AND record_local IS NOT NULL
), z AS (
    SELECT b.*, o.offset_min FROM b ASOF LEFT JOIN tz_offset o ON b.record_local >= o.valid_from_utc
)
SELECT 'height', source_key, record_local - to_minutes(offset_min), record_local, offset_min,
       NULL, NULL, yyyymmdd(record_local), hour(record_local) * 60 + minute(record_local),
       height_m, NULL, 'm',
       CASE WHEN height_m < 0.5 OR height_m > 2.6 THEN 'out_of_range' ELSE 'ok' END,
       import_batch_id, nk_hash
FROM z;

DELETE FROM fact_heart_rate_sample WHERE source_key LIKE 'garmin:%';
INSERT INTO fact_heart_rate_sample
WITH h AS (
    SELECT source_key, ts_utc, message, import_batch_id,
           TRY_CAST(json_extract_string(fields, '$.heart_rate') AS DOUBLE) AS bpm
    FROM _g_fit WHERE message IN ('record', 'monitoring') AND ts_utc IS NOT NULL
), d AS (
    SELECT DISTINCT ON (source_key, ts_utc) *
    FROM h WHERE bpm IS NOT NULL AND bpm > 0
    ORDER BY source_key, ts_utc, (message = 'record') DESC, import_batch_id DESC
), z AS (
    SELECT d.*, o.offset_min FROM d ASOF LEFT JOIN tz_offset o ON d.ts_utc >= o.valid_from_utc
)
SELECT source_key, ts_utc, ts_utc + to_minutes(offset_min), offset_min,
       yyyymmdd(ts_utc + to_minutes(offset_min)),
       CAST(hour(ts_utc + to_minutes(offset_min)) * 60 + minute(ts_utc + to_minutes(offset_min))
            AS SMALLINT),
       bpm, CASE WHEN message = 'record' THEN 'activity' ELSE 'rest' END,
       CASE WHEN bpm < 25 OR bpm > 230 THEN 'out_of_range' ELSE 'ok' END, import_batch_id
FROM z;

DELETE FROM fact_stress_sample WHERE source_key LIKE 'garmin:%';
INSERT INTO fact_stress_sample
WITH s AS (
    SELECT DISTINCT ON (source_key, ts_utc) source_key, ts_utc, import_batch_id,
           TRY_CAST(json_extract_string(fields, '$.stress_level_value') AS DOUBLE) AS v
    FROM _g_fit WHERE message = 'stress_level' AND ts_utc IS NOT NULL
    ORDER BY source_key, ts_utc, import_batch_id DESC
), z AS (
    SELECT s.*, o.offset_min FROM s ASOF LEFT JOIN tz_offset o ON s.ts_utc >= o.valid_from_utc
), c AS (
    SELECT z.*, (SELECT arg_min(cat, max_value) FROM _g_stress_cat sc WHERE sc.max_value >= z.v)
               AS cat
    FROM z
)
SELECT source_key, ts_utc, ts_utc + to_minutes(offset_min), offset_min,
       yyyymmdd(ts_utc + to_minutes(offset_min)),
       CAST(hour(ts_utc + to_minutes(offset_min)) * 60 + minute(ts_utc + to_minutes(offset_min))
            AS SMALLINT),
       v,
       CASE WHEN v < 0 THEN 'invalid' ELSE cat END,
       -- -1 = off wrist, -2 = too active to measure (kept, flagged)
       CASE WHEN v < 0 THEN 'invalid_value' WHEN v > 100 THEN 'out_of_range' ELSE 'ok' END,
       import_batch_id
FROM c WHERE v IS NOT NULL;
