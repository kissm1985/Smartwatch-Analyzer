-- 001: initial star schema (staging, mapping, dimensions, facts)
-- Conventions: dim_*, fact_*, stg_*, map_* ; all fact tables carry
-- import_batch_id, source_key, quality_flag. Timestamps: naive UTC (*_utc),
-- naive local (*_local) + tz_offset_min. date_key = local date as YYYYMMDD.

CREATE SEQUENCE IF NOT EXISTS seq_import_batch START 1;

-- ---------------------------------------------------------------- lineage
CREATE TABLE dim_import_batch (
    import_batch_id   INTEGER PRIMARY KEY,
    started_at        TIMESTAMP NOT NULL,
    finished_at       TIMESTAMP,
    vendor            VARCHAR,
    adapter           VARCHAR NOT NULL,
    file_name         VARCHAR,
    file_path         VARCHAR,
    sha256            VARCHAR,
    status            VARCHAR NOT NULL,   -- running | success | failed | skipped
    records_read      BIGINT DEFAULT 0,
    records_new       BIGINT DEFAULT 0,
    records_updated   BIGINT DEFAULT 0,
    records_skipped   BIGINT DEFAULT 0,
    records_error     BIGINT DEFAULT 0,
    date_from         DATE,
    date_to           DATE,
    message           VARCHAR             -- technical only, never health values
);

-- ---------------------------------------------------------------- staging (Apple)
-- Raw attribute strings are preserved verbatim; *_utc / value_num are parsed
-- helper columns. row_hash = full content hash, nk_hash = natural key hash.
CREATE TABLE stg_apple_record (
    row_hash          VARCHAR PRIMARY KEY,
    nk_hash           VARCHAR NOT NULL,
    import_batch_id   INTEGER NOT NULL,
    record_type       VARCHAR NOT NULL,
    source_name       VARCHAR,
    source_version    VARCHAR,
    device            VARCHAR,
    unit              VARCHAR,
    value_text        VARCHAR,
    value_num         DOUBLE,
    creation_date_raw VARCHAR,
    start_date_raw    VARCHAR,
    end_date_raw      VARCHAR,
    start_utc         TIMESTAMP,
    end_utc           TIMESTAMP,
    metadata          JSON
);

CREATE TABLE stg_apple_workout (
    row_hash            VARCHAR PRIMARY KEY,
    nk_hash             VARCHAR NOT NULL,
    import_batch_id     INTEGER NOT NULL,
    activity_type       VARCHAR NOT NULL,
    duration            DOUBLE,
    duration_unit       VARCHAR,
    total_distance      DOUBLE,
    total_distance_unit VARCHAR,
    total_energy        DOUBLE,
    total_energy_unit   VARCHAR,
    source_name         VARCHAR,
    source_version      VARCHAR,
    device              VARCHAR,
    creation_date_raw   VARCHAR,
    start_date_raw      VARCHAR,
    end_date_raw        VARCHAR,
    start_utc           TIMESTAMP,
    end_utc             TIMESTAMP,
    metadata            JSON,
    events              JSON,
    route_files         JSON
);

CREATE TABLE stg_apple_workout_stat (
    workout_row_hash  VARCHAR NOT NULL,
    import_batch_id   INTEGER NOT NULL,
    stat_type         VARCHAR NOT NULL,
    start_date_raw    VARCHAR,
    end_date_raw      VARCHAR,
    stat_sum          DOUBLE,
    stat_avg          DOUBLE,
    stat_min          DOUBLE,
    stat_max          DOUBLE,
    unit              VARCHAR
);

CREATE TABLE stg_apple_activity_summary (
    row_hash            VARCHAR PRIMARY KEY,
    nk_hash             VARCHAR NOT NULL,
    import_batch_id     INTEGER NOT NULL,
    date_components     VARCHAR NOT NULL,
    active_energy       DOUBLE,
    active_energy_goal  DOUBLE,
    active_energy_unit  VARCHAR,
    move_time           DOUBLE,
    move_time_goal      DOUBLE,
    exercise_time       DOUBLE,
    exercise_time_goal  DOUBLE,
    stand_hours         DOUBLE,
    stand_hours_goal    DOUBLE
);

-- ECG CSVs are stored verbatim only (no interpretation, SPEC 4.1).
CREATE TABLE stg_apple_ecg (
    row_hash        VARCHAR PRIMARY KEY,
    import_batch_id INTEGER NOT NULL,
    file_name       VARCHAR NOT NULL,
    content         VARCHAR
);

-- Inventory of other files found in an export (routes, ignored files...).
CREATE TABLE stg_source_file (
    import_batch_id INTEGER NOT NULL,
    file_path       VARCHAR NOT NULL,
    file_kind       VARCHAR NOT NULL,   -- main | route | ecg | ignored | other
    size_bytes      BIGINT
);

-- ---------------------------------------------------------------- mapping (from config/*.yaml, rebuilt every time)
CREATE TABLE map_apple_type (hk_type VARCHAR PRIMARY KEY, metric_key VARCHAR, target VARCHAR, scale_if_fraction DOUBLE);
CREATE TABLE map_sleep_value (vendor VARCHAR, value_text VARCHAR, stage_key VARCHAR, PRIMARY KEY (vendor, value_text));
CREATE TABLE map_unit (from_unit VARCHAR, to_unit VARCHAR, factor DOUBLE, PRIMARY KEY (from_unit, to_unit));
CREATE TABLE map_apple_workout_type (hk_type VARCHAR PRIMARY KEY, unified_type VARCHAR, category VARCHAR);
CREATE TABLE tz_offset (valid_from_utc TIMESTAMP PRIMARY KEY, offset_min INTEGER NOT NULL);

-- ---------------------------------------------------------------- dimensions
CREATE TABLE dim_date (
    date_key        INTEGER PRIMARY KEY,
    date            DATE NOT NULL,
    year            SMALLINT,
    quarter         TINYINT,
    month           TINYINT,
    month_name_hu   VARCHAR,
    iso_year        SMALLINT,
    iso_week        TINYINT,
    day_of_month    TINYINT,
    day_of_week     TINYINT,           -- 1 = Monday ... 7 = Sunday
    day_name_hu     VARCHAR,
    is_weekend      BOOLEAN,
    is_holiday_hu   BOOLEAN,
    holiday_name_hu VARCHAR,
    is_workday_hu   BOOLEAN,
    season          VARCHAR,           -- winter | spring | summer | autumn (meteorological)
    season_hu       VARCHAR
);

CREATE TABLE dim_time_of_day (
    minute_of_day   SMALLINT PRIMARY KEY,
    hour            TINYINT,
    minute          TINYINT,
    part_of_day     VARCHAR,           -- night | morning | forenoon | afternoon | evening
    part_of_day_hu  VARCHAR
);

CREATE TABLE dim_source (
    source_key        VARCHAR PRIMARY KEY,
    vendor            VARCHAR,
    source_name       VARCHAR,
    device_type       VARCHAR,
    device_model      VARCHAR,
    hardware          VARCHAR,
    software_version  VARCHAR,
    first_date        DATE,
    last_date         DATE
);

CREATE TABLE dim_metric (
    metric_key        VARCHAR PRIMARY KEY,
    name_hu           VARCHAR,
    name_en           VARCHAR,
    category          VARCHAR,
    unit              VARCHAR,
    agg_rule          VARCHAR,          -- SUM | AVG | MIN | MAX | LAST
    better_direction  VARCHAR,          -- higher | lower | neutral
    valid_min         DOUBLE,
    valid_max         DOUBLE,
    note              VARCHAR,
    is_auto           BOOLEAN DEFAULT FALSE,
    source_type       VARCHAR            -- original source type for auto metrics
);

CREATE TABLE dim_activity_type (
    activity_type_key VARCHAR PRIMARY KEY,
    vendor            VARCHAR,
    original_name     VARCHAR,
    unified_type      VARCHAR,          -- running | walking | cycling | swimming | strength | hiit | hiking | yoga | other
    unified_name_hu   VARCHAR,
    category          VARCHAR           -- aerobic | anaerobic | mobility
);

CREATE TABLE dim_sleep_stage (
    stage_key   VARCHAR PRIMARY KEY,
    name_hu     VARCHAR,
    name_en     VARCHAR,
    is_asleep   BOOLEAN,
    sort_order  TINYINT,
    note        VARCHAR
);

CREATE TABLE dim_hr_zone (
    zone        TINYINT PRIMARY KEY,
    name_hu     VARCHAR,
    lower_pct   DOUBLE,     -- % of max HR
    upper_pct   DOUBLE
);

CREATE TABLE dim_user_profile (
    valid_from     DATE NOT NULL,
    valid_to       DATE,
    birth_year     SMALLINT,
    sex            VARCHAR,
    height_cm      DOUBLE,
    max_hr         DOUBLE,
    max_hr_source  VARCHAR,          -- estimated | measured
    zone_bounds    VARCHAR,          -- JSON list of zone lower bounds (bpm)
    PRIMARY KEY (valid_from)
);

CREATE SEQUENCE IF NOT EXISTS seq_event START 1;
CREATE TABLE dim_event (
    event_id    INTEGER PRIMARY KEY DEFAULT nextval('seq_event'),
    start_date  DATE NOT NULL,
    end_date    DATE,
    category    VARCHAR,
    label       VARCHAR NOT NULL,
    note        VARCHAR,
    created_at  TIMESTAMP DEFAULT current_timestamp
);

-- ---------------------------------------------------------------- facts
CREATE TABLE fact_measurement (
    metric_key      VARCHAR NOT NULL,
    source_key      VARCHAR NOT NULL,
    ts_utc          TIMESTAMP NOT NULL,
    ts_local        TIMESTAMP NOT NULL,
    tz_offset_min   SMALLINT,
    end_ts_utc      TIMESTAMP,
    end_ts_local    TIMESTAMP,
    date_key        INTEGER NOT NULL,
    minute_of_day   SMALLINT,
    value           DOUBLE,
    value_text      VARCHAR,
    unit            VARCHAR,
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL,
    record_nk       VARCHAR
);

CREATE TABLE fact_heart_rate_sample (
    source_key      VARCHAR NOT NULL,
    ts_utc          TIMESTAMP NOT NULL,
    ts_local        TIMESTAMP NOT NULL,
    tz_offset_min   SMALLINT,
    date_key        INTEGER NOT NULL,
    minute_of_day   SMALLINT,
    bpm             DOUBLE,
    context         VARCHAR,           -- rest | activity | sleep
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL
);

CREATE TABLE fact_stress_sample (
    source_key      VARCHAR NOT NULL,
    ts_utc          TIMESTAMP NOT NULL,
    ts_local        TIMESTAMP NOT NULL,
    tz_offset_min   SMALLINT,
    date_key        INTEGER NOT NULL,
    minute_of_day   SMALLINT,
    stress          DOUBLE,
    category        VARCHAR,
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL
);

CREATE TABLE fact_sleep_session (
    session_id         VARCHAR PRIMARY KEY,
    source_key         VARCHAR NOT NULL,
    sleep_date_key     INTEGER NOT NULL,
    sleep_date         DATE NOT NULL,
    is_main            BOOLEAN,
    start_utc          TIMESTAMP,
    end_utc            TIMESTAMP,
    start_local        TIMESTAMP,
    end_local          TIMESTAMP,
    start_tz_offset_min SMALLINT,
    end_tz_offset_min  SMALLINT,
    in_bed_min         DOUBLE,
    asleep_min         DOUBLE,
    deep_min           DOUBLE,
    light_min          DOUBLE,
    rem_min            DOUBLE,
    awake_min          DOUBLE,
    unspecified_min    DOUBLE,
    efficiency_pct     DOUBLE,
    sleep_score        DOUBLE,
    hr_avg             DOUBLE,
    hr_min             DOUBLE,
    respiratory_rate_avg DOUBLE,
    spo2_avg           DOUBLE,
    stress_avg         DOUBLE,
    has_stages         BOOLEAN,
    quality_flag       VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id    INTEGER NOT NULL
);

CREATE TABLE fact_sleep_stage_interval (
    session_id      VARCHAR,
    source_key      VARCHAR NOT NULL,
    stage_key       VARCHAR NOT NULL,
    start_utc       TIMESTAMP NOT NULL,
    end_utc         TIMESTAMP NOT NULL,
    start_local     TIMESTAMP,
    end_local       TIMESTAMP,
    tz_offset_min   SMALLINT,
    duration_min    DOUBLE,
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL
);

CREATE TABLE fact_hrv_nightly (
    source_key      VARCHAR NOT NULL,
    sleep_date_key  INTEGER NOT NULL,
    sleep_date      DATE NOT NULL,
    session_id      VARCHAR,
    hrv_method      VARCHAR NOT NULL,    -- RMSSD | SDNN (never mixed)
    hrv_avg_ms      DOUBLE,
    hrv_7d_avg_ms   DOUBLE,
    device_status   VARCHAR,
    baseline_low_ms DOUBLE,
    baseline_high_ms DOUBLE,
    sample_count    INTEGER,
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL,
    PRIMARY KEY (source_key, sleep_date_key, hrv_method)
);

CREATE TABLE fact_activity (
    activity_id        VARCHAR PRIMARY KEY,
    source_key         VARCHAR NOT NULL,
    activity_type_key  VARCHAR NOT NULL,
    date_key           INTEGER NOT NULL,
    start_utc          TIMESTAMP,
    end_utc            TIMESTAMP,
    start_local        TIMESTAMP,
    end_local          TIMESTAMP,
    tz_offset_min      SMALLINT,
    duration_min       DOUBLE,
    distance_km        DOUBLE,
    energy_kcal        DOUBLE,
    hr_avg             DOUBLE,
    hr_max             DOUBLE,
    hr_min             DOUBLE,
    avg_speed_kmh      DOUBLE,
    pace_min_per_km    DOUBLE,
    elevation_gain_m   DOUBLE,
    aerobic_effect     DOUBLE,
    anaerobic_effect   DOUBLE,
    training_load      DOUBLE,
    vo2max             DOUBLE,
    recovery_time_h    DOUBLE,
    hr_recovery_1min   DOUBLE,
    quality_flag       VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id    INTEGER NOT NULL
);

CREATE TABLE fact_activity_hr_zone (
    activity_id     VARCHAR NOT NULL,
    source_key      VARCHAR NOT NULL,
    zone            TINYINT NOT NULL,
    seconds_in_zone DOUBLE,
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL
);

CREATE TABLE fact_body_measure (
    source_key      VARCHAR NOT NULL,
    ts_utc          TIMESTAMP NOT NULL,
    ts_local        TIMESTAMP NOT NULL,
    tz_offset_min   SMALLINT,
    date_key        INTEGER NOT NULL,
    body_mass_kg    DOUBLE,
    body_fat_pct    DOUBLE,
    bmi             DOUBLE,
    bmi_is_computed BOOLEAN,
    lean_mass_kg    DOUBLE,
    muscle_mass_kg  DOUBLE,
    body_water_pct  DOUBLE,
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL
);

CREATE TABLE fact_vo2max (
    source_key      VARCHAR NOT NULL,
    date_key        INTEGER NOT NULL,
    vo2max          DOUBLE,
    sample_count    INTEGER,
    quality_flag    VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id INTEGER NOT NULL,
    PRIMARY KEY (source_key, date_key)
);

CREATE TABLE fact_daily_summary (
    date_key           INTEGER NOT NULL,
    source_key         VARCHAR NOT NULL,
    steps              DOUBLE,
    distance_km        DOUBLE,
    active_kcal        DOUBLE,
    basal_kcal         DOUBLE,
    total_kcal         DOUBLE,
    flights_climbed    DOUBLE,
    exercise_min       DOUBLE,
    stand_min          DOUBLE,
    moderate_min       DOUBLE,
    vigorous_min       DOUBLE,
    resting_hr         DOUBLE,
    walking_hr_avg     DOUBLE,
    hr_min             DOUBLE,
    hr_max             DOUBLE,
    hr_avg             DOUBLE,
    hr_sample_count    INTEGER,
    stress_avg         DOUBLE,
    body_battery_max   DOUBLE,
    body_battery_min   DOUBLE,
    spo2_avg           DOUBLE,
    spo2_min           DOUBLE,
    respiratory_rate_avg DOUBLE,
    quality_flag       VARCHAR NOT NULL DEFAULT 'ok',
    import_batch_id    INTEGER NOT NULL,
    PRIMARY KEY (date_key, source_key)
);
