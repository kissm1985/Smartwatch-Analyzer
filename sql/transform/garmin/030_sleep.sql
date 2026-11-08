-- Garmin sleep: one session per night with stage totals (the export has no
-- stage intervals, so fact_sleep_stage_interval stays empty for Garmin).
-- Garmin "light" ≈ Apple "Core" (dim_sleep_stage note). Same sleep_date
-- convention as Apple (configurable).
DELETE FROM fact_sleep_session WHERE source_key LIKE 'garmin:%';

INSERT INTO fact_sleep_session
WITH s AS (
    SELECT g.*, g_source(device_id, user_pk) AS sk
    FROM _g_sleep g WHERE start_utc IS NOT NULL AND end_utc > start_utc
), a AS (
    SELECT s.*, o.offset_min AS s_off FROM s ASOF LEFT JOIN tz_offset o ON s.start_utc >= o.valid_from_utc
), b AS (
    SELECT a.*, o.offset_min AS e_off FROM a ASOF LEFT JOIN tz_offset o ON a.end_utc >= o.valid_from_utc
), x AS (
    SELECT b.*,
           start_utc + to_minutes(s_off) AS s_loc, end_utc + to_minutes(e_off) AS e_loc,
           date_diff('second', start_utc, end_utc) / 60.0 AS span_min,
           coalesce(deep_min, 0) + coalesce(light_min, 0) + coalesce(rem_min, 0) AS asleep
    FROM b
), y AS (
    SELECT x.*,
           CASE WHEN getvariable('sleep_day_convention') = 'start'
                THEN CAST(s_loc - INTERVAL 12 HOUR AS DATE) ELSE CAST(e_loc AS DATE) END
               AS sleep_date
    FROM x
)
SELECT md5('garmin|' || sk || '|' || CAST(start_utc AS VARCHAR)), sk,
       yyyymmdd(sleep_date), sleep_date,
       row_number() OVER (PARTITION BY sk, sleep_date ORDER BY asleep DESC, start_utc) = 1,
       start_utc, end_utc, s_loc, e_loc, s_off, e_off,
       span_min, asleep, deep_min, light_min, rem_min, awake_min, 0,
       CASE WHEN span_min > 0 AND asleep > 0 THEN least(100.0, 100.0 * asleep / span_min) END,
       sleep_score, hr_avg, NULL, respiration_avg, spo2_avg, stress_avg, TRUE,
       CASE WHEN asleep = 0 THEN 'no_stages'
            WHEN span_min > 20 * 60 THEN 'implausible_duration'
            WHEN asleep + coalesce(awake_min, 0) > span_min + 1 THEN 'overlapping_intervals'
            ELSE 'ok' END,
       import_batch_id
FROM y;
