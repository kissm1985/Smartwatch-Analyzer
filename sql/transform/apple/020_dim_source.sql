-- Apple sources/devices (one per sourceName). first/last date are local dates.
DELETE FROM dim_source WHERE vendor = 'Apple';

INSERT INTO dim_source
WITH src AS (
    SELECT source_key, source_name, source_version, device, start_utc, start_local
    FROM _apple_rec
    UNION ALL
    SELECT 'apple:' || coalesce(w.source_name, 'unknown'), w.source_name, w.source_version,
           w.device, w.start_utc, w.start_utc + to_minutes(o.offset_min)
    FROM stg_apple_workout w ASOF LEFT JOIN tz_offset o ON w.start_utc >= o.valid_from_utc
    WHERE w.start_utc IS NOT NULL
)
SELECT
    source_key,
    'Apple',
    any_value(source_name),
    arg_max(CASE
        WHEN device ILIKE '%name:Apple Watch%' OR source_name ILIKE '%watch%' THEN 'watch'
        WHEN device ILIKE '%name:iPhone%' OR source_name ILIKE '%iphone%' THEN 'phone'
        WHEN device IS NULL THEN 'app'
        ELSE 'other' END, start_utc),
    arg_max(nullif(regexp_extract(device, 'model:([^,>]+)', 1), ''), start_utc),
    arg_max(nullif(regexp_extract(device, 'hardware:([^,>]+)', 1), ''), start_utc),
    arg_max(coalesce(nullif(regexp_extract(device, 'software:([^,>]+)', 1), ''), source_version),
            start_utc),
    min(start_local)::DATE,
    max(start_local)::DATE
FROM src
GROUP BY source_key;

INSERT INTO dim_source
SELECT 'apple:activity_summary', 'Apple', 'Activity Summary (gyűrűk)', 'summary', NULL, NULL,
       NULL, min(try_strptime(date_components, '%Y-%m-%d'))::DATE,
       max(try_strptime(date_components, '%Y-%m-%d'))::DATE
FROM stg_apple_activity_summary
HAVING count(*) > 0;
