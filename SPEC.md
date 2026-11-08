# Követelményspecifikáció – „VitalMart": személyes okosóra-adatpiac és egészségügyi elemző alkalmazás

Verzió: 1.0 · Dátum: 2026-11-08 · Célközönség: Claude Code (implementáló), a termék tulajdonosa (István)

---

## 0. Hogyan használd ezt a dokumentumot Claude Code-dal

1. Hozz létre egy üres projektmappát, másold bele ezt a fájlt `SPEC.md` néven.
2. Indítsd a Claude Code-ot, és add ki: *„Olvasd el a SPEC.md-t. Először készíts tervet (plan mode) a 12. fejezet mérföldkövei szerint, majd valósítsd meg az M1-et tesztekkel. Minden mérföldkő végén állj meg, és mutasd meg az eredményt."*
3. A 13. fejezet tartalmazza a `CLAUDE.md`-be másolható szabályokat.
4. A „MUST / SHOULD / MAY" jelölések: MUST = kötelező, SHOULD = erősen javasolt, MAY = opcionális.

---

## 1. Cél és hatókör

### 1.1 Cél
Egyetlen felhasználó számára, **helyben (lokálisan) futó** alkalmazás, amely:

1. okosóra-exportokat (Garmin Venu Sq 2, Apple Watch / Apple Health) beolvas,
2. ezekből **adatpiacot** (csillagséma: ténytáblák + dimenziók) épít,
3. az adatokból **átfogó, adatokkal alátámasztott egészségügyi elemzést** (riportot) készít (sport, alvás, HRV, stressz, pulzus, trendek stb.),
4. **interaktív elemző felületet** ad, ahol a felhasználó a tényadatokból és dimenziókból saját grafikonos és táblázatos kutatásokat állíthat össze,
5. az elemzéseket **PDF-be**, a nyers/aggregált adatokat **XLSX-be** exportálja.

### 1.2 Nem célok
- Nem orvosi eszköz, **nem diagnosztizál**, nem ad terápiás javaslatot (lásd 7.6 és 10.1).
- Nincs többfelhasználós működés, bejelentkezés, felhő-szinkron, mobilalkalmazás.
- Nincs közvetlen Garmin/Apple API-integráció (csak fájlimport). A Garmin Connect nem hivatalos API-k használata kizárt.
- Nincs valós idejű adatfolyam.

### 1.3 Felhasználói profil
Egy felhasználó, magyar nyelvű felület (alapértelmezett), angol kódazonosítók. A felhasználó technikailag jártas (adat/BI háttér), a riport nyelve magyar. A felület nyelve i18n fájlokból jöjjön (hu, en), hogy később bővíthető legyen.

---

## 2. Technológiai javaslat (MUST, hacsak az implementáló indokolt módon nem tér el)

| Réteg | Választás | Indoklás |
|---|---|---|
| Nyelv | Python 3.12+ | adatfeldolgozás, ökoszisztéma |
| Csomagkezelés | `uv` + `pyproject.toml` | reprodukálható környezet |
| Adatpiac | **DuckDB** (egyetlen `.duckdb` fájl) | oszlopos, gyors analitika, nulla üzemeltetés, SQL |
| Átalakítás | tiszta Python + SQL (`sql/` mappa, verziózott migrációk) | átlátható, tesztelhető |
| UI | **Streamlit** (többoldalas) | gyors, interaktív, helyben fut |
| Grafikonok | **Plotly** | interaktív; statikus exporthoz `kaleido` |
| PDF | **WeasyPrint** (HTML→PDF, Jinja2 sablon) vagy ReportLab | szép, sablonozható riport, magyar ékezetek |
| XLSX | `xlsxwriter` vagy `openpyxl` | formázott, több munkalapos export |
| XML (Apple) | `lxml.etree.iterparse` (streaming) | az `export.xml` több száz MB is lehet |
| FIT (Garmin) | `fitdecode` vagy `fitparse` | aktivitásfájlok |
| Validáció | `pydantic` v2 | konfiguráció, adatmodellek |
| Teszt | `pytest`, `hypothesis` (opcionális) | |
| Minőség | `ruff`, `mypy` (SHOULD) | |

Indítás: `uv run streamlit run app/main.py`. CLI is legyen: `vitalmart import <path>`, `vitalmart rebuild`, `vitalmart report --from --to --out`.

---

## 3. Architektúra

```
Forrásfájlok (ZIP/XML/JSON/CSV/FIT)
        │  importer (parserek, forrásonként plugin)
        ▼
  [RAW / STAGING réteg]   stg_* táblák, eredeti értékek, import_batch hivatkozás
        │  tisztítás, normalizálás, egységesítés, deduplikálás
        ▼
  [CORE / DATAMART]       dim_* + fact_* (csillagséma)
        │  származtatott mutatók (derived metrics), alapvonalak
        ▼
  [SEMANTIC LAYER]        YAML: mérőszámok, dimenziók, join-ok (az Explorer ezt használja)
        │
        ├── Riport-motor (szabályalapú elemzés → HTML → PDF)
        ├── Explorer UI (interaktív elemzések)
        └── Export (PDF, XLSX)
```

Alapelvek:
- **MUST** idempotens import: ugyanazt a fájlt kétszer betöltve nem keletkezhet duplikáció.
- **MUST** teljes adat-eredet (lineage): minden ténysor visszavezethető `import_batch_id`-re és a forrásfájlra.
- **MUST** a nyers (staging) réteg változatlanul megőrzi a forrásértékeket; a core réteg a staging-ből **újraépíthető** (`vitalmart rebuild`).
- **SHOULD** a forrásonkénti parser egy közös interfészt valósítson meg (`SourceAdapter.discover()`, `.parse()`), hogy új eszköz (pl. Fitbit, Oura) később hozzáadható legyen.

---

## 4. Bemenetek és importálás

### 4.1 Támogatott források

**A) Apple Health / Apple Watch** (a felhasználó az iPhone Egészség appból exportál: Profil → Összes egészségügyi adat exportálása → `export.zip`)
- `apple_health_export/export.xml` – `<Record>`, `<Workout>`, `<ActivitySummary>` elemek (MUST).
- `workout-routes/*.gpx` – útvonalak (MAY, első verzióban kihagyható, de a parser ne törjön el rajta).
- `electrocardiograms/*.csv` – EKG (MAY; csak tárolás, **értelmezés nincs**).
- Az `export_cda.xml`-t figyelmen kívül kell hagyni.
- Kezelendő rekordtípusok (minimum): `HeartRate`, `RestingHeartRate`, `WalkingHeartRateAverage`, `HeartRateVariabilitySDNN`, `HeartRateRecoveryOneMinute`, `StepCount`, `DistanceWalkingRunning`, `ActiveEnergyBurned`, `BasalEnergyBurned`, `AppleExerciseTime`, `AppleStandTime`, `FlightsClimbed`, `VO2Max`, `OxygenSaturation`, `RespiratoryRate`, `BodyMass`, `BodyFatPercentage`, `SleepAnalysis` (kategória: InBed, AsleepUnspecified, AsleepCore, AsleepDeep, AsleepREM, Awake), `Workout` (típus, időtartam, távolság, energia, statisztikák).
- Az ismeretlen rekordtípusokat a rendszer az általános mérési táblába (`fact_measurement`) tölti, nem dobja el és nem is áll le.

**B) Garmin (Venu Sq 2)** – két támogatott út:
1. **Garmin Connect „Adatok exportálása"** (fiókbeállítások → adatkezelés → adatok exportálása) → e-mailben kapott ZIP, amely JSON/CSV fájlokat tartalmaz (jellemzően `DI_CONNECT/` alatt: wellness, fitness/aktivitás, alvás, aggregátor/napi összesítő, stb.). **A fájlnevek és a séma verziónként eltérhetnek**, ezért a parser:
   - fájlnév-minta + tartalom-alapú felismerést használjon (MUST),
   - a mezőleképezés külön konfigurációs fájlban legyen (`config/garmin_mapping.yaml`), hogy séma-változáskor ne kódot kelljen módosítani (MUST),
   - ismeretlen mezőket ne dobjon el, hanem a nyers rétegben megőrizze (MUST).
2. **FIT fájlok** (USB-n az óráról: `GARMIN/Activity`, `GARMIN/Monitor`) – aktivitások és monitoring (MUST az aktivitásokra, SHOULD a monitoringra).
3. **Garmin Connect CSV export** aktivitáslistáról (MAY).

> Fontos: a Venu Sq 2 nem minden mérőszámot rögzít (pl. HRV-státusz, edzésterhelés/Training Status eszközfüggő lehet). A rendszernek **hiányzó mérőszámot hibaként ne kezeljen**, hanem „nincs adat" állapotként, és a riport jelezze, mely szekciókhoz nem volt elegendő adat.

### 4.2 Importfolyamat (MUST)
1. A felhasználó az **Import** oldalon feltölt (fájl/ZIP) vagy egy helyi mappát jelöl ki; CLI-ből útvonalat ad.
2. Forrás-felismerés (Apple / Garmin JSON / FIT) automatikusan, a felhasználó felülbírálhatja.
3. Előnézet: felismert fájlok, rekordtípusok, dátumtartomány, rekordszám, figyelmeztetések. Megerősítés után indul a betöltés.
4. Betöltés haladásjelzővel (nagy Apple XML-nél is, streaming feldolgozás, korlátos memóriahasználat; cél: 1 GB export.xml < 4 GB RAM).
5. Betöltés után összefoglaló: új / frissített / kihagyott (duplikált) rekordok, hibák száma, a betöltött időtartomány.
6. Importnapló (`dim_import_batch`): azonosító, időpont, forrás, fájlnév, SHA-256, rekordszámok, státusz.

### 4.3 Deduplikálás és frissítés
- Természetes kulcs ténytáblánként (pl. forrás + metrika + kezdő időbélyeg + eszköz). Ütközéskor: **upsert**, az újabb batch nyer; a régi érték a nyers rétegben megmarad.
- Ugyanaz a fájl (azonos SHA-256) másodszori betöltése: figyelmeztetés + kihagyás (felülírható kapcsolóval).
- Egy időszakot lefedő két forrás (pl. Garmin + Apple egy időben) **nem olvad össze csendben**: minden ténysor hordozza a `source_key`-t; a mutatók forrásonként külön kezelhetők, és a felhasználó választhat „elsődleges forrást" metrikánként (Beállítások).

### 4.4 Idő- és időzónakezelés (MUST)
- Minden időbélyeg tárolása **UTC-ben** és a **helyi időben** (`ts_utc`, `ts_local`, `tz_offset_min`), mert az alvás/napi összegzés a helyi naphoz kötődik.
- Alapértelmezett időzóna: `Europe/Budapest` (konfigurálható). Nyári/téli időszámítás-váltás tesztelendő (az alvás és a 23/25 órás napok).
- „Alvásnap" konvenció: az éjszakai alvás annak a naptári napnak tulajdonítandó, amelyen **felébredtél** (`sleep_date`). A konvenció legyen dokumentált és konfigurálható.

### 4.5 Adatminőség (MUST)
Automatikus ellenőrzések importkor és külön „Adatminőség" oldalon:
- lefedettség naponta metrikánként (hány nap van adat), hézagok listája,
- fiziológiailag lehetetlen értékek jelölése (pl. pulzus < 25 vagy > 230, SpO₂ < 70 %), ezek **nem törlődnek**, hanem `quality_flag` kapnak, és a riport/Explorer alapból kizárja őket (kapcsolóval visszakapcsolható),
- az eszköz le nem viselésének jelei (hosszú null/konstans szakaszok),
- eszközváltás/forrásváltás jelzése az idővonalon.

---

## 5. Adatpiac modell (csillagséma)

Névkonvenció: `dim_*`, `fact_*`, `stg_*`; snake_case; minden ténytábla tartalmaz `import_batch_id`, `source_key`, `quality_flag` oszlopokat.

### 5.1 Dimenziók

| Tábla | Szemcsézettség / tartalom |
|---|---|
| `dim_date` | 1 sor/nap, 2015–2040: `date_key (YYYYMMDD)`, dátum, év, negyedév, hónap, hét (ISO), hét napja, hétvége?, magyar ünnepnap?, évszak |
| `dim_time_of_day` | 1 sor/perc: óra, perc, napszak (éjszaka/reggel/délelőtt/délután/este) |
| `dim_source` | forrás/eszköz: `source_key`, gyártó (Garmin/Apple), eszköztípus, szoftververzió, első/utolsó adat dátuma |
| `dim_metric` | metrika-katalógus: kód, magyar/angol név, kategória (alvás, szív, stressz, aktivitás, testösszetétel, légzés), mértékegység, aggregációs szabály (SUM/AVG/MIN/MAX/LAST), „jobb irány" (magasabb/alacsonyabb/semleges), megjegyzés (pl. HRV módszer) |
| `dim_activity_type` | egységesített edzéstípus (futás, gyaloglás, kerékpár, úszás, erősítés, HIIT, túra, jóga, egyéb) + a forrás eredeti típusneve, kategória (aerob/anaerob/mobilitás) |
| `dim_sleep_stage` | ébren, könnyű/core, mély, REM, ágyban (in bed), nem megadott |
| `dim_hr_zone` | zónák 1–5 (a felhasználó max. pulzusa/zónahatárai alapján, konfigurálható) |
| `dim_import_batch` | lineage (lásd 4.2) |
| `dim_user_profile` | érvényességi időszakos (SCD2): születési év, nem, magasság, max. pulzus becslés/mért, zónahatárok. Opcionális, hiányában a riport nem használ életkor-függő értelmezést |

### 5.2 Ténytáblák

| Tábla | Szemcsézettség | Főbb mérőszámok |
|---|---|---|
| `fact_daily_summary` | nap × forrás | lépés, távolság, aktív/össz kalória, emeletek, mérsékelt/intenzív percek, nyugalmi pulzus, napi min/max/átlag pulzus, átlagos stressz, Body Battery max/min (Garmin), SpO₂ átlag/min, légzésszám átlag |
| `fact_sleep_session` | alvási szakasz (session) × forrás | kezdet, vég, ágyban töltött idő, alvásidő, mély/könnyű/REM/ébrenlét perc, hatékonyság %, alvás-pontszám (ha van, forrásfüggő), átlagos pulzus, min. pulzus, légzésszám, SpO₂, átlagos stressz alvás közben, `sleep_date` |
| `fact_sleep_stage_interval` | alvásfázis-intervallum | kezdet, vég, fázis (a részletes hipnogramhoz) |
| `fact_hrv_nightly` | éjszaka × forrás | átlagos éjszakai HRV, `hrv_method` (**RMSSD** vagy **SDNN**), 7 napos átlag, eszköz-státusz/alapvonalsáv (ha van), mintaszám |
| `fact_heart_rate_sample` | perc (vagy a forrás felbontása) | pulzus bpm, kontextus (nyugalom/aktivitás/alvás) |
| `fact_stress_sample` | percszintű (Garmin) | stresszérték 0–100, kategória |
| `fact_activity` | edzés × forrás | típus, kezdet, időtartam, távolság, átlag/max pulzus, kalória, átlagsebesség/tempó, szint, edzéshatás (aerob/anaerob, ha van), terhelés (load, ha van), VO₂max-becslés, regenerációs idő (ha van), pulzus-visszaesés 1 perc |
| `fact_activity_hr_zone` | edzés × zóna | zónában töltött idő |
| `fact_body_measure` | mérés × forrás | testsúly, testzsír %, BMI (számolt), izom/víz (ha van) |
| `fact_vo2max` | dátum × forrás | VO₂max becslés |
| `fact_measurement` | **általános (EAV)**: metrika × időbélyeg × forrás | `metric_key`, érték, egység – az ismeretlen/új metrikák „menedékhelye" és a bővíthetőség záloga |

### 5.3 Kritikus modellezési döntések
1. **HRV nem összehasonlítható forrásközt.** A Garmin jellemzően RMSSD-szerű éjszakai átlagot, az Apple Watch **SDNN**-t ad. A `hrv_method` oszlop kötelező; a trendeket és riportot **módszerenként külön** kell kezelni, és forrásváltásnál a rendszer figyelmeztessen. Nincs automatikus konverzió.
2. **A stressz-pontszám Garmin-specifikus** (az Apple-nek nincs megfelelője). Apple adatok esetén a stressz-szekció „nincs adat" legyen, a rendszer ne próbálja pótolni pontatlan becsléssel (MAY: HRV-alapú, jól jelölt becsült „terhelési proxy").
3. **Alvásfázisok:** Apple „Core" ≈ Garmin „Light" leképezés a `dim_sleep_stage`-ben dokumentált; a fázis-százalékok forrásközi összevetése csak figyelmeztetéssel.
4. **Napi összesítők újraszámolhatók** a perc/mintaszintű adatokból, de ha a forrás saját napi összesítőt ad, az az elsődleges; eltérés esetén `quality_flag`.
5. **Helyi nap** a dimenziókulcs (`date_key` = helyi dátum).

### 5.4 Származtatott mutatók (derived layer, SQL view-k vagy materializált táblák)
- 7/28 napos mozgóátlag és mozgó medián minden fő metrikára,
- **személyes alapvonal**: a metrika 28–60 napos mozgó mediánja és MAD/szórása; napi **z-pontszám** az alapvonalhoz képest,
- alvási szabályosság (a lefekvés/ébredés időpontjának szórása 7/14 napon), alvási adósság (cél vs. tényleges, konfigurálható cél, alapérték 7,5 h),
- heti aktivitásvolumen: mérsékelt + 2×intenzív percek (WHO-logika szerinti „egyenértékes percek"),
- edzésterhelés-arány (akut:krónikus, 7 nap : 28 nap) – **SHOULD**, csak elegendő adat mellett, és figyelmeztetéssel, hogy módszertani közelítés,
- nyugalmi pulzus-trend, HRV-trend (ln(HRV) 7 napos átlag vs. 28 napos alapvonal),
- „regenerációs napok" száma, pihenőnapok szabályossága.

---

## 6. Funkcionális követelmények – alkalmazásoldalak

### 6.1 Navigáció (Streamlit többoldalas)
1. **Áttekintés** (kezdőlap)
2. **Import**
3. **Egészségügyi riport**
4. **Elemző (Explorer)**
5. **Adatminőség**
6. **Export**
7. **Beállítások**

### 6.2 Áttekintés (SHOULD)
Utolsó 7/30/90 nap kulcsmutatói kártyákon (nyugalmi pulzus, HRV, alvásidő, heti aktív percek, lépés, átlagos stressz), mindegyik az **alapvonalhoz mért eltéréssel** és mini-trendgörbével; adatfrissesség jelzése („utolsó adat: …").

### 6.3 Beállítások (MUST)
- időzóna, alvásnap-konvenció, alvási cél,
- profil (opcionális): születési év, nem, magasság, max. pulzus, zónahatárok,
- elsődleges forrás metrikánként,
- mérőszám-küszöbök (a riportszabályok paraméterei, lásd 7.3),
- adatbázis helye, biztonsági mentés és **teljes adat törlése** gomb (megerősítéssel).

---

## 7. Átfogó egészségügyi elemzés (riportmotor)

### 7.1 Működés
- Paraméterek: időszak (alapértelmezés: utolsó 90 nap; választható 30/90/180/365 nap vagy egyéni), összehasonlító időszak (előző azonos hosszúságú), forrás.
- A riport **determinisztikus, szabályalapú**: számított mutatók → szabályok → szöveges megállapítások sablonból. **MUST NOT**: LLM/külső API hívás az elemzéshez (adatvédelem és reprodukálhatóság miatt). A szöveg magyar, tárgyilagos, számokkal alátámasztott.
- Minden megállapításhoz tartozik: **mért érték**, **összehasonlítási alap** (saját alapvonal és/vagy általános irányadó tartomány), **bizonyossági szint** (magas/közepes/alacsony – az adatmennyiség és a zaj alapján), és a hivatkozott grafikon.
- Elegendő adat hiányában (pl. < 14 érvényes nap) a szekció „nincs elegendő adat" jelzést kap, nem következtet.

### 7.2 Riport felépítése

1. **Címoldal és összefoglaló (Vezetői összegzés):** időszak, adatforrás(ok), adatlefedettség %, 5–8 legfontosabb megállapítás (pozitív és figyelmet igénylő), „mi változott az előző időszakhoz képest".
2. **Alvás:** átlagos alvásidő és eloszlása, alvásidő-cél teljesülése (napok %-a), alvási szabályosság (lefekvés/ébredés szórása), fázisarányok (mély/REM/könnyű – forrásfüggő jelöléssel), ébrenlét, hétköznap–hétvége különbség („social jet lag"), alvási adósság, trend, alvás és másnapi mutatók (HRV, nyugalmi pulzus) kapcsolata.
3. **Szív és HRV:** nyugalmi pulzus trend és alapvonaltól való eltérések, éjszakai HRV (módszer megjelölve), 7 napos átlag vs. alapvonal, tartós eltérések (pl. ≥ 5 egymást követő nap az alapvonal sávján kívül), pulzus-visszaesés edzés után (ha van), max/átlag pulzus eloszlás.
4. **Stressz és regeneráció** (Garmin): napi átlagos stressz, magas stresszű percek aránya, Body Battery töltődés éjszaka (ha van), stressz × alvás összefüggés. Apple adatnál a szekció nem jelenik meg vagy csak a HRV-alapú rész.
5. **Fizikai aktivitás és edzés:** heti aktív percek (WHO-irányadó: felnőtteknek heti 150–300 mérsékelt vagy 75–150 intenzív perc + heti 2+ erősítő nap – a küszöbök paraméterezhetők), lépés-trend, edzéstípusok megoszlása, edzésgyakoriság, terhelés-alakulás, pihenőnapok, VO₂max-trend, aerob vs. erősítő arány, zónaeloszlás (polarizált/küszöb/ülő jelleg megjelölése).
6. **Légzés és SpO₂** (ha van): éjszakai SpO₂ átlag/minimum, alacsony értékű epizódok száma – csak leíró jelleggel, **nem diagnosztikus megfogalmazással**.
7. **Testösszetétel** (ha van): súly és testzsír trend.
8. **Összefüggések (korrelációk):** előre definiált, interpretálható kapcsolatok (pl. alvásidő → másnapi HRV; késői/intenzív edzés → alvás; stressz → alvásminőség), Spearman-korrelációval, hatásmérettel és mintaszámmal; **a szöveg mindig jelezze, hogy korreláció ≠ ok-okozat**.
9. **Anomáliák és figyelmeztetések:** a személyes alapvonaltól tartósan eltérő időszakok, kiugró napok, adathiányos időszakok.
10. **Fókuszterületek:** legfeljebb 3–5 adatvezérelt, tárgyilagos „figyelendő terület" (nem előírás), pl. „Az alvás kezdete a mérési időszakban hétköznap–hétvége között átlagosan X perccel eltolódik."
11. **Módszertan és korlátok:** adatforrások, számítási módszerek, küszöbforrások, az eszközök pontosságának korlátai, a HRV/alvásfázis-különbségek, a fogyasztói okosóra-adatok korlátai.
12. **Jogi/orvosi nyilatkozat** (7.6).

### 7.3 Szabálykatalógus (konfigurálható `config/rules.yaml`)
A szabályok YAML-ban legyenek, ne kódban: azonosító, metrika, feltétel, küszöb, súlyosság (info / figyelmet érdemel / orvosi konzultáció megfontolandó), szövegsablon, hivatkozás a forrásra (pl. WHO 2020 fizikai aktivitási irányelvek; AASM/SRS konszenzus a felnőttek ≥ 7 órás alvásáról; AHA/ESC irányadó értékek). Szabálypéldák:
- Átlagos alvásidő < 7 óra az időszak > 50 %-ában → „figyelmet érdemel".
- Nyugalmi pulzus ≥ 7 napja ≥ 1,5 szórással az alapvonal felett → „figyelmet érdemel (lehetséges terhelés, betegség, alváshiány – az adat önmagában nem ad magyarázatot)".
- Nyugalmi pulzus tartósan > 100 bpm vagy tartósan nagyon alacsony (< 40) tünetekkel, illetve ismétlődő magas pulzus pihenés közben → csak **általános jelzés**, hogy érdemes orvossal beszélni, ha tünetei vannak; a rendszer ne magyarázzon diagnózist.
- Heti aktív percek < 150 mérsékelt-egyenértékes → „az irányadó érték alatt".
- Túl nagy heti terhelésugrás (az akut:krónikus arány > konfigurált küszöb) → „sérülési/túlterheltségi kockázat megfontolandó" – közelítésként jelölve.

> A küszöbök és szövegek **orvosi felülvizsgálatra szoruló** tartalmak: a kódban ne legyenek elrejtve, és a `rules.yaml`-ban legyen `reviewed_by` és `reviewed_at` mező, amelyet a termék tulajdonosa tölt ki.

### 7.4 Vizualizációk a riportban (Plotly → statikus PNG/SVG a PDF-hez)
Idősoros trendek mozgóátlaggal és alapvonal-sávval, hipnogram (típusos éjszaka), alvásfázis-halmozott oszlop, heti aktív percek a célvonallal, naptár-hőtérkép (alvásidő/lépés/stressz), eloszláshisztogram/boxplot, szóródási diagram regressziós vonallal (korrelációknál), edzéstípus-megoszlás, pulzuszóna-eloszlás.

### 7.5 Reprodukálhatóság
A riport elmenthető „pillanatképként" (paraméterek + adatverzió + szabálykatalógus-verzió + időbélyeg), és ugyanazokkal a bemenetekkel azonos kimenetet ad. A PDF láblécében: generálás ideje, adatintervallum, verziószámok.

### 7.6 Kötelező nyilatkozat (PDF-ben és a UI-ban is)
„Ez a jelentés fogyasztói okosóra adatain alapuló, tájékoztató jellegű elemzés. Nem orvosi diagnózis, nem helyettesíti az orvosi vizsgálatot vagy tanácsot. Az eszközök mérési pontossága korlátozott. Ha panaszai vannak, vagy az értékek aggasztják, forduljon orvoshoz; sürgős tünet esetén hívja a 112-t."

---

## 8. Interaktív elemző felület (Explorer)

### 8.1 Alapelv: szemantikus réteg
Az Explorer **nem szabad SQL-t** kér alapból; a felhasználó **mérőszámokat** (measures) és **dimenziókat** választ egy YAML-ban definiált szemantikus rétegből (`config/semantic.yaml`), a rendszer generálja a biztonságos, paraméterezett SQL-t. A szemantikus réteg definiálja: ténytáblák, mérőszámok (név, képlet, aggregáció, egység), dimenziók (tábla, attribútumok, hierarchiák: pl. év → negyedév → hónap → hét → nap), megengedett join-ok.

### 8.2 Elemzés-építő (MUST)
1. **Adatforrás (téma) választása:** alvás, napi összesítő, edzés, HRV, pulzus/stressz (intraday) stb.
2. **Mérőszámok:** 1–4 db, aggregációval (összeg, átlag, medián, min, max, szórás, percentilis, darabszám).
3. **Csoportosítás (dimenziók):** 0–3 db (pl. hét napja, hónap, edzéstípus, napszak, forrás).
4. **Szűrők:** időszak (előbeállítások + egyéni), dimenzió-attribútum szűrők, érték-tartomány szűrők, „csak minőségileg rendben lévő adat" kapcsoló.
5. **Időbeli felbontás:** napi / heti / havi / negyedéves / éves.
6. **Átalakítások:** mozgóátlag (N nap), kumulatív összeg, változás az előző időszakhoz (abszolút, %), indexálás (alapvonal = 100), z-pontszám, normalizálás (min–max), differenciálás.
7. **Grafikon típusa:** vonal, oszlop (csoportosított/halmozott), terület, szóródás (opcionális színezés/mérettel), buborék, hisztogram, boxplot, hegedű (violin), hőtérkép (óra×nap, hét napja×hét), naptár-hőtérkép, korrelációs mátrix, radar (összehasonlító profilok), edzés-szakaszok idősora.
8. **Két tengely** (pl. alvásidő és másnapi HRV) és kisebb panelek (small multiples) egy dimenzió szerint.
9. **Összehasonlító mód:** két időszak (pl. e hónap vs. előző; „munkás" vs. „szabadságos" időszak, saját címkézett időszakok) egymás mellett, különbségi mutatóval.
10. **Eltolás/lag-elemzés:** mérőszám X napokkal eltolva egy másikhoz (pl. edzésterhelés → másnapi alvás), korrelációs együttható (Pearson és Spearman) a mintaszámmal és p-értékkel/konfidenciaintervallummal, a korreláció ≠ ok-okozat jelzéssel.
11. **Annotációk:** a felhasználó saját **eseményeket** rögzíthet (pl. utazás, betegség, kávémentes hét, új edzésterv, gyógyszer/étrend-változás szabad szöveggel) `dim_event` táblába; az események vízszintes sávként/jelölőként megjelennek a grafikonokon, és szűrőként/dimenzióként használhatók („betegség idején vs. egyébként"). *Az események szabadszöveges adatok, helyi tárolásúak.*
12. **Táblázat nézet** az eredményről (rendezhető, szűrhető, oszlopválasztó), és az alapjául szolgáló **generált SQL megtekintése** (csak olvasható, információs célra).

### 8.3 Mentett elemzések (MUST)
- Az elemzés teljes definíciója JSON/YAML-ban elmenthető névvel, címkékkel, leírással; újra megnyitható, duplikálható, törölhető.
- Mentett elemzések **műszerfal-szerűen** (több grafikon egy oldalon, „Saját nézetek") csoportosíthatók (SHOULD).
- A mentett elemzés a riportba **melléklet-szekcióként** beilleszthető (SHOULD).

### 8.4 Haladó mód (MAY)
Csak olvasható SQL-konzol a csillagséma ellen (`SELECT` / `WITH` engedélyezett; DDL/DML tiltott, időkorlát és sorlimit), a séma böngészővel (táblák, oszlopok, leírások).

### 8.5 Teljesítményi célok
- Napi aggregátumok lekérdezése 5 év adaton: < 1 s.
- Percszintű (intraday) adatokra szűkített lekérdezés: < 3 s. Intraday adat grafikonra max. ~20 000 pontot jelenítsen meg (automatikus mintavételezés/LTTB-lecsökkentés), a tooltip mutassa az eredeti értéket.
- Az Explorer ne töltse be a teljes adatbázist memóriába; az aggregálás DuckDB-ben történjen.

---

## 9. Export követelmények

### 9.1 PDF (MUST)
- **Riport-PDF:** a 7.2 szerinti felépítés, címoldallal, tartalomjegyzékkel, oldalszámozással, fejléccel/lábléccel, magyar ékezetes betűtípus beágyazásával (pl. DejaVu Sans/Noto Sans), A4 álló, nyomtatásbarát (világos háttér), grafikonok vektoros (SVG) vagy ≥ 200 dpi raszteres formában.
- **Egyedi elemzés-PDF:** az Explorerben összeállított egy vagy több grafikon + adattábla + a szűrők/paraméterek összefoglalója + a felhasználó megjegyzése (szabad szöveg).
- A PDF tartalmazza a metaadatokat (időszak, forrás, verzió), és a 7.6 nyilatkozatot.

### 9.2 XLSX (MUST)
- **Nyers adat export:** választható ténytáblák/dimenziók/időszak szerint; minden tábla külön munkalap; fejléc rögzítve, szűrő bekapcsolva, oszlopszélesség, helyes dátum/idő formátum, mértékegység a fejlécben.
- **Elemzési eredmény export:** az Explorerben látott aggregált táblázat + a definíció (paraméterek) külön „Leírás" munkalapon.
- **Adatszótár** munkalap (dim_metric alapján: mi mit jelent, mértékegység, forrás).
- Excel sorlimit (1 048 576) kezelése: figyelmeztetés és automatikus darabolás vagy CSV/Parquet alternatíva felajánlása.
- **SHOULD:** CSV és Parquet export ugyanazon a felületen.

### 9.3 Exportok kezelése
Export oldal: előzmények (mikor, mit exportáltam), újragenerálás ugyanazon paraméterekkel, a fájlok az `exports/` mappába kerülnek és letölthetők a UI-ból.

---

## 10. Nem funkcionális követelmények

### 10.1 Adatvédelem és biztonság (MUST)
- Az egészségügyi adat **különleges kategóriájú személyes adat** (GDPR 9. cikk). Az alkalmazás **kizárólag helyben fut**: nincs telemetria, nincs külső API-hívás az adatokkal, nincs CDN-ről betöltött kód futás közben (az összes függőség helyben telepített).
- A hálózati kapcsolat nélkül is működnie kell (a telepítés után).
- Streamlit csak `localhost`-ra kössön (`--server.address 127.0.0.1`).
- Az adatbázisfájl és az exportok az **adatmappában** legyenek (konfigurálható; `.gitignore`-ban). Az adatot sosem szabad verziókezelésbe tenni.
- A napló (log) **ne tartalmazzon egészségügyi értékeket**, csak technikai információt.
- SHOULD: opcionális adatbázis-titkosítás (pl. DuckDB titkosítás, ha elérhető, vagy titkosított lemezkötet ajánlása a dokumentációban).
- Teljes adattörlés funkció (a UI-ból és CLI-ből).

### 10.2 Megbízhatóság és hibakezelés
- Hibás/részleges import nem hagyhatja félig betöltött állapotban az adatbázist: **tranzakció/batch-szintű rollback**.
- Hibák felhasználóbarát magyar üzenettel; részletek a logban.
- Szakadt XML/ZIP, ismeretlen séma, hiányzó fájl: kezelt eset, nem összeomlás.

### 10.3 Teljesítmény
- 1 GB Apple `export.xml` importja ésszerű időn belül (cél: < 10 perc modern laptopon), progress barral.
- Újraimport/rebuild: az érintett időszakra korlátozható (inkrementális), nem kell mindig mindent újraépíteni.

### 10.4 Tesztelhetőség és minőség (MUST)
- **Szintetikus tesztadat-generátor** (`tools/make_sample_data.py`): kicsi, valósághű Apple- és Garmin-szerű minta (ismert „beépített" mintázatokkal – pl. rossz alvású időszak, edzésblokk, betegségi időszak), hogy a parserek, a mutatók és a riportszabályok ellenőrizhetők legyenek. **Valódi személyes adatot tesztfixture-nek tilos a repóba tenni.**
- Egységtesztek: parserek (golden file-ok), időzóna/DST, deduplikálás, alvásnap-hozzárendelés, derived metrics, szabálymotor, export (fájl megnyitható, lapok és fejlécek helyesek).
- Integrációs teszt: minta-import → rebuild → riport PDF generálás → XLSX export végig.
- Kódlefedettség cél: ≥ 80 % a parser, transzformációs és szabálymotor modulokon.
- A numerikus számítások (alapvonal, z-pont, korreláció) független referencia-implementációval (pl. pandas/scipy) legyenek ellenőrizve.

### 10.5 Használhatóság
- Magyar felület (alapértelmezett), világos és sötét téma, mobilbarát nem követelmény, de 1366 px-es laptop képernyőn jól használható legyen.
- Üres állapotok (még nincs adat) érthető útmutatóval: „Hogyan exportálj Apple Health / Garmin adatot".
- Minden szám mellett mértékegység; dátumformátum `ÉÉÉÉ.HH.NN.`, 24 órás idő, tizedesvessző a megjelenítésben.

### 10.6 Hordozhatóság és üzemeltetés
- Windows/macOS/Linux; Python 3.12; egyetlen parancsos telepítés (`uv sync`).
- README: telepítés, export-útmutatók (Apple, Garmin), első lépések, hibaelhárítás.
- Verziózott adatbázis-migrációk (`sql/migrations/NNN_*.sql`) és migrációkezelő; sémaverzió a `meta` táblában.
- Biztonsági mentés: egy gombnyomásos másolat az adatbázisról.

---

## 11. Javasolt projektstruktúra

```
vitalmart/
├─ pyproject.toml
├─ README.md
├─ SPEC.md
├─ CLAUDE.md
├─ config/
│  ├─ app.yaml              # időzóna, útvonalak, alvási cél
│  ├─ garmin_mapping.yaml   # Garmin mezőleképezés
│  ├─ apple_mapping.yaml    # HK típusok → metrika-kódok
│  ├─ semantic.yaml         # mérőszámok, dimenziók, join-ok
│  └─ rules.yaml            # riportszabályok, küszöbök
├─ sql/
│  └─ migrations/
├─ src/vitalmart/
│  ├─ importers/            # apple_health.py, garmin_json.py, garmin_fit.py, base.py
│  ├─ transform/            # staging → core, dim betöltés, derived metrics
│  ├─ datamart/             # DuckDB kapcsolat, migrációk, lekérdező réteg
│  ├─ semantic/             # YAML → SQL generátor
│  ├─ analytics/            # alapvonal, trend, korreláció, szabálymotor
│  ├─ report/               # sablonok (Jinja2), PDF építés, grafikon-renderelés
│  ├─ export/               # xlsx, csv, parquet
│  ├─ i18n/                 # hu.yaml, en.yaml
│  └─ cli.py
├─ app/
│  ├─ main.py               # Streamlit belépési pont
│  └─ pages/                # 1_Attekintes.py … 7_Beallitasok.py
├─ tools/make_sample_data.py
├─ tests/
└─ data/                    # .gitignore! (db, exports, uploads)
```

---

## 12. Mérföldkövek (Claude Code számára, sorrendben)

| # | Mérföldkő | Elfogadási kritérium |
|---|---|---|
| **M0** | Projektváz, konfig, migrációkezelő, CI-szerű `pytest`/`ruff` futtatás, szintetikus adatgenerátor | `uv run pytest` zöld; minta-adat generálható |
| **M1** | Apple Health import (streaming), staging + core betöltés, dim_date/metric/source/activity_type | minta `export.xml` importja; kétszeri betöltés nem duplikál; DST-teszt zöld |
| **M2** | Garmin JSON + FIT import, mezőleképezés YAML-ból, ismeretlen mezők megőrzése | Garmin minta betöltve; ismeretlen mező a `fact_measurement`-ben landol |
| **M3** | Derived metrics (alapvonal, z-pont, mozgóátlag, alvási szabályosság, aktív percek), adatminőségi ellenőrzések | derived értékek egyeznek a pandas/scipy referenciával |
| **M4** | Streamlit váz, Import oldal, Áttekintés, Adatminőség, Beállítások | teljes import a UI-ból, előnézettel és összefoglalóval |
| **M5** | Szemantikus réteg + Explorer (építő, szűrők, grafikon-típusok, átalakítások, mentés) | legalább 10 előre definiált mentett elemzés működik; generált SQL megtekinthető |
| **M6** | Riportmotor (szabálykatalógus, szekciók, ábrák) HTML-ben | minden szekció megjelenik a mintán; „nincs elegendő adat" ágak tesztelve |
| **M7** | PDF és XLSX/CSV/Parquet export, export-előzmények | PDF magyar ékezetekkel, tartalomjegyzékkel; XLSX több munkalappal, adatszótárral |
| **M8** | Haladó funkciók: események/annotációk, lag-korreláció, összehasonlító mód, saját nézetek, (MAY) SQL-konzol | elfogadási tesztek az Explorerre |
| **M9** | Kemény tesztelés valós (a felhasználó saját) exporton, teljesítményhangolás, dokumentáció | 1 GB Apple export < 10 perc; README kész |

Minden mérföldkő végén: rövid összefoglaló, futtatási útmutató, ismert korlátok listája.

---

## 13. `CLAUDE.md` – másolható szabályok az implementálónak

```markdown
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
```

---

## 14. Nyitott kérdések (a termék tulajdonosának)

1. A Garmin Venu Sq 2 pontos exportmintájának (mappaszerkezet, néhány JSON-fájl anonimizált szerkezete) megadása a parser pontosításához – az M2 előtt érdemes egy valódi export szerkezetét (fájlnevek, mezőnevek, **értékek nélkül**) megmutatni.
2. Apple-oldalon: pontos eszköz-/szoftververzió, mert az elérhető mérőszámok (pl. alvásfázisok, SpO₂, hőmérséklet) ettől függnek.
3. Szükséges-e az elsődleges forrás egyidejű, párhuzamos használata (Garmin + Apple egy időszakban), vagy váltás történik?
4. Orvosi felülvizsgálat: kéri-e a szabálykatalógus és a küszöbök szakmai átnézését (a `reviewed_by` mezőn keresztül) a „élesítés" előtt?
5. Szükséges-e angol nyelvű riport is, vagy csak magyar?
