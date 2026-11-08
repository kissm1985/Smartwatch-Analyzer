-- Apple workouts -> dim_activity_type, fact_activity
DELETE FROM dim_activity_type WHERE vendor = 'Apple';
INSERT INTO dim_activity_type
SELECT DISTINCT
    'apple:' || w.activity_type, 'Apple', w.activity_type,
    coalesce(m.unified_type, 'other'),
    unified_name_hu(coalesce(m.unified_type, 'other')),
    m.category
FROM stg_apple_workout w
LEFT JOIN map_apple_workout_type m ON m.hk_type = w.activity_type;

DELETE FROM fact_activity WHERE source_key LIKE 'apple:%';
INSERT INTO fact_activity
WITH w AS (
    SELECT * FROM stg_apple_workout
    WHERE start_utc IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY nk_hash ORDER BY import_batch_id DESC, row_hash) = 1
), st AS (
    SELECT s.workout_row_hash,
        max(s.stat_avg) FILTER (WHERE s.stat_type = 'HKQuantityTypeIdentifierHeartRate') AS hr_avg,
        max(s.stat_max) FILTER (WHERE s.stat_type = 'HKQuantityTypeIdentifierHeartRate') AS hr_max,
        min(s.stat_min) FILTER (WHERE s.stat_type = 'HKQuantityTypeIdentifierHeartRate') AS hr_min,
        max(s.stat_sum * CASE WHEN s.unit = 'km' THEN 1 ELSE uk.factor END) FILTER (
            WHERE s.stat_type IN ('HKQuantityTypeIdentifierDistanceWalkingRunning',
                                  'HKQuantityTypeIdentifierDistanceCycling',
                                  'HKQuantityTypeIdentifierDistanceSwimming',
                                  'HKQuantityTypeIdentifierDistanceDownhillSnowSports')) AS dist_km,
        max(s.stat_sum * CASE WHEN s.unit = 'kcal' THEN 1 ELSE ue.factor END) FILTER (
            WHERE s.stat_type = 'HKQuantityTypeIdentifierActiveEnergyBurned') AS kcal
    FROM stg_apple_workout_stat s
    LEFT JOIN map_unit uk ON uk.from_unit = s.unit AND uk.to_unit = 'km'
    LEFT JOIN map_unit ue ON ue.from_unit = s.unit AND ue.to_unit = 'kcal'
    GROUP BY s.workout_row_hash
), j AS (
    SELECT w.*, st.hr_avg, st.hr_max, st.hr_min, st.dist_km, st.kcal,
        coalesce(w.duration * CASE WHEN w.duration_unit = 'min' THEN 1 ELSE ud.factor END,
                 date_diff('second', w.start_utc, w.end_utc) / 60.0) AS duration_min,
        coalesce(w.total_distance * CASE WHEN w.total_distance_unit = 'km' THEN 1
                                          ELSE udist.factor END, st.dist_km) AS distance_km,
        coalesce(w.total_energy * CASE WHEN w.total_energy_unit = 'kcal' THEN 1
                                        ELSE uen.factor END, st.kcal) AS energy_kcal,
        json_extract_string(w.metadata, '$.HKElevationAscended') AS elev_raw
    FROM w
    LEFT JOIN st ON st.workout_row_hash = w.row_hash
    LEFT JOIN map_unit ud ON ud.from_unit = w.duration_unit AND ud.to_unit = 'min'
    LEFT JOIN map_unit udist ON udist.from_unit = w.total_distance_unit AND udist.to_unit = 'km'
    LEFT JOIN map_unit uen ON uen.from_unit = w.total_energy_unit AND uen.to_unit = 'kcal'
), z AS (
    SELECT j.*, o.offset_min
    FROM j ASOF LEFT JOIN tz_offset o ON j.start_utc >= o.valid_from_utc
)
SELECT
    nk_hash, 'apple:' || coalesce(source_name, 'unknown'), 'apple:' || activity_type,
    CAST(strftime(start_utc + to_minutes(offset_min), '%Y%m%d') AS INTEGER),
    start_utc, end_utc, start_utc + to_minutes(offset_min), end_utc + to_minutes(offset_min),
    offset_min, duration_min,
    nullif(distance_km, 0), energy_kcal, hr_avg, hr_max, hr_min,
    CASE WHEN distance_km > 0 AND duration_min > 0 THEN distance_km / (duration_min / 60.0) END,
    CASE WHEN distance_km > 0 AND duration_min > 0 THEN duration_min / distance_km END,
    try_cast(regexp_extract(elev_raw, '^([0-9.]+)', 1) AS DOUBLE) *
        CASE regexp_extract(elev_raw, '([a-z]+)$', 1)
            WHEN 'cm' THEN 0.01 WHEN 'm' THEN 1 WHEN 'ft' THEN 0.3048 END,
    NULL, NULL, NULL, NULL, NULL,   -- aerobic/anaerobic effect, load, vo2max, recovery: Garmin
    NULL,                           -- hr_recovery_1min (filled below)
    CASE WHEN duration_min <= 0 OR duration_min > 24 * 60 OR hr_max > 230 OR hr_avg < 25
         THEN 'out_of_range' ELSE 'ok' END,
    import_batch_id
FROM z;

-- HR recovery (1 min): the Apple record written right after the workout end
UPDATE fact_activity a SET hr_recovery_1min = x.v
FROM (
    SELECT a2.activity_id, arg_min(m.value, m.ts_utc) AS v
    FROM fact_activity a2
    JOIN fact_measurement m
      ON m.metric_key = 'hr_recovery_1min' AND m.source_key = a2.source_key
     AND m.ts_utc >= a2.end_utc - INTERVAL 5 MINUTE AND m.ts_utc <= a2.end_utc + INTERVAL 60 MINUTE
    WHERE a2.source_key LIKE 'apple:%' AND m.quality_flag = 'ok'
    GROUP BY a2.activity_id
) x
WHERE a.activity_id = x.activity_id;

-- workout windows override other HR contexts
UPDATE fact_heart_rate_sample h SET context = 'activity'
FROM fact_activity a
WHERE h.source_key LIKE 'apple:%' AND a.source_key LIKE 'apple:%'
  AND h.ts_utc >= a.start_utc AND h.ts_utc <= a.end_utc;
