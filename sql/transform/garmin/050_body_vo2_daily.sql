-- Garmin body measures (weight in g → kg via mapping), VO2max, daily summary.
DELETE FROM fact_body_measure WHERE source_key LIKE 'garmin:%';
INSERT INTO fact_body_measure
WITH w AS (
    SELECT DISTINCT ON (sk, weight_utc) *
    FROM (SELECT g_source(NULL, user_pk) AS sk, weight_utc, weight_kg, import_batch_id
          FROM _g_biometrics WHERE weight_kg IS NOT NULL AND weight_utc IS NOT NULL)
    ORDER BY sk, weight_utc, import_batch_id DESC
), h AS (
    SELECT ts_utc AS h_ts, value AS height_m FROM fact_measurement
    WHERE metric_key = 'height' AND quality_flag = 'ok' AND source_key LIKE 'garmin:%'
), wh AS (
    SELECT w.*, h.height_m FROM w ASOF LEFT JOIN h ON w.weight_utc >= h.h_ts
), z AS (
    SELECT wh.*, o.offset_min FROM wh ASOF LEFT JOIN tz_offset o ON wh.weight_utc >= o.valid_from_utc
)
SELECT sk, weight_utc, weight_utc + to_minutes(offset_min), offset_min,
       yyyymmdd(weight_utc + to_minutes(offset_min)), weight_kg, NULL,
       CASE WHEN height_m > 0 THEN weight_kg / (height_m * height_m) END, height_m > 0,
       NULL, NULL, NULL,
       CASE WHEN weight_kg < 20 OR weight_kg > 350 THEN 'out_of_range' ELSE 'ok' END,
       import_batch_id
FROM z;

DELETE FROM fact_vo2max WHERE source_key LIKE 'garmin:%';
INSERT INTO fact_vo2max
SELECT sk, yyyymmdd(calendar_date), arg_max(vo2max, updated_at), count(*),
       CASE WHEN arg_max(vo2max, updated_at) BETWEEN 10 AND 95 THEN 'ok' ELSE 'out_of_range' END,
       max(import_batch_id)
FROM (SELECT g_source(device_id, user_pk) AS sk, * FROM _g_vo2max
      WHERE vo2max IS NOT NULL AND calendar_date IS NOT NULL)
GROUP BY sk, calendar_date;

-- Source-provided daily summary is primary (SPEC 5.3/4); hr_avg / sample count
-- come from FIT samples when available. Garmin stress < 0 = not enough data.
DELETE FROM fact_daily_summary WHERE source_key LIKE 'garmin:%';
INSERT INTO fact_daily_summary
WITH d AS (
    SELECT g.*, g_source(NULL, user_pk) AS sk FROM _g_daily g
    QUALIFY row_number() OVER (PARTITION BY sk, calendar_date ORDER BY import_batch_id DESC) = 1
), h AS (
    SELECT date_key, source_key, avg(bpm) AS hr_avg, count(*) AS n
    FROM fact_heart_rate_sample WHERE source_key LIKE 'garmin:%' AND quality_flag = 'ok'
    GROUP BY date_key, source_key
)
SELECT yyyymmdd(d.calendar_date), d.sk, d.steps, d.distance_km, d.active_kcal, d.basal_kcal,
       d.total_kcal, d.flights_climbed, d.exercise_min, d.stand_min, d.moderate_min,
       d.vigorous_min, d.resting_hr, d.walking_hr_avg, d.hr_min, d.hr_max,
       coalesce(d.hr_avg, h.hr_avg), coalesce(h.n, 0),
       CASE WHEN d.stress_avg >= 0 THEN d.stress_avg END,
       d.body_battery_max, d.body_battery_min, d.spo2_avg, d.spo2_min, d.respiratory_rate_avg,
       CASE WHEN d.resting_hr NOT BETWEEN 25 AND 150 OR d.hr_max > 230 OR d.hr_min < 25
            THEN 'out_of_range'
            ELSE 'ok' END,
       d.import_batch_id
FROM d LEFT JOIN h ON h.date_key = yyyymmdd(d.calendar_date) AND h.source_key = d.sk;
