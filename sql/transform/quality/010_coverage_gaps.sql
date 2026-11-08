-- Data quality (SPEC 4.5): coverage per person × source × metric, gaps, source switches.
DELETE FROM dq_coverage;
INSERT INTO dq_coverage
SELECT person_key, source_key, metric_key, min(date), max(date), count(DISTINCT date),
       date_diff('day', min(date), max(date)) + 1,
       round(100.0 * count(DISTINCT date) / (date_diff('day', min(date), max(date)) + 1), 1)
FROM _cand_p
GROUP BY person_key, source_key, metric_key;

-- gaps only for metrics that are normally daily (coverage >= 50 %); weekly
-- measurements (weight, VO2max) would otherwise produce meaningless "gaps"
DELETE FROM dq_gap;
INSERT INTO dq_gap
WITH d AS (
    SELECT DISTINCT c.person_key, c.source_key, c.metric_key, c.date
    FROM _cand_p c
    JOIN dq_coverage v USING (person_key, source_key, metric_key)
    WHERE v.coverage_pct >= 50
), l AS (
    SELECT d.*, lead(date) OVER (PARTITION BY person_key, source_key, metric_key ORDER BY date)
               AS next_date
    FROM d
)
SELECT person_key, source_key, metric_key, date + 1, next_date - 1,
       date_diff('day', date, next_date) - 1
FROM l
WHERE next_date IS NOT NULL
  AND date_diff('day', date, next_date) - 1 >= getvariable('gap_min_days');

-- primary-source switches that persist (islands of the chosen source)
DELETE FROM dq_source_switch;
INSERT INTO dq_source_switch
WITH r AS (
    SELECT person_key, metric_key, date, source_key,
           CASE WHEN source_key IS DISTINCT FROM lag(source_key) OVER
                     (PARTITION BY person_key, metric_key ORDER BY date) THEN 1 ELSE 0 END AS chg
    FROM derived_person_daily
), i AS (
    SELECT r.*, sum(chg) OVER (PARTITION BY person_key, metric_key ORDER BY date
                               ROWS UNBOUNDED PRECEDING) AS run
    FROM r
), runs AS (
    SELECT person_key, metric_key, run, any_value(source_key) AS source_key,
           min(date) AS d0, count(*) AS n
    FROM i GROUP BY person_key, metric_key, run
    HAVING count(*) >= getvariable('switch_min_days')
), k AS (
    SELECT runs.*, lag(source_key) OVER (PARTITION BY person_key, metric_key ORDER BY d0)
               AS prev_source
    FROM runs
)
SELECT person_key, metric_key, d0, prev_source, source_key
FROM k WHERE prev_source IS NOT NULL AND prev_source <> source_key;
