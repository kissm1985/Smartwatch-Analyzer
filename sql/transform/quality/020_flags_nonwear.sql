-- Data quality: flagged values per table (never deleted, SPEC 4.5) and wear signals.
DELETE FROM dq_flag_summary;
INSERT INTO dq_flag_summary
WITH f AS (
    SELECT source_key, 'fact_measurement' AS t, metric_key AS m, quality_flag FROM fact_measurement
    UNION ALL SELECT source_key, 'fact_heart_rate_sample', 'heart_rate', quality_flag
              FROM fact_heart_rate_sample
    UNION ALL SELECT source_key, 'fact_stress_sample', 'stress', quality_flag FROM fact_stress_sample
    UNION ALL SELECT source_key, 'fact_daily_summary', 'daily_summary', quality_flag
              FROM fact_daily_summary
    UNION ALL SELECT source_key, 'fact_sleep_session', 'sleep_session', quality_flag
              FROM fact_sleep_session
    UNION ALL SELECT source_key, 'fact_sleep_stage_interval', stage_key, quality_flag
              FROM fact_sleep_stage_interval
    UNION ALL SELECT source_key, 'fact_activity', 'activity', quality_flag FROM fact_activity
    UNION ALL SELECT source_key, 'fact_hrv_nightly', 'hrv_' || lower(hrv_method), quality_flag
              FROM fact_hrv_nightly
    UNION ALL SELECT source_key, 'fact_body_measure', 'body', quality_flag FROM fact_body_measure
    UNION ALL SELECT source_key, 'fact_vo2max', 'vo2max', quality_flag FROM fact_vo2max
), g AS (
    SELECT source_key, t, m, quality_flag, count(*) AS n,
           sum(count(*)) OVER (PARTITION BY source_key, t, m) AS total
    FROM f GROUP BY source_key, t, m, quality_flag
)
SELECT coalesce(s.person_key, 'unassigned'), g.source_key, t, m, quality_flag, n, total
FROM g LEFT JOIN dim_source s USING (source_key)
WHERE quality_flag <> 'ok';

DELETE FROM dq_nonwear;
-- long pauses between heart-rate samples – only for sources with continuous
-- (all-day) monitoring, i.e. a median of >= 24 non-workout samples per day;
-- workout-only recordings (FIT activity files) would otherwise look "not worn"
INSERT INTO dq_nonwear
WITH cont AS (
    SELECT source_key FROM (
        SELECT source_key, date_key, count(*) AS n FROM fact_heart_rate_sample
        WHERE context <> 'activity' GROUP BY 1, 2)
    GROUP BY source_key HAVING median(n) >= 24
), h AS (
    SELECT source_key, ts_utc, ts_local, tz_offset_min,
           lag(ts_utc) OVER (PARTITION BY source_key ORDER BY ts_utc) AS prev_utc,
           lag(ts_local) OVER (PARTITION BY source_key ORDER BY ts_utc) AS prev_local
    FROM fact_heart_rate_sample
    WHERE context <> 'activity' AND source_key IN (SELECT source_key FROM cont)
)
SELECT coalesce(s.person_key, 'unassigned'), h.source_key, 'hr_gap', prev_utc, ts_utc,
       prev_local, ts_local, date_diff('second', prev_utc, ts_utc) / 60.0, 0
FROM h LEFT JOIN dim_source s USING (source_key)
WHERE prev_utc IS NOT NULL
  AND date_diff('second', prev_utc, ts_utc) / 3600.0 >= getvariable('nonwear_gap_hours');

-- runs of identical heart-rate values (stuck sensor / not worn)
INSERT INTO dq_nonwear
WITH h AS (
    SELECT source_key, ts_utc, ts_local, bpm,
           CASE WHEN bpm IS DISTINCT FROM lag(bpm) OVER (PARTITION BY source_key ORDER BY ts_utc)
                THEN 1 ELSE 0 END AS chg
    FROM fact_heart_rate_sample
), r AS (
    SELECT h.*, sum(chg) OVER (PARTITION BY source_key ORDER BY ts_utc ROWS UNBOUNDED PRECEDING)
               AS run
    FROM h
), g AS (
    SELECT source_key, run, min(ts_utc) AS s, max(ts_utc) AS e, min(ts_local) AS sl,
           max(ts_local) AS el, count(*) AS n
    FROM r GROUP BY source_key, run
    HAVING count(*) >= getvariable('constant_run_samples')
)
SELECT coalesce(s2.person_key, 'unassigned'), g.source_key, 'hr_constant', s, e, sl, el,
       date_diff('second', s, e) / 60.0, n
FROM g LEFT JOIN dim_source s2 USING (source_key);

-- Garmin: consecutive "off wrist" stress samples (value -1)
INSERT INTO dq_nonwear
WITH st AS (
    SELECT source_key, ts_utc, ts_local, stress = -1 AS off,
           CASE WHEN (stress = -1) IS DISTINCT FROM lag(stress = -1) OVER
                     (PARTITION BY source_key ORDER BY ts_utc) THEN 1 ELSE 0 END AS chg
    FROM fact_stress_sample
), r AS (
    SELECT st.*, sum(chg) OVER (PARTITION BY source_key ORDER BY ts_utc ROWS UNBOUNDED PRECEDING)
               AS run
    FROM st
), g AS (
    SELECT source_key, run, min(ts_utc) AS s, max(ts_utc) AS e, min(ts_local) AS sl,
           max(ts_local) AS el, count(*) AS n
    FROM r WHERE off GROUP BY source_key, run
)
SELECT coalesce(s2.person_key, 'unassigned'), g.source_key, 'offwrist', s, e, sl, el,
       date_diff('second', s, e) / 60.0, n
FROM g LEFT JOIN dim_source s2 USING (source_key);
