-- Apple sleep: stage intervals -> sessions (gaps-and-islands per source).
-- Durations are computed on UTC instants, so DST nights are 7h/9h correctly.
-- sleep_date: 'wake' = local date of the session end (default, SPEC 4.4);
--             'start' = local date of (session start - 12h), i.e. "night of".
--
-- Real-world robustness (seen in a real export):
-- * third-party sleep apps write chained, OVERLAPPING segments of the same stage
--   -> minutes are computed on the UNION of intervals (never a plain sum); the
--      session stays 'ok' (corrected), the intervals carry quality_flag 'overlap';
-- * some apps write implausibly long single intervals (e.g. a 20 h "in bed")
--   -> intervals longer than sleep.max_interval_hours are kept (flagged) but
--      excluded from session building.
DELETE FROM fact_sleep_stage_interval WHERE source_key LIKE 'apple:%';
DELETE FROM fact_sleep_session WHERE source_key LIKE 'apple:%';

CREATE OR REPLACE TEMP TABLE _apple_sleep_iv AS
SELECT r.source_key,
       coalesce(sv.stage_key, 'unspecified') AS stage_key,
       r.start_utc, r.end_utc, r.start_local, r.end_local, r.start_off, r.end_off,
       date_diff('second', r.start_utc, r.end_utc) / 60.0 AS duration_min,
       r.import_batch_id,
       CASE WHEN date_diff('second', r.start_utc, r.end_utc) / 60.0
                 > getvariable('sleep_max_interval_min') THEN 'implausible_duration'
            WHEN sv.stage_key IS NULL THEN 'unknown_stage'
            ELSE 'ok' END AS quality_flag
FROM _apple_rec r
LEFT JOIN map_sleep_value sv ON sv.vendor = 'Apple' AND sv.value_text = r.value_text
WHERE r.target = 'sleep' AND r.end_utc IS NOT NULL AND r.end_utc > r.start_utc;

CREATE OR REPLACE TEMP TABLE _apple_sleep AS
WITH iv AS (
    SELECT * FROM _apple_sleep_iv WHERE quality_flag <> 'implausible_duration'
), g AS (
    SELECT iv.*,
           max(end_utc) OVER (PARTITION BY source_key ORDER BY start_utc, end_utc
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_end
    FROM iv
), f AS (
    SELECT g.*,
           CASE WHEN prev_end IS NULL
                  OR start_utc > prev_end + to_minutes(getvariable('sleep_gap_min'))
                THEN 1 ELSE 0 END AS new_session,
           -- informational: this interval starts before an earlier one ended
           (prev_end IS NOT NULL AND start_utc < prev_end) AS overlaps_prev
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
       start_off, duration_min,
       CASE WHEN quality_flag = 'ok' AND overlaps_prev THEN 'overlap' ELSE quality_flag END,
       import_batch_id
FROM _apple_sleep
UNION ALL
SELECT NULL, source_key, stage_key, start_utc, end_utc, start_local, end_local, start_off,
       duration_min, quality_flag, import_batch_id
FROM _apple_sleep_iv WHERE quality_flag = 'implausible_duration';

-- union (merged) islands per session and group: each stage, all asleep stages, all
CREATE OR REPLACE TEMP TABLE _apple_sleep_islands AS
WITH x AS (
    SELECT session_id, stage_key AS grp, start_utc, end_utc FROM _apple_sleep
    UNION ALL
    SELECT session_id, 'asleep', start_utc, end_utc FROM _apple_sleep
    WHERE stage_key IN ('light', 'deep', 'rem', 'unspecified')
    UNION ALL
    SELECT session_id, 'sleep_or_awake', start_utc, end_utc FROM _apple_sleep
    WHERE stage_key <> 'in_bed'
), g AS (
    SELECT x.*,
           max(end_utc) OVER (PARTITION BY session_id, grp ORDER BY start_utc, end_utc
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS pe
    FROM x
), f AS (
    SELECT g.*,
           sum(CASE WHEN pe IS NULL OR start_utc > pe THEN 1 ELSE 0 END)
               OVER (PARTITION BY session_id, grp ORDER BY start_utc, end_utc
                     ROWS UNBOUNDED PRECEDING) AS isl
    FROM g
)
SELECT session_id, grp, isl, min(start_utc) AS s, max(end_utc) AS e
FROM f GROUP BY session_id, grp, isl;

-- "awake" wins where an app's awake segment overlaps asleep segments
-- (seen in real data: boundary overlaps of a few minutes)
CREATE OR REPLACE TEMP TABLE _apple_sleep_union AS
WITH tot AS (
    SELECT session_id, grp, sum(date_diff('second', s, e)) / 60.0 AS mins
    FROM _apple_sleep_islands GROUP BY session_id, grp
), aw AS (
    SELECT a.session_id, a.grp,
           sum(date_diff('second', greatest(a.s, w.s), least(a.e, w.e))) / 60.0 AS mins
    FROM _apple_sleep_islands a
    JOIN _apple_sleep_islands w
      ON w.session_id = a.session_id AND w.grp = 'awake' AND w.s < a.e AND a.s < w.e
    WHERE a.grp IN ('asleep', 'deep', 'light', 'rem', 'unspecified')
    GROUP BY a.session_id, a.grp
), net AS (
    SELECT t.session_id, t.grp, t.mins - coalesce(aw.mins, 0) AS mins
    FROM tot t LEFT JOIN aw USING (session_id, grp)
)
SELECT session_id,
       max(mins) FILTER (WHERE grp = 'in_bed') AS in_bed,
       max(mins) FILTER (WHERE grp = 'asleep') AS asleep,
       max(mins) FILTER (WHERE grp = 'sleep_or_awake') AS sleep_or_awake,
       max(mins) FILTER (WHERE grp = 'deep') AS deep,
       max(mins) FILTER (WHERE grp = 'light') AS light,
       max(mins) FILTER (WHERE grp = 'rem') AS rem,
       max(mins) FILTER (WHERE grp = 'awake') AS awake,
       max(mins) FILTER (WHERE grp = 'unspecified') AS unspec
FROM net GROUP BY session_id;

INSERT INTO fact_sleep_session
WITH agg AS (
    SELECT session_id, source_key,
           min(start_utc) AS s_utc, max(end_utc) AS e_utc,
           arg_min(start_local, start_utc) AS s_loc, arg_max(end_local, end_utc) AS e_loc,
           arg_min(start_off, start_utc) AS s_off, arg_max(end_off, end_utc) AS e_off,
           bool_or(stage_key IN ('light', 'deep', 'rem')) AS has_stages,
           max(import_batch_id) AS bid
    FROM _apple_sleep
    GROUP BY session_id, source_key
), x AS (
    SELECT agg.*, u.in_bed AS in_bed_sum, u.asleep, u.sleep_or_awake, u.deep, u.light, u.rem,
           u.awake, u.unspec,
           date_diff('second', s_utc, e_utc) / 60.0 AS span_min,
           CASE WHEN getvariable('sleep_day_convention') = 'start'
                THEN CAST(s_loc - INTERVAL 12 HOUR AS DATE)
                ELSE CAST(e_loc AS DATE) END AS sleep_date
    FROM agg JOIN _apple_sleep_union u USING (session_id)
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
            THEN least(100.0, 100.0 * asleep / greatest(coalesce(in_bed_sum, span_min), asleep))
       END,
       NULL,                       -- sleep_score: not provided by Apple
       NULL, NULL, NULL, NULL,     -- hr_avg, hr_min, respiratory_rate_avg, spo2_avg (filled later)
       NULL,                       -- stress_avg: Garmin-only (SPEC 5.3/2)
       coalesce(has_stages, FALSE),
       CASE WHEN asleep IS NULL THEN 'in_bed_only'
            WHEN span_min > 20 * 60 THEN 'implausible_duration'
            -- different stages claiming the same minutes (after per-stage union)
            WHEN coalesce(deep, 0) + coalesce(light, 0) + coalesce(rem, 0) + coalesce(unspec, 0)
                 + coalesce(awake, 0) > sleep_or_awake + 1 THEN 'overlapping_stages'
            ELSE 'ok' END,
       bid
FROM x;
