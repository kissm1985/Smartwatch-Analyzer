-- Shared macros for all vendor transforms.
CREATE OR REPLACE TEMP MACRO unified_name_hu(t) AS CASE t
    WHEN 'running' THEN 'Futás' WHEN 'walking' THEN 'Gyaloglás'
    WHEN 'cycling' THEN 'Kerékpározás' WHEN 'swimming' THEN 'Úszás'
    WHEN 'strength' THEN 'Erősítés' WHEN 'hiit' THEN 'HIIT'
    WHEN 'hiking' THEN 'Túrázás' WHEN 'yoga' THEN 'Jóga / mobilitás'
    WHEN 'racket' THEN 'Ütős sport (squash, tenisz …)'
    ELSE 'Egyéb' END;

CREATE OR REPLACE TEMP MACRO yyyymmdd(d) AS CAST(strftime(d, '%Y%m%d') AS INTEGER);
