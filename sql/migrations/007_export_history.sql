-- 007: export history (SPEC 8.3) – what was exported when, with which parameters.
CREATE TABLE export_history (
    export_id    VARCHAR PRIMARY KEY,
    created_at   TIMESTAMP NOT NULL,
    kind         VARCHAR NOT NULL,      -- raw | analysis | report
    fmt          VARCHAR NOT NULL,      -- xlsx | csv | parquet | pdf
    title        VARCHAR NOT NULL,
    params       JSON NOT NULL,         -- everything needed to regenerate
    file_path    VARCHAR NOT NULL,
    size_bytes   BIGINT,
    row_count    BIGINT,
    sha256       VARCHAR,
    warnings     VARCHAR,
    regenerated_from VARCHAR
);
