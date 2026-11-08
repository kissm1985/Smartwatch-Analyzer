-- 006: report snapshots (SPEC 7.5) – parameters + data version + rule version + timestamp.
CREATE TABLE report_snapshot (
    snapshot_id    VARCHAR PRIMARY KEY,
    created_at     TIMESTAMP NOT NULL,
    person_key     VARCHAR NOT NULL,
    date_from      DATE NOT NULL,
    date_to        DATE NOT NULL,
    cmp_from       DATE,
    cmp_to         DATE,
    params         JSON,
    data_version   VARCHAR,
    rules_version  INTEGER,
    app_version    VARCHAR,
    html_path      VARCHAR,
    html_sha256    VARCHAR
);
