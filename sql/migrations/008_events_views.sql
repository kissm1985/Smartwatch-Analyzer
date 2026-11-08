-- 008: events / annotations per person (SPEC 8.2/11), saved views (SPEC 8.3 "Saját nézetek").
-- dim_event.person_key NULL = the whole household.
ALTER TABLE dim_event ADD COLUMN person_key VARCHAR;
ALTER TABLE dim_event ADD COLUMN updated_at TIMESTAMP;

-- one row per (person, day) covered by at least one event; used as the "event" join of the
-- semantic layer (labels / categories of overlapping events joined with ", ")
CREATE VIEW event_day AS
SELECT p.person_key, d.date,
       string_agg(DISTINCT e.label, ', ' ORDER BY e.label) AS labels,
       string_agg(DISTINCT coalesce(nullif(e.category, ''), e.label), ', '
                  ORDER BY coalesce(nullif(e.category, ''), e.label)) AS categories
FROM dim_event e
JOIN dim_person p ON e.person_key IS NULL OR e.person_key = p.person_key
JOIN dim_date d ON d.date BETWEEN e.start_date AND coalesce(e.end_date, e.start_date)
GROUP BY p.person_key, d.date;

CREATE TABLE saved_view (
    view_id      VARCHAR PRIMARY KEY,
    name         VARCHAR NOT NULL,
    description  VARCHAR,
    items        JSON NOT NULL,          -- ordered list of saved_analysis ids
    columns      INTEGER NOT NULL DEFAULT 2,
    period       VARCHAR,                -- NULL = keep each analysis' own period
    persons      JSON,                   -- NULL = keep each analysis' own persons
    created_at   TIMESTAMP NOT NULL,
    updated_at   TIMESTAMP NOT NULL
);
