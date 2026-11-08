-- Daily summary per (local date, source) from the granular facts.
-- Interval quantities (e.g. steps) are attributed to the local date of their start.
-- moderate/vigorous minutes: not provided by Apple (exercise_min is Apple's
-- "Exercise" ring minutes); they are filled by vendors that report them.
DELETE FROM fact_daily_summary;

INSERT INTO fact_daily_summary
WITH m AS (
    SELECT date_key, source_key,
        sum(value) FILTER (WHERE metric_key = 'steps') AS steps,
        sum(value) FILTER (WHERE metric_key = 'distance_walk_run') AS distance_km,
        sum(value) FILTER (WHERE metric_key = 'active_energy') AS active_kcal,
        sum(value) FILTER (WHERE metric_key = 'basal_energy') AS basal_kcal,
        sum(value) FILTER (WHERE metric_key = 'flights_climbed') AS flights,
        sum(value) FILTER (WHERE metric_key = 'exercise_time') AS exercise_min,
        sum(value) FILTER (WHERE metric_key = 'stand_time') AS stand_min,
        avg(value) FILTER (WHERE metric_key = 'resting_hr') AS resting_hr,
        avg(value) FILTER (WHERE metric_key = 'walking_hr_avg') AS walking_hr,
        avg(value) FILTER (WHERE metric_key = 'stress') AS stress_avg,
        max(value) FILTER (WHERE metric_key = 'body_battery') AS bb_max,
        min(value) FILTER (WHERE metric_key = 'body_battery') AS bb_min,
        avg(value) FILTER (WHERE metric_key = 'spo2') AS spo2_avg,
        min(value) FILTER (WHERE metric_key = 'spo2') AS spo2_min,
        avg(value) FILTER (WHERE metric_key = 'respiratory_rate') AS rr,
        max(import_batch_id) AS bid
    FROM fact_measurement
    WHERE quality_flag = 'ok'
      AND metric_key IN ('steps', 'distance_walk_run', 'active_energy', 'basal_energy',
                         'flights_climbed', 'exercise_time', 'stand_time', 'resting_hr',
                         'walking_hr_avg', 'stress', 'body_battery', 'spo2', 'respiratory_rate')
    GROUP BY date_key, source_key
), h AS (
    SELECT date_key, source_key, min(bpm) AS hr_min, max(bpm) AS hr_max, avg(bpm) AS hr_avg,
           count(*) AS n, max(import_batch_id) AS bid
    FROM fact_heart_rate_sample
    WHERE quality_flag = 'ok'
    GROUP BY date_key, source_key
)
SELECT
    coalesce(m.date_key, h.date_key), coalesce(m.source_key, h.source_key),
    m.steps, m.distance_km, m.active_kcal, m.basal_kcal,
    CASE WHEN m.active_kcal IS NOT NULL OR m.basal_kcal IS NOT NULL
         THEN coalesce(m.active_kcal, 0) + coalesce(m.basal_kcal, 0) END,
    m.flights, m.exercise_min, m.stand_min,
    NULL, NULL,
    m.resting_hr, m.walking_hr, h.hr_min, h.hr_max, h.hr_avg, coalesce(h.n, 0),
    m.stress_avg, m.bb_max, m.bb_min, m.spo2_avg, m.spo2_min, m.rr,
    'ok',
    greatest(coalesce(m.bid, 0), coalesce(h.bid, 0))
FROM m FULL OUTER JOIN h ON h.date_key = m.date_key AND h.source_key = m.source_key;
