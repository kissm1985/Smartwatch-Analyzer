-- Apple: deduplicated, typed record set (temp) used by later steps.
-- Dedup: one row per natural key, the newest import batch wins (SPEC 4.3).
-- Local time: configured zone via tz_offset (ASOF JOIN), independent of the
-- offset Apple wrote into the file.

CREATE OR REPLACE TEMP TABLE _apple_rec AS
WITH d AS (
    SELECT * FROM stg_apple_record
    WHERE start_utc IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY nk_hash ORDER BY import_batch_id DESC, row_hash) = 1
), s AS (
    SELECT d.*, o.offset_min AS start_off
    FROM d ASOF LEFT JOIN tz_offset o ON d.start_utc >= o.valid_from_utc
), e AS (
    SELECT s.*, o.offset_min AS end_off
    FROM s ASOF LEFT JOIN tz_offset o ON coalesce(s.end_utc, s.start_utc) >= o.valid_from_utc
), m AS (
    SELECT e.*,
        apple_source_key(e.source_name, e.device) AS source_key,
        coalesce(mt.metric_key,
            'apple_' || lower(regexp_replace(
                regexp_replace(e.record_type,
                    '^(HKQuantityTypeIdentifier|HKCategoryTypeIdentifier|HKCorrelationTypeIdentifier|HKDataType)', ''),
                '([a-z0-9])([A-Z])', '\1_\2', 'g'))) AS metric_key,
        coalesce(mt.target, 'measurement') AS target,
        mt.scale_if_fraction,
        mt.hk_type IS NULL AS is_auto,
        e.start_utc + to_minutes(e.start_off) AS start_local,
        coalesce(e.end_utc, e.start_utc) + to_minutes(e.end_off) AS end_local
    FROM e LEFT JOIN map_apple_type mt ON mt.hk_type = e.record_type
), c AS (
    SELECT m.*,
        dm.unit AS target_unit,
        CASE
            WHEN m.value_num IS NULL THEN NULL
            WHEN dm.unit IS NULL OR m.unit IS NULL OR m.unit = dm.unit THEN m.value_num
            WHEN u.factor IS NOT NULL THEN m.value_num * u.factor
            ELSE m.value_num
        END AS value_conv,
        CASE
            WHEN dm.unit IS NULL THEN m.unit
            WHEN m.unit IS NULL OR m.unit = dm.unit OR u.factor IS NOT NULL THEN dm.unit
            ELSE m.unit
        END AS unit_out,
        dm.valid_min, dm.valid_max
    FROM m
    LEFT JOIN dim_metric dm ON dm.metric_key = m.metric_key
    LEFT JOIN map_unit u ON u.from_unit = m.unit AND u.to_unit = dm.unit
)
SELECT
    c.*,
    CASE WHEN c.scale_if_fraction IS NOT NULL AND c.value_conv <= 1
         THEN c.value_conv * c.scale_if_fraction ELSE c.value_conv END AS value,
    CAST(strftime(c.start_local, '%Y%m%d') AS INTEGER) AS date_key,
    CAST(hour(c.start_local) * 60 + minute(c.start_local) AS SMALLINT) AS minute_of_day
FROM c;

-- quality flag (SPEC 4.5): never delete, only mark
ALTER TABLE _apple_rec ADD COLUMN quality_flag VARCHAR;
UPDATE _apple_rec SET quality_flag = CASE
    WHEN target_unit IS NOT NULL AND unit_out IS DISTINCT FROM target_unit AND value IS NOT NULL
        THEN 'unit_unconverted'
    WHEN value IS NOT NULL AND (value < valid_min OR value > valid_max) THEN 'out_of_range'
    ELSE 'ok' END;

-- auto metrics for unknown record types (SPEC 4.1: never dropped)
INSERT INTO dim_metric
SELECT metric_key,
       any_value(regexp_replace(record_type, '^HK[A-Za-z]*TypeIdentifier', '')),
       any_value(regexp_replace(record_type, '^HK[A-Za-z]*TypeIdentifier', '')),
       'other', mode(unit_out), 'AVG', 'neutral', NULL, NULL,
       'Automatikusan felvett metrika ismeretlen Apple rekordtípusból.', TRUE,
       any_value(record_type)
FROM _apple_rec
WHERE is_auto AND metric_key NOT IN (SELECT metric_key FROM dim_metric)
GROUP BY metric_key;
