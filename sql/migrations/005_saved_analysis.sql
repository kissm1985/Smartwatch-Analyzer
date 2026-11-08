-- 005: saved Explorer analyses (SPEC 8.3). The full definition is stored as JSON.
CREATE TABLE saved_analysis (
    analysis_id  VARCHAR PRIMARY KEY,      -- builtin:<key> | user:<hex>
    name         VARCHAR NOT NULL,
    description  VARCHAR,
    tags         VARCHAR[],
    spec         JSON NOT NULL,
    is_builtin   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP NOT NULL DEFAULT current_timestamp,
    updated_at   TIMESTAMP NOT NULL DEFAULT current_timestamp
);
