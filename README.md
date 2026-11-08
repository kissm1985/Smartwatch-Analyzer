# VitalMart

Helyben futó, egyfelhasználós **okosóra-adatpiac és egészségügyi elemző** alkalmazás
(Apple Watch / Apple Health, később Garmin Venu Sq 2). A követelmények forrása a
[`SPEC.md`](SPEC.md), a fejlesztési szabályok a [`CLAUDE.md`](CLAUDE.md)-ben vannak.

> **Adatvédelem:** minden adat kizárólag a gépeden, az adatmappában (`data/`, gitignore-olva)
> marad. Nincs telemetria, nincs külső hívás. A napló nem tartalmaz egészségügyi értékeket.

> Ez az alkalmazás nem orvosi eszköz, és nem diagnosztizál (SPEC 7.6).

## Állapot

| Mérföldkő | Állapot |
|---|---|
| M0 – projektváz, konfig, migrációk, szintetikus adat | ✅ kész |
| M1 – Apple Health import, staging + core, dim_date/metric/source/activity_type | ✅ kész |
| M2 – Garmin JSON + FIT import | ⏳ következik (lásd SPEC 14.1: kell egy valódi export szerkezete értékek nélkül) |
| M3–M9 | ⏳ |

## Telepítés

```bash
# uv: https://docs.astral.sh/uv/
uv sync
uv run pytest            # tesztek
uv run ruff check        # lint
```

Python 3.12+ szükséges. Telepítés után hálózat nélkül is működik.

## Első lépések

```bash
# 1) szintetikus mintaadat (valódi adat NÉLKÜL kipróbálható)
uv run python tools/make_sample_data.py --out data/sample

# 2) előnézet: felismert fájlok, rekordtípusok, dátumtartomány
uv run vitalmart import --preview data/sample/export.zip

# 3) betöltés (haladásjelzővel, tranzakcióban)
uv run vitalmart import data/sample/export.zip

# 4) mi került be?
uv run vitalmart status
```

További parancsok:

| Parancs | Leírás |
|---|---|
| `vitalmart init` | adatbázis létrehozása / migrálása |
| `vitalmart import <zip/mappa/xml> [--force] [--preview] [--source apple_health]` | import |
| `vitalmart rebuild` | a core réteg teljes újraépítése a stagingből (pl. időzóna-váltás után) |
| `vitalmart status` | táblák sorszáma |
| `vitalmart backup` | adatbázis-másolat a `data/backups/` mappába |
| `vitalmart wipe --yes` | **minden** adat törlése (adatbázis, exportok, mentések) |
| `vitalmart report` | M6-ban készül el |

Globális kapcsolók: `--data-dir`, `--config`, `-v` (technikai napló).

## Apple Health export készítése

1. iPhone-on nyisd meg az **Egészség** appot.
2. Jobb felül a profilképed → **Összes egészségügyi adat exportálása** → Exportálás.
3. Az elkészült `export.zip`-et juttasd el a gépedre (AirDrop, kábel, Fájlok app).
4. `uv run vitalmart import export.zip` – a ZIP-et nem kell kicsomagolni
   (a kicsomagolt `apple_health_export/` mappa vagy maga az `export.xml` is megadható).

Feldolgozott tartalom: `export.xml` (`Record`, `Workout`, `ActivitySummary`);
az EKG CSV-k változatlanul tárolódnak (értelmezés nélkül); a GPX útvonalak nyilvántartásba
kerülnek; az `export_cda.xml` figyelmen kívül marad.

## Garmin export

Az M2 mérföldkőben készül el (Garmin Connect „Adatok exportálása" ZIP + FIT fájlok).

## Architektúra röviden

```
export.zip ──(lxml iterparse, streaming)──► stg_apple_*      (nyers értékek, row_hash dedup)
                                              │  sql/transform/apple/*.sql
                                              ▼
                     dim_date, dim_time_of_day, dim_source, dim_metric, dim_activity_type,
                     dim_sleep_stage, dim_hr_zone, dim_import_batch
                     fact_measurement (EAV), fact_heart_rate_sample, fact_sleep_session,
                     fact_sleep_stage_interval, fact_hrv_nightly, fact_activity,
                     fact_body_measure, fact_vo2max, fact_daily_summary
```

- **Idempotens import:** azonos fájl (SHA-256) → kihagyás (`--force` felülírja); a staging
  sorai teljes tartalom-hash szerint egyediek, a core rétegben természetes kulcsonként
  a legújabb batch nyer, a régi érték a stagingben megmarad.
- **Tranzakció:** staging-betöltés + core-újraépítés egyetlen tranzakció; hibánál teljes
  rollback, a sikertelen kísérlet `failed` státusszal naplózódik.
- **Idő:** `*_utc` + `*_local` + `tz_offset_min`. A helyi idő a **beállított** időzónából
  (alapértelmezés `Europe/Budapest`) számolódik az UTC időpontból – nem az exportfájlba írt
  offsetből (az Apple az export pillanatának zónáját írja minden dátumhoz). A nyári/téli
  átállás éjszakái valós (7 / 9 órás) hosszúak.
- **Alvásnap:** alapértelmezés az ébredés napja (`sleep.day_convention: wake`); `start`
  esetén az „aznap éjjel" konvenció (a kezdés − 12 óra dátuma). Az alvási szakaszok
  forrásonként, 90 percnél rövidebb szünetekkel fűződnek össze (`sleep.session_gap_minutes`).
- **HRV:** Apple → `SDNN`, a `hrv_method` oszlop kötelező; RMSSD-vel nem keveredik.
- **Adatminőség:** az érvényességi tartományon kívüli értékek (`config/metrics.yaml`:
  `valid_min`/`valid_max`, pl. pulzus 25–230, SpO₂ ≥ 70 %) `quality_flag = 'out_of_range'`
  jelölést kapnak, nem törlődnek; a napi összesítő kizárja őket.
- **Ismeretlen rekordtípus:** automatikusan `apple_<snake_case>` metrikaként a
  `fact_measurement`-be kerül, és a `dim_metric`-be `is_auto = true` jelöléssel.
- **Konfiguráció:** `config/app.yaml` (időzóna, alvás, memória), `config/metrics.yaml`
  (metrika-katalógus), `config/apple_mapping.yaml` (HK típusok, mértékegységek, edzéstípusok).

## Teljesítmény (szintetikus adaton mérve)

| Export | Rekord | Idő | Csúcsmemória |
|---|---|---|---|
| 230 MB `export.xml` | 0,58 M | 9 s | 2,2 GB |
| 1,0 GB `export.xml` | 2,52 M | 62 s | 3,3 GB |

A DuckDB memóriakorlátja `config/app.yaml` → `database.memory_limit` (alapérték 2 GB);
e fölött az adatmappa `tmp/` alkönyvtárába lapoz. Kisebb gépen állítsd lejjebb.

## Ismert korlátok (M1)

- A core réteg minden importnál **teljesen** újraépül (gyors, de nem inkrementális – SPEC 10.3 SHOULD, M9).
- Az Apple napi összesítő (`fact_daily_summary`) forrásonként külön sor (óra és iPhone
  lépései nem adódnak össze); az „elsődleges forrás metrikánként" választás az M4 Beállítások része.
- Az Apple nem ad mérsékelt/intenzív perc bontást: a `moderate_min`/`vigorous_min` üres,
  az `exercise_min` az Edzés-gyűrű perce. A WHO-egyenértékes percek az M3-ban.
- Edzésenkénti pulzuszóna-idő (`fact_activity_hr_zone`) a felhasználói profillal (M4) együtt készül.
- GPX útvonalak csak nyilvántartásba kerülnek; EKG csak tárolás.
- Sérült (nem jól formált) `export.xml` esetén az import leáll és visszagörget
  (nincs „helyreállító" parse-olás, hogy csendben ne vesszen el adat).
- Intervallum-mennyiségek (pl. lépés) a kezdő időpont helyi napjához számítanak.

## Hibaelhárítás

- **„Ezt a fájlt már betöltötted"** – azonos fájl; `--force`-szal újratölthető (duplikáció nem keletkezik).
- **„Az XML-fájl sérült vagy csonka"** – töltsd le / exportáld újra; az adatbázis változatlan maradt.
- **Kevés a memória** – csökkentsd a `database.memory_limit` értékét.
- **Időzóna módosítása után** futtasd: `uv run vitalmart rebuild`.
