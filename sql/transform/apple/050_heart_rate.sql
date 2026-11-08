-- Apple heart-rate samples (source resolution) + context.
DELETE FROM fact_heart_rate_sample WHERE source_key LIKE 'apple:%';

INSERT INTO fact_heart_rate_sample
SELECT source_key, start_utc, start_local, start_off, date_key, minute_of_day, value,
       CASE WHEN json_extract_string(metadata, '$.HKMetadataKeyHeartRateMotionContext') = '2'
            THEN 'activity' ELSE 'rest' END,
       quality_flag, import_batch_id
FROM _apple_rec
WHERE target = 'heart_rate_sample';

-- samples inside an (actually slept) Apple sleep session -> 'sleep'
UPDATE fact_heart_rate_sample h SET context = 'sleep'
FROM fact_sleep_session s
WHERE h.source_key LIKE 'apple:%' AND s.source_key LIKE 'apple:%'
  AND s.asleep_min > 0
  AND h.ts_utc >= s.start_utc AND h.ts_utc <= s.end_utc;

-- per-session HR / respiration / SpO2 (any Apple source in the session window)
UPDATE fact_sleep_session s SET hr_avg = x.hr_avg, hr_min = x.hr_min
FROM (
    SELECT s2.session_id, avg(h.bpm) AS hr_avg, min(h.bpm) AS hr_min
    FROM fact_sleep_session s2
    JOIN fact_heart_rate_sample h
      ON h.source_key LIKE 'apple:%' AND h.ts_utc >= s2.start_utc AND h.ts_utc <= s2.end_utc
    WHERE s2.source_key LIKE 'apple:%' AND h.quality_flag = 'ok'
    GROUP BY s2.session_id
) x
WHERE s.session_id = x.session_id;

UPDATE fact_sleep_session s SET respiratory_rate_avg = x.rr, spo2_avg = x.spo2
FROM (
    SELECT s2.session_id,
           avg(m.value) FILTER (WHERE m.metric_key = 'respiratory_rate') AS rr,
           avg(m.value) FILTER (WHERE m.metric_key = 'spo2') AS spo2
    FROM fact_sleep_session s2
    JOIN fact_measurement m
      ON m.source_key LIKE 'apple:%' AND m.ts_utc >= s2.start_utc AND m.ts_utc <= s2.end_utc
    WHERE s2.source_key LIKE 'apple:%' AND m.quality_flag = 'ok'
      AND m.metric_key IN ('respiratory_rate', 'spo2')
    GROUP BY s2.session_id
) x
WHERE s.session_id = x.session_id;
