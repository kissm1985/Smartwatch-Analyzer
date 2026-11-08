-- 003: derived layer (SPEC 5.4) and data-quality tables (SPEC 4.5).
-- All derived tables are per PERSON (household) and fully rebuilt from the core layer.

-- one value per person × day × metric, taken from the primary source
CREATE TABLE derived_person_daily (
    person_key    VARCHAR NOT NULL,
    metric_key    VARCHAR NOT NULL,
    date          DATE NOT NULL,
    date_key      INTEGER NOT NULL,
    value         DOUBLE NOT NULL,
    source_key    VARCHAR NOT NULL,      -- chosen (primary) source
    n_sources     INTEGER NOT NULL,      -- how many sources had a value that day
    PRIMARY KEY (person_key, metric_key, date)
);

-- moving windows + personal baseline (preceding window, current day excluded)
CREATE TABLE derived_daily_stats (
    person_key    VARCHAR NOT NULL,
    metric_key    VARCHAR NOT NULL,
    date          DATE NOT NULL,
    date_key      INTEGER NOT NULL,
    value         DOUBLE NOT NULL,
    n7            INTEGER, ma7 DOUBLE, med7 DOUBLE,
    n28           INTEGER, ma28 DOUBLE, med28 DOUBLE,
    trend_7v28    DOUBLE,                -- ma7 - ma28
    bl_n          INTEGER,               -- valid days in the baseline window
    bl_mean       DOUBLE, bl_std DOUBLE, bl_median DOUBLE, bl_mad DOUBLE,
    z_score       DOUBLE,                -- (value - bl_mean) / bl_std
    robust_z      DOUBLE,                -- (value - bl_median) / (1.4826 * bl_mad)
    outside_band  BOOLEAN,               -- |z| >= derived.band_z
    PRIMARY KEY (person_key, metric_key, date)
);

CREATE TABLE derived_sleep_regularity (
    person_key     VARCHAR NOT NULL,
    date           DATE NOT NULL,        -- sleep_date
    date_key       INTEGER NOT NULL,
    bedtime_min    DOUBLE,               -- minutes after 12:00 of the evening day
    waketime_min   DOUBLE,               -- minutes after 00:00 of the wake day
    sleep_min      DOUBLE,
    n7             INTEGER,
    bedtime_std7   DOUBLE, bedtime_std14 DOUBLE,
    waketime_std7  DOUBLE, waketime_std14 DOUBLE,
    debt7_min      DOUBLE,               -- Σ (goal − sleep) over the last 7 nights with data
    debt14_min     DOUBLE,
    PRIMARY KEY (person_key, date)
);

CREATE TABLE derived_weekly_activity (
    person_key        VARCHAR NOT NULL,
    week_start        DATE NOT NULL,     -- ISO week Monday
    iso_year          SMALLINT, iso_week TINYINT,
    days_with_data    INTEGER,
    moderate_min      DOUBLE,
    vigorous_min      DOUBLE,
    who_equiv_min     DOUBLE,            -- moderate + 2 × vigorous (WHO)
    who_method        VARCHAR,           -- intensity_minutes | exercise_as_moderate | mixed
    steps             DOUBLE,
    activity_count    INTEGER,
    active_days       INTEGER,
    strength_days     INTEGER,
    rest_days         INTEGER,
    rest_days_std4w   DOUBLE,            -- regularity of rest days (std over 4 weeks)
    total_load        DOUBLE,
    PRIMARY KEY (person_key, week_start)
);

CREATE TABLE derived_training_load (
    person_key   VARCHAR NOT NULL,
    date         DATE NOT NULL,
    date_key     INTEGER NOT NULL,
    load         DOUBLE NOT NULL,        -- 0 on days without activity
    load_method  VARCHAR,                -- device_load | edwards_trimp | duration | none | mixed
    is_rest_day  BOOLEAN,
    acute        DOUBLE,                 -- mean daily load, acute window
    chronic      DOUBLE,                 -- mean daily load, chronic window
    acwr         DOUBLE,                 -- acute / chronic (approximation!)
    acwr_valid   BOOLEAN,
    PRIMARY KEY (person_key, date)
);

-- ---------------------------------------------------------------- data quality
CREATE TABLE dq_coverage (
    person_key     VARCHAR, source_key VARCHAR, metric_key VARCHAR,
    first_date     DATE, last_date DATE,
    days_with_data INTEGER, days_in_span INTEGER, coverage_pct DOUBLE
);

CREATE TABLE dq_gap (
    person_key VARCHAR, source_key VARCHAR, metric_key VARCHAR,
    gap_start DATE, gap_end DATE, gap_days INTEGER
);

CREATE TABLE dq_flag_summary (
    person_key VARCHAR, source_key VARCHAR, fact_table VARCHAR, metric_key VARCHAR,
    quality_flag VARCHAR, n BIGINT, total BIGINT
);

CREATE TABLE dq_nonwear (
    person_key VARCHAR, source_key VARCHAR, kind VARCHAR,   -- hr_gap | hr_constant | offwrist
    start_utc TIMESTAMP, end_utc TIMESTAMP, start_local TIMESTAMP, end_local TIMESTAMP,
    minutes DOUBLE, samples INTEGER
);

CREATE TABLE dq_source_switch (
    person_key VARCHAR, metric_key VARCHAR, date DATE,
    from_source VARCHAR, to_source VARCHAR
);
