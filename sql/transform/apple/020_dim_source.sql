-- Apple sources/devices (one per sourceName). first/last date are local dates.
-- person_key: the person of the import batches that delivered the source;
-- person_conflict = the same source arrived under different persons (import is refused).
DELETE FROM dim_source WHERE vendor = 'Apple';

INSERT INTO dim_source
WITH src AS (
    SELECT source_key, source_name, source_version, device, start_utc, start_local,
           import_batch_id
    FROM _apple_rec
    UNION ALL
    SELECT apple_source_key(w.source_name, w.device), w.source_name, w.source_version,
           w.device, w.start_utc, w.start_utc + to_minutes(o.offset_min), w.import_batch_id
    FROM stg_apple_workout w ASOF LEFT JOIN tz_offset o ON w.start_utc >= o.valid_from_utc
    WHERE w.start_utc IS NOT NULL
), per_batch AS (
    -- person per (source, batch): computed on the small distinct set
    SELECT DISTINCT s.source_key, b.person_key
    FROM (SELECT DISTINCT source_key, import_batch_id FROM src) s
    JOIN dim_import_batch b ON b.import_batch_id = s.import_batch_id
), persons AS (
    SELECT source_key, min(person_key) AS person_key, count(DISTINCT person_key) > 1 AS conflict
    FROM per_batch GROUP BY source_key
)
SELECT
    src.source_key,
    'Apple',
    any_value(src.source_name) || coalesce(' › ' || any_value(apple_ext_device(src.device)), ''),
    arg_max(CASE
        WHEN apple_ext_device(src.device) IS NOT NULL THEN 'other'
        WHEN src.device ILIKE '%name:Apple Watch%' OR src.source_name ILIKE '%watch%' THEN 'watch'
        WHEN src.device ILIKE '%name:iPhone%' OR src.source_name ILIKE '%iphone%' THEN 'phone'
        WHEN src.device IS NULL THEN 'app'
        ELSE 'other' END, src.start_utc),
    arg_max(nullif(regexp_extract(src.device, 'model:([^,>]+)', 1), ''), src.start_utc),
    arg_max(nullif(regexp_extract(src.device, 'hardware:([^,>]+)', 1), ''), src.start_utc),
    arg_max(coalesce(nullif(regexp_extract(src.device, 'software:([^,>]+)', 1), ''),
                     src.source_version), src.start_utc),
    min(src.start_local)::DATE,
    max(src.start_local)::DATE,
    any_value(p.person_key),
    coalesce(any_value(p.conflict), FALSE)
FROM src LEFT JOIN persons p ON p.source_key = src.source_key
GROUP BY src.source_key;

INSERT INTO dim_source
SELECT 'apple:activity_summary', 'Apple', 'Activity Summary (gyűrűk)', 'summary', NULL, NULL,
       NULL, min(try_strptime(s.date_components, '%Y-%m-%d'))::DATE,
       max(try_strptime(s.date_components, '%Y-%m-%d'))::DATE,
       min(b.person_key), count(DISTINCT b.person_key) > 1
FROM stg_apple_activity_summary s
JOIN dim_import_batch b ON b.import_batch_id = s.import_batch_id
HAVING count(*) > 0;
