-- 009: sources excluded from analysis (Beállítások → Források). Filled from
-- config derived.excluded_sources at every rebuild; the data itself is kept.
CREATE TABLE map_excluded_source (
    source_key VARCHAR PRIMARY KEY
);
