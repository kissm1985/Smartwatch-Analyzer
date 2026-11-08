-- Apple Watch HRV: SDNN (all watchOS) and RMSSD (newer watchOS) – SPEC 5.3/1:
-- one row per method, never mixed or converted.
-- Nightly value = mean of the method's samples inside the night's main sleep session.
-- Each sample counts once even if several Apple sources recorded a session.
DELETE FROM fact_hrv_nightly WHERE source_key LIKE 'apple:%';

INSERT INTO fact_hrv_nightly
WITH m AS (
    SELECT DISTINCT ON (r.metric_key, r.source_key, r.ts_utc)
           r.metric_key, r.source_key, r.ts_utc, r.value, r.import_batch_id,
           s.session_id, s.sleep_date, s.sleep_date_key
    FROM fact_measurement r
    JOIN fact_sleep_session s
      ON s.source_key LIKE 'apple:%' AND s.is_main AND coalesce(s.asleep_min, 0) > 0
     AND r.ts_utc >= s.start_utc AND r.ts_utc <= s.end_utc
    WHERE r.metric_key IN ('hrv_sdnn', 'hrv_rmssd') AND r.quality_flag = 'ok'
      AND r.source_key LIKE 'apple:%'
    ORDER BY r.metric_key, r.source_key, r.ts_utc, (s.source_key = r.source_key) DESC,
             s.asleep_min DESC
), n AS (
    SELECT source_key, sleep_date_key, sleep_date,
           CASE metric_key WHEN 'hrv_sdnn' THEN 'SDNN' ELSE 'RMSSD' END AS method,
           any_value(session_id) AS session_id,
           avg(value) AS hrv, count(*) AS n, max(import_batch_id) AS bid
    FROM m
    GROUP BY source_key, sleep_date_key, sleep_date, metric_key
)
SELECT source_key, sleep_date_key, sleep_date, session_id, method, hrv,
       avg(hrv) OVER (PARTITION BY source_key, method ORDER BY sleep_date
                      RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW),
       NULL, NULL, NULL, n,
       CASE WHEN n < 2 THEN 'low_sample' ELSE 'ok' END,
       bid
FROM n;
