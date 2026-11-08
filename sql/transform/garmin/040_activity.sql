-- Garmin activities: Connect summaries (JSON) first; FIT sessions only when no
-- JSON activity of the same source starts within ±2 minutes (same workout).
DELETE FROM fact_activity WHERE source_key LIKE 'garmin:%';

INSERT INTO fact_activity
WITH a AS (
    SELECT g.*, g_source(device_id, user_pk) AS sk FROM _g_activities g WHERE start_utc IS NOT NULL
), z AS (
    SELECT a.*, o.offset_min FROM a ASOF LEFT JOIN tz_offset o ON a.start_utc >= o.valid_from_utc
), e AS (
    SELECT z.*, start_utc + to_microseconds(CAST(coalesce(duration_min, 0) * 60e6 AS BIGINT))
               AS end_utc
    FROM z
)
SELECT 'garmin:' || activity_id, sk, 'garmin:' || lower(activity_type),
       yyyymmdd(start_utc + to_minutes(offset_min)),
       start_utc, end_utc, start_utc + to_minutes(offset_min), end_utc + to_minutes(offset_min),
       offset_min, duration_min, nullif(distance_km, 0), energy_kcal, hr_avg, hr_max, hr_min,
       CASE WHEN distance_km > 0 THEN avg_speed_kmh END,
       CASE WHEN distance_km > 0 AND avg_speed_kmh > 0 THEN 60.0 / avg_speed_kmh END,
       elevation_gain_m, aerobic_effect, anaerobic_effect, training_load, vo2max, NULL, NULL,
       CASE WHEN duration_min <= 0 OR duration_min > 24 * 60 OR hr_max > 230 OR hr_avg < 25
            THEN 'out_of_range' ELSE 'ok' END,
       import_batch_id
FROM e;

INSERT INTO fact_activity
WITH u AS (
    SELECT s.* FROM _g_fit_session s
    WHERE s.start_utc IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM fact_activity a
                      WHERE a.source_key = s.source_key
                        AND abs(date_diff('second', a.start_utc, s.start_utc)) <= 120)
), z AS (
    SELECT u.*, o.offset_min,
           u.start_utc + to_microseconds(CAST(coalesce(u.elapsed_s, u.timer_s, 0) * 1e6 AS BIGINT))
               AS end_utc,
           coalesce(u.timer_s, u.elapsed_s) / 60.0 AS duration_min,
           u.distance_m / 1000.0 AS distance_km
    FROM u ASOF LEFT JOIN tz_offset o ON u.start_utc >= o.valid_from_utc
)
SELECT 'garmin:fit:' || file_hash || ':' || msg_index, source_key, 'garmin:' || type_key,
       yyyymmdd(start_utc + to_minutes(offset_min)),
       start_utc, end_utc, start_utc + to_minutes(offset_min), end_utc + to_minutes(offset_min),
       offset_min, duration_min, nullif(distance_km, 0), kcal, hr_avg, hr_max, NULL,
       CASE WHEN distance_km > 0 AND duration_min > 0 THEN distance_km / (duration_min / 60) END,
       CASE WHEN distance_km > 0 AND duration_min > 0 THEN duration_min / distance_km END,
       ascent_m, aerobic_te, anaerobic_te, NULL, NULL, NULL, NULL,
       CASE WHEN duration_min <= 0 OR duration_min > 24 * 60 OR hr_max > 230 THEN 'out_of_range'
            ELSE 'ok' END,
       import_batch_id
FROM z;

DELETE FROM fact_activity_hr_zone WHERE source_key LIKE 'garmin:%';
INSERT INTO fact_activity_hr_zone
SELECT 'garmin:' || activity_id, g_source(device_id, user_pk),
       CAST(regexp_extract(col, 'hr_zone_(\d+)_sec', 1) AS TINYINT), sec, 'ok', import_batch_id
FROM (
    UNPIVOT (SELECT activity_id, device_id, user_pk, import_batch_id, COLUMNS('hr_zone_.*')
             FROM _g_activities WHERE start_utc IS NOT NULL)
    ON COLUMNS('hr_zone_.*') INTO NAME col VALUE sec
)
WHERE sec IS NOT NULL;

-- HR sample context for Garmin: inside a Garmin sleep session -> sleep,
-- inside an activity -> activity (overrides)
UPDATE fact_heart_rate_sample h SET context = 'sleep'
FROM fact_sleep_session s
WHERE h.source_key LIKE 'garmin:%' AND s.source_key = h.source_key
  AND h.ts_utc >= s.start_utc AND h.ts_utc <= s.end_utc;
UPDATE fact_heart_rate_sample h SET context = 'activity'
FROM fact_activity a
WHERE h.source_key LIKE 'garmin:%' AND a.source_key = h.source_key
  AND h.ts_utc >= a.start_utc AND h.ts_utc <= a.end_utc;

UPDATE fact_sleep_session s SET hr_min = x.hr_min, hr_avg = coalesce(s.hr_avg, x.hr_avg)
FROM (
    SELECT s2.session_id, min(h.bpm) AS hr_min, avg(h.bpm) AS hr_avg
    FROM fact_sleep_session s2 JOIN fact_heart_rate_sample h
      ON h.source_key = s2.source_key AND h.ts_utc >= s2.start_utc AND h.ts_utc <= s2.end_utc
    WHERE s2.source_key LIKE 'garmin:%' AND h.quality_flag = 'ok'
    GROUP BY s2.session_id
) x
WHERE s.session_id = x.session_id;
