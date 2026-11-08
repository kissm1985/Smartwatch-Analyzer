-- Body composition: one row per (source, measurement instant). BMI is taken from
-- the record when present, otherwise computed from the latest known height.
DELETE FROM fact_body_measure WHERE source_key LIKE 'apple:%';

INSERT INTO fact_body_measure
WITH b AS (
    SELECT source_key, ts_utc,
           any_value(ts_local) AS ts_local, any_value(tz_offset_min) AS off,
           any_value(date_key) AS dk,
           avg(value) FILTER (WHERE metric_key = 'body_mass') AS mass,
           avg(value) FILTER (WHERE metric_key = 'body_fat_pct') AS fat,
           avg(value) FILTER (WHERE metric_key = 'bmi') AS bmi,
           avg(value) FILTER (WHERE metric_key = 'lean_body_mass') AS lean,
           max(import_batch_id) AS bid,
           bool_or(quality_flag <> 'ok') AS bad
    FROM fact_measurement
    WHERE source_key LIKE 'apple:%'
      AND metric_key IN ('body_mass', 'body_fat_pct', 'bmi', 'lean_body_mass')
    GROUP BY source_key, ts_utc
), h AS (
    SELECT ts_utc AS h_ts, value AS height_m
    FROM fact_measurement WHERE metric_key = 'height' AND quality_flag = 'ok'
), first_h AS (
    SELECT arg_min(height_m, h_ts) AS h0 FROM h
), bh AS (
    SELECT b.*, coalesce(h.height_m, (SELECT h0 FROM first_h)) AS height_m
    FROM b ASOF LEFT JOIN h ON b.ts_utc >= h.h_ts
)
SELECT source_key, ts_utc, ts_local, off, dk, mass, fat,
       coalesce(bmi, CASE WHEN mass IS NOT NULL AND height_m > 0
                          THEN mass / (height_m * height_m) END),
       bmi IS NULL AND mass IS NOT NULL AND height_m > 0,
       lean, NULL, NULL,
       CASE WHEN bad THEN 'out_of_range' ELSE 'ok' END,
       bid
FROM bh;

DELETE FROM fact_vo2max WHERE source_key LIKE 'apple:%';
INSERT INTO fact_vo2max
SELECT source_key, date_key, avg(value), count(*), 'ok', max(import_batch_id)
FROM fact_measurement
WHERE metric_key = 'vo2max' AND quality_flag = 'ok' AND source_key LIKE 'apple:%'
GROUP BY source_key, date_key;
