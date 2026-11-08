-- Garmin: source resolution, FIT message set, dim_source, dim_activity_type.
-- source_key = 'garmin:<deviceId>' (Garmin Connect deviceId = the watch's unit id =
-- FIT file_id.serial_number). Daily/account-level records without a device id are
-- attributed to the account's main device (_g_account_device), else 'garmin:account-<pk>'.

CREATE OR REPLACE TEMP MACRO g_source(dev, pk) AS
    'garmin:' || coalesce(dev,
                          (SELECT a.device_id FROM _g_account_device a WHERE a.user_pk = pk),
                          'account-' || pk, 'unknown');

CREATE OR REPLACE TEMP TABLE _g_fit AS
SELECT m.*, f.import_batch_id, f.file_type,
       'garmin:' || coalesce(f.serial_number, 'unknown') AS source_key
FROM stg_garmin_fit_message m JOIN stg_garmin_fit_file f USING (file_hash);

CREATE OR REPLACE TEMP TABLE _g_fit_session AS
SELECT file_hash, msg_index, source_key, import_batch_id,
       TRY_CAST(json_extract_string(fields, '$.start_time') AS TIMESTAMP) AS start_utc,
       TRY_CAST(json_extract_string(fields, '$.total_elapsed_time') AS DOUBLE) AS elapsed_s,
       TRY_CAST(json_extract_string(fields, '$.total_timer_time') AS DOUBLE) AS timer_s,
       TRY_CAST(json_extract_string(fields, '$.total_distance') AS DOUBLE) AS distance_m,
       TRY_CAST(json_extract_string(fields, '$.total_calories') AS DOUBLE) AS kcal,
       TRY_CAST(json_extract_string(fields, '$.avg_heart_rate') AS DOUBLE) AS hr_avg,
       TRY_CAST(json_extract_string(fields, '$.max_heart_rate') AS DOUBLE) AS hr_max,
       TRY_CAST(json_extract_string(fields, '$.total_ascent') AS DOUBLE) AS ascent_m,
       TRY_CAST(json_extract_string(fields, '$.total_training_effect') AS DOUBLE) AS aerobic_te,
       TRY_CAST(json_extract_string(fields, '$.total_anaerobic_training_effect') AS DOUBLE)
           AS anaerobic_te,
       lower(CASE WHEN coalesce(json_extract_string(fields, '$.sub_sport'), 'generic')
                       NOT IN ('generic', 'all')
                  THEN json_extract_string(fields, '$.sub_sport')
                  ELSE json_extract_string(fields, '$.sport') END) AS type_key
FROM _g_fit WHERE message = 'session';

DELETE FROM dim_source WHERE vendor = 'Garmin';
INSERT INTO dim_source
WITH s AS (
    SELECT g_source(NULL, user_pk) AS sk, import_batch_id AS bid, calendar_date AS d FROM _g_uds
    UNION ALL SELECT g_source(device_id, user_pk), import_batch_id, calendar_date FROM _g_sleep
    UNION ALL SELECT g_source(device_id, user_pk), import_batch_id, CAST(start_utc AS DATE)
              FROM _g_activities
    UNION ALL SELECT g_source(device_id, user_pk), import_batch_id, calendar_date FROM _g_vo2max
    UNION ALL SELECT g_source(NULL, user_pk), import_batch_id,
                     CAST(coalesce(weight_utc, record_local) AS DATE) FROM _g_biometrics
    UNION ALL SELECT g_source(NULL, user_pk), import_batch_id, calendar_date FROM _g_generic
    UNION ALL SELECT g_source(NULL, user_pk), import_batch_id, NULL FROM _g_heart_rate_zones
    UNION ALL SELECT source_key, import_batch_id, CAST(ts_utc AS DATE) FROM _g_fit
), info AS (
    SELECT 'garmin:' || serial_number AS sk, arg_max(product, time_created_utc) AS product
    FROM stg_garmin_fit_file GROUP BY 1
), p AS (
    SELECT x.sk, min(b.person_key) AS person_key, count(DISTINCT b.person_key) > 1 AS conflict
    FROM (SELECT DISTINCT sk, bid FROM s) x
    JOIN dim_import_batch b ON b.import_batch_id = x.bid
    GROUP BY x.sk
)
SELECT s.sk, 'Garmin',
       CASE WHEN s.sk LIKE 'garmin:account-%' THEN 'Garmin Connect fiók'
            ELSE coalesce(any_value(i.product), 'Garmin ' || replace(s.sk, 'garmin:', '')) END,
       'watch', any_value(i.product), NULL, NULL,
       min(s.d), max(s.d), any_value(p.person_key), coalesce(any_value(p.conflict), FALSE)
FROM s LEFT JOIN info i ON i.sk = s.sk LEFT JOIN p ON p.sk = s.sk
GROUP BY s.sk;

DELETE FROM dim_activity_type WHERE vendor = 'Garmin';
INSERT INTO dim_activity_type
SELECT DISTINCT 'garmin:' || x.t, 'Garmin', x.t, coalesce(m.unified_type, 'other'),
       unified_name_hu(coalesce(m.unified_type, 'other')), m.category
FROM (SELECT lower(activity_type) AS t FROM _g_activities WHERE activity_type IS NOT NULL
      UNION SELECT type_key FROM _g_fit_session WHERE type_key IS NOT NULL) x
LEFT JOIN _g_activity_type_map m ON m.type_key = x.t;
