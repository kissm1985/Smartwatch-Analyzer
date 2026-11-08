-- Apple sleep: stage intervals -> sessions (gaps-and-islands per source).
-- Durations are computed on UTC instants, so DST nights are 7h/9h correctly.
-- sleep_date: 'wake' = local date of the session end (default, SPEC 4.4);
--             'start' = local date of (session start - 12h), i.e. "night of".
DELETE FROM fact_sleep_stage_interval WHERE source_key LIKE 'apple:%';
DELETE FROM fact_sleep_session WHERE source_key LIKE 'apple:%';

CREATE OR REPLACE TEMP TABLE _apple_sleep AS
WITH iv AS (
    SELECT r.source_key,
           coalesce(sv.stage_key, 'unspecified') AS stage_key,
           r.start_utc, r.end_utc, r.start_local, r.end_local, r.start_off, r.end_off,
           date_diff('second', r.start_utc, r.end_utc) / 60.0 AS duration_min,
           r.import_batch_id,
           CASE WHEN sv.stage_key IS NULL THEN 'unknown_stage' ELSE 'ok' END AS quality_flag
    FROM _apple_rec r
    LEFT JOIN map_sleep_value sv ON sv.vendor = 'Apple' AND sv.value_text = r.value_text
    WHERE r.target = 'sleep' AND r.end_utc IS NOT NULL AND r.end_utc > r.start_utc
), g AS (
    SELECT iv.*,
           max(end_utc) OVER (PARTITION BY source_key ORDER BY start_utc, end_utc
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_end
    FROM iv
), f AS (
    SELECT g.*,
           CASE WHEN prev_end IS NULL
                  OR start_utc > prev_end + to_minutes(getvariable('sleep_gap_min'))
                THEN 1 ELSE 0 END AS new_session
    FROM g
), n AS (
    SELECT f.*,
           sum(new_session) OVER (PARTITION BY source_key ORDER BY start_utc, end_utc
                                  ROWS UNBOUNDED PRECEDING) AS session_seq
    FROM f
)
SELECT n.*,
       md5(source_key || '|' || CAST(min(start_utc) OVER (PARTITION BY source_key, session_seq)
                                     AS VARCHAR)) AS session_id
FROM n;

INSERT INTO fact_sleep_stage_interval
SELECT session_id, source_key, stage_key, start_utc, end_utc, start_local, end_local,
       start_off, duration_min, quality_flag, import_batch_id
FROM _apple_sleep;

INSERT INTO fact_sleep_session
WITH agg AS (
    SELECT session_id, source_key,
           min(start_utc) AS s_utc, max(end_utc) AS e_utc,
           arg_min(start_local, start_utc) AS s_loc, arg_max(end_local, end_utc) AS e_loc,
           arg_min(start_off, start_utc) AS s_off, arg_max(end_off, end_utc) AS e_off,
           sum(duration_min) FILTER (WHERE stage_key = 'in_bed') AS in_bed_sum,
           sum(duration_min) FILTER (WHERE stage_key IN ('light', 'deep', 'rem', 'unspecified'))
               AS asleep,
           sum(duration_min) FILTER (WHERE stage_key = 'deep') AS deep,
           sum(duration_min) FILTER (WHERE stage_key = 'light') AS light,
           sum(duration_min) FILTER (WHERE stage_key = 'rem') AS rem,
           sum(duration_min) FILTER (WHERE stage_key = 'awake') AS awake,
           sum(duration_min) FILTER (WHERE stage_key = 'unspecified') AS unspec,
           bool_or(stage_key IN ('light', 'deep', 'rem')) AS has_stages,
           max(import_batch_id) AS bid
    FROM _apple_sleep
    GROUP BY session_id, source_key
), x AS (
    SELECT agg.*,
           date_diff('second', s_utc, e_utc) / 60.0 AS span_min,
           CASE WHEN getvariable('sleep_day_convention') = 'start'
                THEN CAST(s_loc - INTERVAL 12 HOUR AS DATE)
                ELSE CAST(e_loc AS DATE) END AS sleep_date
    FROM agg
)
SELECT session_id, source_key,
       CAST(strftime(sleep_date, '%Y%m%d') AS INTEGER), sleep_date,
       row_number() OVER (PARTITION BY source_key, sleep_date
                          ORDER BY coalesce(asleep, 0) DESC, span_min DESC, s_utc) = 1,
       s_utc, e_utc, s_loc, e_loc, s_off, e_off,
       coalesce(in_bed_sum, span_min) AS in_bed_min,
       asleep,
       CASE WHEN has_stages THEN coalesce(deep, 0) END,
       CASE WHEN has_stages THEN coalesce(light, 0) END,
       CASE WHEN has_stages THEN coalesce(rem, 0) END,
       CASE WHEN has_stages OR awake IS NOT NULL THEN coalesce(awake, 0) END,
       CASE WHEN asleep IS NOT NULL THEN coalesce(unspec, 0) END,
       CASE WHEN asleep > 0 AND coalesce(in_bed_sum, span_min) > 0
            THEN least(100.0, 100.0 * asleep / coalesce(in_bed_sum, span_min)) END,
       NULL,                       -- sleep_score: not provided by Apple
       NULL, NULL, NULL, NULL,     -- hr_avg, hr_min, respiratory_rate_avg, spo2_avg (filled later)
       NULL,                       -- stress_avg: Garmin-only (SPEC 5.3/2)
       coalesce(has_stages, FALSE),
       CASE WHEN asleep IS NULL THEN 'in_bed_only'
            WHEN span_min > 20 * 60 THEN 'implausible_duration'
            -- stage intervals of one source must not overlap; if they do the
            -- per-stage sums exceed the session span and are unreliable
            WHEN asleep + coalesce(awake, 0) > span_min + 1 THEN 'overlapping_intervals'
            ELSE 'ok' END,
       bid
FROM x;
