# VitalMart – projektszabályok
- A SPEC.md az igazság forrása. Eltérés előtt kérdezz.
- Egészségügyi adat: SOHA ne kerüljön a repóba, a logba, hálózati hívásba. Tesztelni csak szintetikus adattal.
- A kód angol, a felhasználói szövegek és a riport magyar (i18n fájlokból).
- Nincs LLM/külső API-hívás az elemzésben. A riport szabályalapú (config/rules.yaml).
- HRV: mindig tárold a módszert (RMSSD vs SDNN); forrásközt ne keverd.
- Idő: UTC + helyi idő + offset; alvásnap = ébredés napja (konfigurálható).
- Minden importnak idempotensnek és tranzakciósnak kell lennie.
- Új funkcióhoz teszt; a mérföldkő végén futtasd: uv run pytest && uv run ruff check.
- Orvosi küszöböket/szövegeket csak a rules.yaml-ban módosíts, és jelöld, hogy felülvizsgálatra szorulnak.
- Soha ne írj diagnosztikus megfogalmazást ("Önnek X betegsége van"); csak leíró, tárgyilagos mondatok.

## Fejlesztői jegyzetek
- Telepítés: `uv sync`; tesztek: `uv run pytest`; lint: `uv run ruff check`.
- Rétegek: `stg_*` (nyers, csak hozzáfűzés, row_hash szerint deduplikálva) → `sql/transform/*.sql` (core újraépítés) → `dim_*`/`fact_*`.
- Konfiguráció (`config/*.yaml`) a rebuild elején `map_*` táblákba töltődik; a transzformációk tiszta SQL-ek.
- Mintaadat: `uv run python tools/make_sample_data.py --out data/sample [--garmin]` (szintetikus, determinisztikus).
- Garmin: a mezőleképezés KIZÁRÓLAG `config/garmin_mapping.yaml`-ban; a `transform/garmin.py` ebből generál SQL-t.
- Személy (háztartás): `dim_person`, `dim_import_batch.person_key`, `dim_source.person_key`; a tényeket a forráson át szűrjük.
- Valódi export szerkezetét csak `vitalmart schema` kimenetként (értékek nélkül) nézd; azonosítót, értéket ne írj a repóba.
- Származtatott réteg: `sql/transform/derived/*.sql` (paraméterek: `config/app.yaml` → `derived`, `quality`); numerikus változtatásnál a `tests/test_derived.py` pandas/scipy referenciatesztjeit is futtasd.
- Elemző: mérőszám/dimenzió csak `config/semantic.yaml`-ban; felhasználói érték mindig SQL-paraméter. Új kifejezésnél a `tests/test_semantic.py::test_every_measure_and_dimension_compiles` ellenőrzi. Streamlit-oldalak: `app/views/` (nem `pages/`, hogy ne induljon a régi automatikus navigáció).
- Riport: szabálytípusok a `src/vitalmart/report/rules.py`-ban, küszöb/szöveg/forrás CSAK a `config/rules.yaml`-ban; reprodukálhatóság miatt az ábraadatokat kerekítjük, a div-azonosítók determinisztikusak.
- Export: táblák/oszlopszótár CSAK a `config/export_tables.yaml`-ban (stg_* tilos); PDF ReportLab + matplotlib (`export/figures.py` Plotly→PNG 200 dpi, `export/pdf.py`), XLSX xlsxwriter (`export/tabular.py`), előzmények `export_history` (`export/service.py`); minden export az adatmappa `exports/` alá kerül.
