-- 002: persons (household: several people, one device set each) + Garmin staging.
-- A person is NOT a login/user (SPEC 1.2 stays: single local operator); it is an
-- analysis dimension. Facts reach the person through dim_source.person_key.

CREATE TABLE dim_person (
    person_key    VARCHAR PRIMARY KEY,   -- short code, e.g. 'me', 'partner'
    display_name  VARCHAR,
    created_at    TIMESTAMP DEFAULT current_timestamp
);

ALTER TABLE dim_import_batch ADD COLUMN person_key VARCHAR;
ALTER TABLE dim_source ADD COLUMN person_key VARCHAR;
ALTER TABLE dim_source ADD COLUMN person_conflict BOOLEAN DEFAULT FALSE;

-- zone 0 = below zone 1 (Garmin reports time below the first zone floor)
INSERT INTO dim_hr_zone VALUES (0, '0. zóna – az 1. zóna alatt', 0.0, 50.0)
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------- staging (Garmin Connect export JSON)
-- One row per JSON record; the full record is kept verbatim (unknown fields included).
CREATE TABLE stg_garmin_record (
    row_hash         VARCHAR PRIMARY KEY,
    nk_hash          VARCHAR NOT NULL,
    import_batch_id  INTEGER NOT NULL,
    file_kind        VARCHAR NOT NULL,   -- uds | sleep | activities | vo2max | biometrics | ... | generic:<name>
    file_name        VARCHAR NOT NULL,
    natural_key      VARCHAR,
    payload          JSON NOT NULL
);

-- ---------------------------------------------------------------- staging (FIT)
CREATE TABLE stg_garmin_fit_file (
    file_hash        VARCHAR PRIMARY KEY,   -- sha256 of the FIT bytes
    import_batch_id  INTEGER NOT NULL,
    file_name        VARCHAR NOT NULL,
    file_type        VARCHAR,               -- activity | monitoring_a | monitoring_b | ...
    manufacturer     VARCHAR,
    product          VARCHAR,
    serial_number    VARCHAR,
    time_created_utc TIMESTAMP
);

CREATE TABLE stg_garmin_fit_message (
    file_hash        VARCHAR NOT NULL,
    msg_index        INTEGER NOT NULL,
    message          VARCHAR NOT NULL,      -- session | record | monitoring | stress_level | ...
    ts_utc           TIMESTAMP,             -- resolved (timestamp_16 expanded)
    fields           JSON                   -- all decoded fields, verbatim
);
