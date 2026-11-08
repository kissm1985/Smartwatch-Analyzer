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
- Mintaadat: `uv run python tools/make_sample_data.py --out data/sample` (szintetikus, determinisztikus).
