-- Training load per day, acute:chronic ratio (APPROXIMATION, SPEC 5.4) and weekly
-- activity volume (WHO: moderate + 2 × vigorous minutes).
-- Session load: device training load if present, else Edwards TRIMP from HR-zone
-- minutes (zone weight 1..5), else duration in minutes. Methods are recorded.
CREATE OR REPLACE TEMP TABLE _act AS
WITH a AS (
    SELECT a.*, s.person_key, s.prio, t.unified_type
    FROM fact_activity a
    JOIN _src s USING (source_key)
    LEFT JOIN dim_activity_type t USING (activity_type_key)
    WHERE a.quality_flag = 'ok'
), dedup AS (
    -- the same workout recorded by two sources of one person: keep the better source
    SELECT a.* FROM a
    WHERE NOT EXISTS (
        SELECT 1 FROM a b
        WHERE b.person_key = a.person_key AND b.activity_id <> a.activity_id
          AND b.start_utc < a.end_utc AND a.start_utc < b.end_utc
          AND (b.prio < a.prio OR (b.prio = a.prio AND b.source_key < a.source_key)))
), z AS (
    SELECT activity_id,
           sum(seconds_in_zone / 60.0 * least(greatest(zone, 0), 5)) AS trimp,
           sum(seconds_in_zone) AS zone_s
    FROM fact_activity_hr_zone GROUP BY activity_id
)
SELECT d.*, CAST(d.start_local AS DATE) AS date,
       CASE WHEN d.training_load IS NOT NULL THEN d.training_load
            WHEN z.zone_s > 0 THEN z.trimp
            ELSE coalesce(d.duration_min, 0) END AS load,
       CASE WHEN d.training_load IS NOT NULL THEN 'device_load'
            WHEN z.zone_s > 0 THEN 'edwards_trimp'
            ELSE 'duration' END AS load_method
FROM dedup d LEFT JOIN z USING (activity_id);

CREATE OR REPLACE TEMP TABLE _person_span AS
SELECT person_key, min(date) AS d0, max(date) AS d1 FROM derived_person_daily GROUP BY 1;

DELETE FROM derived_training_load;
INSERT INTO derived_training_load
WITH cal AS (
    SELECT person_key, CAST(unnest(generate_series(d0, d1, INTERVAL 1 DAY)) AS DATE) AS date
    FROM _person_span
), dl AS (
    SELECT person_key, date, sum(load) AS load,
           CASE WHEN count(DISTINCT load_method) > 1 THEN 'mixed' ELSE any_value(load_method) END
               AS method
    FROM _act GROUP BY person_key, date
), j AS (
    SELECT cal.person_key, cal.date, coalesce(dl.load, 0) AS load,
           coalesce(dl.method, 'none') AS method,
           row_number() OVER (PARTITION BY cal.person_key ORDER BY cal.date) AS day_no
    FROM cal LEFT JOIN dl USING (person_key, date)
), w AS (
    SELECT j.*,
           avg(load) OVER (PARTITION BY person_key ORDER BY date
                           ROWS BETWEEN getvariable('acute_days') - 1 PRECEDING AND CURRENT ROW)
               AS acute,
           avg(load) OVER (PARTITION BY person_key ORDER BY date
                           ROWS BETWEEN getvariable('chronic_days') - 1 PRECEDING AND CURRENT ROW)
               AS chronic
    FROM j
)
SELECT person_key, date, yyyymmdd(date), load, method, load = 0, acute, chronic,
       CASE WHEN chronic > 0 THEN acute / chronic END,
       day_no >= getvariable('chronic_days') AND chronic > 0
FROM w;

DELETE FROM derived_weekly_activity;
INSERT INTO derived_weekly_activity
WITH days AS (
    SELECT person_key, date, date_trunc('week', date)::DATE AS week_start,
           max(value) FILTER (WHERE metric_key = 'moderate_min') AS moderate,
           max(value) FILTER (WHERE metric_key = 'vigorous_min') AS vigorous,
           max(value) FILTER (WHERE metric_key = 'who_equiv_min') AS who,
           max(CASE WHEN metric_key = 'who_equiv_min' THEN
                    CASE WHEN source_key LIKE 'garmin:%' THEN 'intensity_minutes'
                         ELSE 'exercise_as_moderate' END END) AS who_method,
           max(value) FILTER (WHERE metric_key = 'steps') AS steps
    FROM derived_person_daily GROUP BY person_key, date
), acts AS (
    SELECT person_key, date_trunc('week', date)::DATE AS week_start,
           count(*) AS activity_count, count(DISTINCT date) AS active_days,
           count(DISTINCT date) FILTER (WHERE unified_type = 'strength') AS strength_days,
           sum(load) AS load
    FROM _act GROUP BY 1, 2
), wk AS (
    SELECT d.person_key, d.week_start, count(*) AS days_with_data,
           sum(d.moderate) AS moderate, sum(d.vigorous) AS vigorous, sum(d.who) AS who,
           CASE WHEN count(DISTINCT d.who_method) > 1 THEN 'mixed'
                ELSE any_value(d.who_method) END AS who_method,
           sum(d.steps) AS steps
    FROM days d GROUP BY 1, 2
), j AS (
    SELECT wk.*, coalesce(a.activity_count, 0) AS activity_count,
           coalesce(a.active_days, 0) AS active_days,
           coalesce(a.strength_days, 0) AS strength_days,
           wk.days_with_data - coalesce(a.active_days, 0) AS rest_days,
           coalesce(a.load, 0) AS load
    FROM wk LEFT JOIN acts a USING (person_key, week_start)
)
SELECT person_key, week_start, CAST(isoyear(week_start) AS SMALLINT),
       CAST(week(week_start) AS TINYINT), days_with_data, moderate, vigorous, who, who_method,
       steps, activity_count, active_days, strength_days, greatest(rest_days, 0),
       stddev_samp(greatest(rest_days, 0)) OVER (PARTITION BY person_key ORDER BY week_start
                                                 ROWS BETWEEN 3 PRECEDING AND CURRENT ROW),
       load
FROM j;
