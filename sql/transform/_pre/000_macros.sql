-- Shared macros for all vendor transforms.
CREATE OR REPLACE TEMP MACRO unified_name_hu(t) AS CASE t
    WHEN 'running' THEN 'Futás' WHEN 'walking' THEN 'Gyaloglás'
    WHEN 'cycling' THEN 'Kerékpározás' WHEN 'swimming' THEN 'Úszás'
    WHEN 'strength' THEN 'Erősítés' WHEN 'hiit' THEN 'HIIT'
    WHEN 'hiking' THEN 'Túrázás' WHEN 'yoga' THEN 'Jóga / mobilitás'
    WHEN 'racket' THEN 'Ütős sport (squash, tenisz …)'
    ELSE 'Egyéb' END;

CREATE OR REPLACE TEMP MACRO yyyymmdd(d) AS CAST(strftime(d, '%Y%m%d') AS INTEGER);

-- Apple: the EXTERNAL device of a record, i.e. a band / sensor / headphone whose data an
-- app or the iPhone saved under its own sourceName (HKDevice string). NULL when the record
-- comes from the source's own Apple device or has no device at all. Records of an external
-- device get their own source ("<source> › <device>", type 'other'), so e.g. a Bluetooth
-- heart-rate band paired with the iPhone is not counted as iPhone data.
CREATE OR REPLACE TEMP MACRO apple_ext_device(dev) AS CASE
    WHEN dev IS NULL OR trim(dev) = '' THEN NULL
    WHEN dev ILIKE '%name:iPhone%' OR dev ILIKE '%name:Apple Watch%'
         OR dev ILIKE '%model:iPhone%' OR dev ILIKE '%model:Watch%'
         OR dev ILIKE '%hardware:iPhone%' OR dev ILIKE '%hardware:Watch%' THEN NULL
    ELSE coalesce(
        nullif(trim(regexp_extract(dev, 'name:([^,>]+)', 1)), ''),
        nullif(trim(regexp_extract(dev, 'manufacturer:([^,>]+)', 1) || ' '
                    || regexp_extract(dev, 'model:([^,>]+)', 1)), ''),
        'külső eszköz') END;

CREATE OR REPLACE TEMP MACRO apple_source_key(src, dev) AS
    'apple:' || coalesce(src, 'unknown') || coalesce(' › ' || apple_ext_device(dev), '');
