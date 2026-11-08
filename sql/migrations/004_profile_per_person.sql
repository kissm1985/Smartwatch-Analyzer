-- 004: user profile per person (household), SCD2 validity periods.
DROP TABLE dim_user_profile;
CREATE TABLE dim_user_profile (
    person_key     VARCHAR NOT NULL,
    valid_from     DATE NOT NULL,
    valid_to       DATE,                 -- NULL = current
    birth_year     SMALLINT,
    sex            VARCHAR,              -- female | male | other | NULL
    height_cm      DOUBLE,
    max_hr         DOUBLE,
    max_hr_source  VARCHAR,              -- measured | estimated
    zone_bounds    VARCHAR,              -- JSON: lower bounds (bpm) of zones 1..5
    updated_at     TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (person_key, valid_from)
);
