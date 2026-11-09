-- Derived layer 1: one value per person × day × metric from the PRIMARY source.
-- Candidates come from every source (quality_flag = 'ok' only); the primary is the
-- per-metric override (derived.primary_source) or else the best device type in
-- derived.source_priority; ties are broken by source_key (deterministic).

CREATE OR REPLACE TEMP TABLE _src AS
SELECT s.source_key, coalesce(s.person_key, 'unassigned') AS person_key, s.vendor,
       coalesce(p.prio, 99) AS prio
FROM dim_source s LEFT JOIN _priority p ON p.device_type = s.device_type
WHERE s.source_key NOT IN (SELECT source_key FROM map_excluded_source);   -- Beállítások

CREATE OR REPLACE TEMP TABLE _cand AS
-- daily summary columns (UNPIVOT drops NULLs)
SELECT source_key, CAST(strptime(CAST(date_key AS VARCHAR), '%Y%m%d') AS DATE) AS date,
       metric_key, value
FROM (
    UNPIVOT (
        SELECT source_key, date_key, steps, distance_km, active_kcal, total_kcal, resting_hr,
               walking_hr_avg, hr_avg, stress_avg, body_battery_max, body_battery_min,
               spo2_avg, respiratory_rate_avg, exercise_min, moderate_min, vigorous_min,
               -- WHO equivalent minutes: moderate + 2 × vigorous; Apple has no intensity
               -- split, its Exercise-ring minutes count as moderate (conservative)
               CASE WHEN moderate_min IS NOT NULL OR vigorous_min IS NOT NULL
                    THEN coalesce(moderate_min, 0) + 2 * coalesce(vigorous_min, 0)
                    ELSE exercise_min END AS who_equiv_min
        FROM fact_daily_summary WHERE quality_flag = 'ok'
    ) ON COLUMNS(* EXCLUDE (source_key, date_key)) INTO NAME metric_key VALUE value
)
UNION ALL
-- main sleep session of the night (asleep time present)
SELECT source_key, sleep_date, metric_key, value
FROM (
    UNPIVOT (
        SELECT source_key, sleep_date,
               asleep_min AS sleep_min, in_bed_min AS sleep_in_bed_min,
               efficiency_pct AS sleep_efficiency, sleep_score,
               deep_min AS sleep_deep_min, light_min AS sleep_light_min, rem_min AS sleep_rem_min,
               awake_min AS sleep_awake_min,
               date_diff('second',
                         CAST(CAST(start_local - INTERVAL 12 HOUR AS DATE) AS TIMESTAMP)
                             + INTERVAL 12 HOUR, start_local) / 60.0 AS bedtime_min,
               date_diff('second', CAST(CAST(end_local AS DATE) AS TIMESTAMP), end_local)
                   / 60.0 AS waketime_min
        FROM fact_sleep_session
        WHERE is_main AND quality_flag = 'ok' AND asleep_min > 0
    ) ON COLUMNS(* EXCLUDE (source_key, sleep_date)) INTO NAME metric_key VALUE value
)
UNION ALL
-- nightly HRV, per method (never mixed); ln(HRV) for the trend (SPEC 5.4)
SELECT source_key, sleep_date, 'hrv_' || lower(hrv_method), hrv_avg_ms
FROM fact_hrv_nightly WHERE quality_flag = 'ok' AND hrv_avg_ms > 0
UNION ALL
SELECT source_key, sleep_date, 'ln_hrv_' || lower(hrv_method), ln(hrv_avg_ms)
FROM fact_hrv_nightly WHERE quality_flag = 'ok' AND hrv_avg_ms > 0
UNION ALL
SELECT source_key, CAST(strptime(CAST(date_key AS VARCHAR), '%Y%m%d') AS DATE), 'vo2max', vo2max
FROM fact_vo2max WHERE quality_flag = 'ok' AND vo2max IS NOT NULL
UNION ALL
SELECT source_key, date, metric_key, value
FROM (
    UNPIVOT (
        SELECT source_key, CAST(ts_local AS DATE) AS date,
               arg_max(body_mass_kg, ts_utc) FILTER (WHERE body_mass_kg IS NOT NULL) AS body_mass,
               arg_max(body_fat_pct, ts_utc) FILTER (WHERE body_fat_pct IS NOT NULL) AS body_fat_pct,
               arg_max(bmi, ts_utc) FILTER (WHERE bmi IS NOT NULL) AS bmi
        FROM fact_body_measure WHERE quality_flag = 'ok'
        GROUP BY source_key, CAST(ts_local AS DATE)
    ) ON COLUMNS(* EXCLUDE (source_key, date)) INTO NAME metric_key VALUE value
)
UNION ALL
-- other curated daily metrics from the EAV store (e.g. Garmin stress breakdown,
-- Body Battery, sleep sub-scores), aggregated with the catalogue's rule;
-- excludes raw samples already summarised above, auto metrics and sensitive ones
SELECT f.source_key, CAST(f.ts_local AS DATE), f.metric_key,
       CASE any_value(d.agg_rule)
            WHEN 'SUM' THEN sum(f.value) WHEN 'MIN' THEN min(f.value)
            WHEN 'MAX' THEN max(f.value) WHEN 'LAST' THEN arg_max(f.value, f.ts_utc)
            ELSE avg(f.value) END
FROM fact_measurement f JOIN dim_metric d USING (metric_key)
WHERE f.quality_flag = 'ok' AND f.value IS NOT NULL AND NOT d.is_auto
  AND d.category IN ('stress', 'sleep', 'respiration', 'heart', 'activity', 'body')
  AND f.metric_key NOT IN (
      'heart_rate', 'resting_hr', 'walking_hr_avg', 'hrv_sdnn', 'hrv_rmssd', 'steps',
      'distance_walk_run', 'active_energy', 'basal_energy', 'exercise_time', 'stand_time',
      'flights_climbed', 'vo2max', 'spo2', 'respiratory_rate', 'body_mass', 'body_fat_pct',
      'lean_body_mass', 'bmi', 'height', 'stress', 'body_battery', 'sleep_duration',
      'sleep_score', 'summary_active_energy', 'summary_exercise_time', 'summary_stand_hours')
GROUP BY f.source_key, CAST(f.ts_local AS DATE), f.metric_key;

CREATE OR REPLACE TEMP TABLE _cand_p AS
SELECT c.*, s.person_key, s.prio, s.vendor,
       (o.source_key IS NOT NULL) AS is_override
FROM _cand c
JOIN _src s USING (source_key)
LEFT JOIN _primary_override o ON o.metric_key = c.metric_key AND o.source_key = c.source_key
WHERE c.value IS NOT NULL AND isfinite(c.value);

DELETE FROM derived_person_daily;
INSERT INTO derived_person_daily
SELECT person_key, metric_key, date, yyyymmdd(date), value, source_key, n_sources
FROM (
    SELECT *, count(*) OVER (PARTITION BY person_key, metric_key, date) AS n_sources
    FROM _cand_p
    QUALIFY row_number() OVER (PARTITION BY person_key, metric_key, date
                               ORDER BY is_override DESC, prio, source_key) = 1
);
