-- Sleep regularity (std of bed/wake time over 7/14 days) and sleep debt
-- (Σ goal − sleep over the nights present in the window). SPEC 5.4.
DELETE FROM derived_sleep_regularity;

INSERT INTO derived_sleep_regularity
WITH p AS (
    PIVOT (SELECT person_key, date, metric_key, value FROM derived_person_daily
           WHERE metric_key IN ('bedtime_min', 'waketime_min', 'sleep_min'))
    ON metric_key IN ('bedtime_min', 'waketime_min', 'sleep_min') USING any_value(value)
), w AS (
    SELECT p.*,
           count(sleep_min) OVER w7 AS n7, count(sleep_min) OVER w14 AS n14,
           stddev_samp(bedtime_min) OVER w7 AS b7, stddev_samp(bedtime_min) OVER w14 AS b14,
           stddev_samp(waketime_min) OVER w7 AS k7, stddev_samp(waketime_min) OVER w14 AS k14,
           sum(getvariable('sleep_goal_min') - sleep_min) OVER w7 AS d7,
           sum(getvariable('sleep_goal_min') - sleep_min) OVER w14 AS d14
    FROM p
    WINDOW w7 AS (PARTITION BY person_key ORDER BY date
                  RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW),
           w14 AS (PARTITION BY person_key ORDER BY date
                   RANGE BETWEEN INTERVAL 13 DAYS PRECEDING AND CURRENT ROW)
)
SELECT person_key, date, yyyymmdd(date), bedtime_min, waketime_min, sleep_min, n7,
       CASE WHEN n7 >= getvariable('reg_min_nights') THEN b7 END,
       CASE WHEN n14 >= getvariable('reg_min_nights') THEN b14 END,
       CASE WHEN n7 >= getvariable('reg_min_nights') THEN k7 END,
       CASE WHEN n14 >= getvariable('reg_min_nights') THEN k14 END,
       d7, d14
FROM w;
