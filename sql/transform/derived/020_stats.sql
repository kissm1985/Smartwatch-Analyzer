-- Derived layer 2: moving windows and the personal baseline (SPEC 5.4).
-- * ma/med N: calendar window of the last N days incl. the current one
--   (equivalent to pandas rolling('7D') / rolling('28D') on a date index)
-- * baseline: the PRECEDING derived.baseline_window_days days, current day excluded
--   (pandas rolling('60D', closed='left')); requires derived.baseline_min_days values
-- * std is the sample standard deviation (ddof = 1); MAD is unscaled
--   (scipy.stats.median_abs_deviation, scale=1); robust z uses 1.4826 × MAD
DELETE FROM derived_daily_stats;

INSERT INTO derived_daily_stats
WITH w AS (
    SELECT person_key, metric_key, date, date_key, value,
           count(value) OVER w7 AS n7, avg(value) OVER w7 AS ma7, median(value) OVER w7 AS med7,
           count(value) OVER w28 AS n28, avg(value) OVER w28 AS ma28,
           median(value) OVER w28 AS med28
    FROM derived_person_daily
    WINDOW w7 AS (PARTITION BY person_key, metric_key ORDER BY date
                  RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW),
           w28 AS (PARTITION BY person_key, metric_key ORDER BY date
                   RANGE BETWEEN INTERVAL 27 DAYS PRECEDING AND CURRENT ROW)
), bl AS (
    SELECT a.person_key, a.metric_key, a.date, list(b.value) AS vals
    FROM derived_person_daily a
    JOIN derived_person_daily b
      ON b.person_key = a.person_key AND b.metric_key = a.metric_key
     AND b.date < a.date
     AND b.date >= a.date - CAST(getvariable('bl_window_days') AS INTEGER)
    GROUP BY a.person_key, a.metric_key, a.date
), bl2 AS (
    SELECT person_key, metric_key, date, vals, len(vals) AS n,
           list_aggregate(vals, 'avg') AS mean,
           list_aggregate(vals, 'stddev_samp') AS sd,
           list_aggregate(vals, 'median') AS med
    FROM bl
), bl3 AS (
    SELECT person_key, metric_key, date, n,
           CASE WHEN n >= getvariable('bl_min_days') THEN mean END AS mean,
           CASE WHEN n >= getvariable('bl_min_days') THEN sd END AS sd,
           CASE WHEN n >= getvariable('bl_min_days') THEN med END AS med,
           CASE WHEN n >= getvariable('bl_min_days')
                THEN list_aggregate(list_transform(vals, lambda x: abs(x - med)), 'median')
           END AS mad
    FROM bl2
)
SELECT w.person_key, w.metric_key, w.date, w.date_key, w.value,
       w.n7, w.ma7, w.med7, w.n28, w.ma28, w.med28, w.ma7 - w.ma28,
       coalesce(b.n, 0), b.mean, b.sd, b.med, b.mad,
       CASE WHEN b.sd > 0 THEN (w.value - b.mean) / b.sd END AS z,
       CASE WHEN b.mad > 0 THEN (w.value - b.med) / (1.4826 * b.mad) END,
       CASE WHEN b.sd > 0 THEN abs((w.value - b.mean) / b.sd) >= getvariable('band_z') END
FROM w LEFT JOIN bl3 b USING (person_key, metric_key, date);
