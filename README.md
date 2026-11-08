# VitalMart

Helyben futó **okosóra-adatpiac és egészségügyi elemző** alkalmazás
(Apple Watch / Apple Health, Garmin Venu Sq 2). Egy háztartás több személyének adatai
külön kezelhetők (személy = elemzési dimenzió, nem felhasználói fiók). A követelmények forrása a
[`SPEC.md`](SPEC.md), a fejlesztési szabályok a [`CLAUDE.md`](CLAUDE.md)-ben vannak.

> **Adatvédelem:** minden adat kizárólag a gépeden, az adatmappában (`data/`, gitignore-olva)
> marad. Nincs telemetria, nincs külső hívás. A napló nem tartalmaz egészségügyi értékeket.

> Ez az alkalmazás nem orvosi eszköz, és nem diagnosztizál (SPEC 7.6).

Állapot: a SPEC 12. pontjának mind a tíz mérföldköve (M0–M9) elkészült; 201 automatikus teszt,
96 %-os kódlefedettség, valós Apple-exporton végigtesztelve.

## Gyors kezdés

Ha van **Node.js** (18+) és **Git** a gépeden, ugyanúgy indítható, mint egy Node-projekt:

```bash
git clone -b claude/funny-keller-v2yxsd https://github.com/kissm1985/smartwatch-analyzer.git vitalmart
cd vitalmart
npm install        # első alkalommal: telepíti az uv-t (ha nincs) és a Python-környezetet
npm run dev        # elindul a felület, és megnyílik a böngészőben: http://127.0.0.1:8501
```

Frissítés később: `git pull`, `npm install`, `npm run dev`. Leállítás: Ctrl+C.
(Az npm itt csak indító: az alkalmazás Pythonban fut, a függőségeket az [uv](https://docs.astral.sh/uv/)
kezeli; npm-csomag nem települ.)

| Parancs | Mit csinál |
|---|---|
| `npm run dev` | felület indítása + böngésző megnyitása |
| `npm start` | felület indítása böngésző nélkül |
| `npm run import -- --person me garmin.zip` | export betöltése parancssorból (amíg a felület nem fut) |
| `npm run sample` | szintetikus mintaadat létrehozása és betöltése (kipróbáláshoz) |
| `npm run diagnose -- garmin.zip --person me --out diag.md` | megosztható, értékek nélküli önellenőrzés |
| `npm run backup` | adatbázis-mentés |
| `npm run cli -- <parancs>` | bármely `vitalmart` parancs (pl. `npm run cli -- report --pdf`) |
| `npm test`, `npm run lint` | tesztek, lint |

Node nélkül ugyanez közvetlenül: `uv sync`, majd `uv run vitalmart ui --open`.

## Telepítés

Szükséges: **Python 3.12+** és az [uv](https://docs.astral.sh/uv/) csomagkezelő. Rendszerkönyvtár,
böngésző-motor vagy külső szolgáltatás nem kell (a PDF is tisztán Pythonból készül).

| Rendszer | uv telepítése |
|---|---|
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

```bash
git clone <repo-url> vitalmart && cd vitalmart      # vagy a ZIP kicsomagolása
uv sync                                              # függőségek a projekt .venv-jébe (Python is, ha kell)
uv run vitalmart init                                # adatmappa + adatbázis (az első import is létrehozza)
```

A telepítés után **hálózat nélkül** is működik. Frissítés: `git pull && uv sync` – az
adatbázis-migrációk (`sql/migrations/`) az első indításkor automatikusan lefutnak, a séma
verziója a `schema_migrations` táblában van. Az adatmappa helye: `config/app.yaml` →
`data_dir` vagy a `VITALMART_DATA_DIR` környezeti változó (alapértelmezés: `data/`).

## Grafikus felület

```bash
uv run vitalmart ui            # vagy: uv run streamlit run app/main.py
```

Megnyitás: <http://127.0.0.1:8501>. A felület **csak a helyi gépen** érhető el
(`.streamlit/config.toml`: `address = "127.0.0.1"`, telemetria kikapcsolva; ha mégis más
címre kötnéd, az alkalmazás nem indul el). Világos/sötét téma a rendszer szerint, a jobb felső
menüben váltható.

| Oldal | Tartalom |
|---|---|
| Áttekintés | személyenként 7/30/90 nap kulcsmutatói (nyugalmi pulzus, HRV a módszerrel, alvásidő, WHO-percek, lépés, stressz) a megelőző 60 nap átlagához mért eltéréssel, mini-trenddel; utolsó adat dátuma, források |
| Import | feltöltés vagy helyi útvonal, személy, automatikus/kézi forrásfelismerés, **előnézet** (fájlok, rekordtípusok, dátumtartomány, figyelmeztetések) → megerősítés → haladásjelző → **összefoglaló**; importnapló |
| Adatminőség | lefedettség (havi hőtérkép), hézagok, megjelölt értékek, nem viselés, forrásváltások |
| Beállítások | időzóna, nyelv, alvásnap-konvenció, alvási cél, alapvonal; személyek neve és profilja (érvényességi időszakokkal, pulzuszónák); elsődleges forrás metrikánként; riportküszöbök; mentés, újraszámolás, **teljes törlés** (megerősítéssel) |
| Elemző | lásd lent |
| Haladó elemzés | eltolásos (lag) korreláció és összehasonlító mód – lásd lent |
| Saját nézetek | mentett elemzések műszerfalként (1–3 oszlop), közös időszak/személy felülírással |
| Események | saját események/időszakok rögzítése, szerkesztése, törlése |
| SQL-konzol | csak olvasható lekérdezések a csillagsémán, séma-böngészővel |
| Egészségügyi riport | személy + időszak (30/90/180/365 nap vagy egyéni) → szabályalapú HTML-riport, letöltés HTML-ben és **PDF-ben**, korábbi riportok újragenerálása |
| Export | nyers és származtatott táblák XLSX / CSV / Parquet formátumban (személy, időszak), **exporttörténet** letöltéssel, újragenerálással, törléssel |

A Beállításokban tett módosítások a `data/settings.yaml`-ba kerülnek (gitignore-olt), a
`config/*.yaml` alapértékeit nem írják felül. A feltöltött fájl a betöltés után törlődik.

> Amíg a felület fut, a parancssori `vitalmart import` nem éri el az adatbázist (DuckDB-zár) –
> importálj a felületről, vagy előbb zárd be.

## Elemző (Explorer)

Szabad SQL nélkül, egy **szemantikus rétegből** (`config/semantic.yaml`) választasz:

- **téma** (10): napi mutatók (elsődleges forrás), alvás, edzések, HRV (módszerenként – SDNN és RMSSD
  sosem keveredik, a módszert csoportosítani vagy egy értékre szűrni kötelező), napon belüli pulzus,
  napon belüli stressz, heti WHO-aktivitás, edzésterhelés/ACWR, személyes alapvonal és z-pont, testösszetétel;
- **1–4 mérőszám** aggregációval (összeg, átlag, medián, min., max., szórás, percentilisek, darab, nyers);
- **0–3 dimenzió** (dátum: év → negyedév → hónap → hét → nap, a hét napja, hétvége, munkanap, évszak;
  személy, forrás, gyártó; napszak, óra; edzéstípus; pulzus-kontextus; stressz-kategória; HRV-módszer);
- **szűrők**: időszak (utolsó 7/30/90/180/365 nap – az utolsó adatnaphoz viszonyítva –, teljes, egyéni),
  személyek, dimenzióértékek (be/ki), érték-tartomány, „csak rendben lévő adat”;
- **időfelbontás**: napi/heti/havi/negyedéves/éves, napon belüli témáknál óránkénti és nyers;
- **átalakítás** sorozatonként: mozgóátlag, kumulatív összeg, változás (abs./%), index (=100), z-pont, 0–1 normálás;
- **grafikon**: vonal, oszlop (csoportosított/halmozott), terület, szóródás (illesztett egyenessel), buborék,
  hisztogram, boxplot, hegedű, hőtérkép, naptár-hőtérkép, korrelációs mátrix (Spearman/Pearson, n-nel),
  radar, idővonal; **második tengely**, **kisebb panelek** dimenzió szerint;
- **táblázat** (rendezhető, oszlopválasztóval) és a **generált SQL** (csak olvasható; a felhasználói
  értékek mindig paraméterként mennek, azonosító csak a YAML-ból kerülhet bele).

Napon belüli adat vonaldiagramon legfeljebb 20 000 pont (LTTB-ritkítás). Mérve: 5 év napi adat
csoportosított lekérdezése < 1 s, 1 év percenkénti pulzus óra × hét napja hőtérképe < 3 s.

**Mentett elemzések**: 17 beépített (🔒, `config/saved_analyses.yaml`) – megnyitás, duplikálás; saját
elemzés mentése névvel, leírással, címkékkel, felülírás, törlés; definíció letöltése/betöltése JSON-ként.
Ha egy elemzés nem bontja személyek szerint, a kiválasztott személyre szűkítve fut (különben két ember
napi adatai összekeverednének).

## Haladó funkciók

**Események / annotációk** (Események oldal, `dim_event`): megnevezés, kategória (javaslatokból vagy
saját), kezdő- és utolsó nap, szabad szöveges megjegyzés; egy személyre vagy az egész háztartásra.
Csak helyben tárolódnak. Megjelenésük:
- az Elemző idővonalas ábráin (és PDF-exportjában) **sávként** a megnevezéssel (kikapcsolható);
- **dimenzióként / szűrőként** minden napi és napon belüli témában: „Esemény”, „Esemény-kategória”,
  „Esemény alatt / egyébként” (pl. nyugalmi pulzus betegség idején vs. egyébként). Egymást átfedő
  események címkéi összefűzve jelennek meg; a háztartási esemény mindenkire vonatkozik (`event_day` nézet).

**Eltolásos (lag) korreláció** (Haladó elemzés): két napi mérőszám bármely témából (pl. edzésterhelés →
következő éjszaka alvásideje), −30…+30 napos eltolásprofil, Pearson és Spearman együttható mintaszámmal,
p-értékkel és 95%-os konfidenciaintervallummal (Fisher-z; Spearmannél 1,06/(n−3) variancia), szóródási
diagram illesztett egyenessel és tárgyilagos, leíró összefoglaló mondattal – mindig „a korreláció nem
jelent ok-okozati összefüggést” jelzéssel. Konvenció: X a t. napon, Y a t + eltolás. napon (a riport
összefüggés-szekciójával azonos).

**Összehasonlító mód**: ugyanaz az elemzés (téma, 1–4 mérőszám, 0–1 bontás) két időszakra: utolsó N nap vs.
előző N nap, e hónap vs. előző, egy évvel korábbi azonos időszak, két egyéni időszak, vagy **egy esemény vs.
az azt megelőző azonos hosszú időszak**. Mutatók: A, B, különbség (B − A), változás %, mintaszám, és napi
értékeknél standardizált különbség (Cohen-féle d, leíró jelleggel); csoportosított oszlop- és napra
igazított vonaldiagram.

**Saját nézetek** (`saved_view`): több mentett elemzés egy oldalon, sorrendben, 1–3 oszlopban; opcionálisan
közös időszak és személy(ek), amelyek felülírják az elemzések saját beállítását. Ha egy elemzést
időközben törölsz, a nézet többi része továbbra is megjelenik.

**SQL-konzol** (SPEC 8.4, MAY): egyetlen `SELECT` / `WITH` a csillagséma tábláira (`dim_*`, `fact_*`,
`derived_*`, `dq_*`, `event_day`). Védelem: a DuckDB elemzője szerint csak SELECT; az AST-ből kigyűjtött
táblák/függvények engedélylistán (staging-tábla, fájl, más adatbázis, `read_csv` stb. tiltva); külön
kurzoron, mindig visszagörgetett tranzakcióban, 15 s időkorláttal és 10 000 soros limittel; eredmény
CSV-ben letölthető. Séma-böngésző az oszlopok leírásával (`config/export_tables.yaml` oszlopszótára).

## Egészségügyi riport

Determinisztikus, **szabályalapú** riport (nincs LLM, nincs külső hívás). A küszöbök, a szövegek és a
forráshivatkozások kizárólag a `config/rules.yaml`-ban vannak; a paraméterek a Beállításokban
felülírhatók. A tulajdonos döntése alapján a küszöbök **szakmai felülvizsgálata nem történt meg**
(`review_status: not_requested`) – ezt a riport Módszertan szekciója is jelzi.

Felépítés (SPEC 7.2): címoldal és vezetői összegzés (legfontosabb megállapítások, változás az előző
azonos hosszú időszakhoz) · alvás · szív és HRV (módszerenként) · stressz és regeneráció (csak
Garmin; Apple-nél indoklással kimarad) · aktivitás és edzés (WHO 150/300 perc, heti 2 erősítő nap,
zónák, ACWR-közelítés) · légzés és SpO₂ (csak leíró) · testösszetétel · összefüggések (Spearman, n,
p, erősség, „a korreláció nem ok-okozat”) · anomáliák · fókuszterületek (max. 5) · módszertan és
korlátok (küszöbtáblával) · nyilatkozat.

Minden megállapításnál: mért érték, összehasonlítási alap (küszöb / személyes alapvonal / előző
időszak), **bizonyossági szint** (magas: ≥ 80 % lefedettség és ≥ 28 érték; közepes: ≥ 50 % és a minimum;
egyébként alacsony) és a hivatkozott ábra. 14 érvényes napnál kevesebbnél a szekció „nincs elegendő
adat” jelzést ad, nem következtet.

Szabálytípusok: arány küszöb alatt/fölött, átlag küszöb fölött/alatt, leghosszabb egymást követő
z-eltérés, heti átlag, napok küszöb fölött, darab küszöb alatt, változás az előző időszakhoz
(abs./%), változás az időszakon belül, alvásszabályosság, „social jet lag”.

**Reprodukálhatóság**: minden riport pillanatképként (`report_snapshot`) mentődik: paraméterek,
adatverzió (tartalom-hash), szabálykatalógus-verzió, időbélyeg. Újragenerálásnál változatlan
adat és szabályok mellett a HTML **bájtra azonos**; a lábléc tartalmazza a verziókat. A Plotly a
fájlba ágyazva, CDN nélkül (kb. 5 MB / riport).

## Export

Minden export a helyi adatmappa `exports/` alkönyvtárába kerül (`reports/`, `analysis/`, `raw/`),
és az `export_history` táblába naplózódik a paraméterekkel – az Export oldal „Exporttörténet”
lapján letölthető, **ugyanazokkal a paraméterekkel újragenerálható** vagy törölhető.

| Mit | Formátum | Tartalom |
|---|---|---|
| Egészségügyi riport | PDF (A4) | borító, **tartalomjegyzék** (oldalszámokkal + PDF-könyvjelzők), fejléc/lábléc az időszakkal és a verziókkal, „N / M. oldal”, minden szekció, ábrák 200 dpi PNG-ként, módszertan, küszöbök, nyilatkozat; beágyazott DejaVu Sans (magyar ékezetek, SpO₂); PDF-metaadatok. Azonos adatnál **bájtra azonos** újragenerálás. |
| Elemző-eredmény | PDF | cím, saját megjegyzés, ábra, paraméter-összefoglaló, táblázat (max. 400 sor, felette utalás az XLSX/CSV-re), SQL, nyilatkozat |
| Elemző-eredmény | XLSX | „Eredmény” lap (mértékegység a fejlécben) + „Leírás” lap (definíció, paraméterek, megjegyzés, SQL, JSON) |
| Elemző-eredmény | CSV | UTF-8, mértékegység a fejlécben |
| Nyers / származtatott adat | XLSX | „Információ” lap, táblánként egy munkalap (rögzített fejléc, autoszűrő, oszlopszélesség, dátum- és időformátum, magyar fejléc mértékegységgel), **„Adatszótár”** (minden oszlop: technikai név, egység, típus, leírás), „Mérőszámok” (dim_metric) |
| Nyers / származtatott adat | CSV / Parquet | táblánként egy fájl (technikai oszlopnevek); több tábla esetén ZIP `adatszotar.csv`-vel |

Az exportálható táblák, a lekérdezésük és az oszlopszótár a `config/export_tables.yaml`-ban
vannak (a staging-táblák szándékosan nem exportálhatók). Az Excel munkalaponkénti sorlimitje
(1 048 575 adatsor) fölött a tábla több munkalapra bomlik („Pulzusminták (2)” …), erről a
fájlban és a felületen figyelmeztetés jelenik meg; nagy táblákhoz a CSV vagy a Parquet ajánlott.

Az ábrák a PDF-hez matplotlibbel renderelődnek a Plotly-ábrákból (nincs szükség böngészőre
vagy rendszerkönyvtárra; ReportLab + matplotlib tisztán Pythonból, `uv sync`-kel települ).

## Első lépések

```bash
# 1) szintetikus mintaadat (valódi adat NÉLKÜL kipróbálható)
uv run python tools/make_sample_data.py --out data/sample

# 2) előnézet: felismert fájlok, rekordtípusok, dátumtartomány
uv run vitalmart import --preview data/sample/export.zip

# 3) betöltés (haladásjelzővel, tranzakcióban)
uv run vitalmart import data/sample/export.zip

# 4) Garmin minta (szintetikus) egy másik személyhez
uv run python tools/make_sample_data.py --garmin --out data/sample
uv run vitalmart import --person me data/sample/garmin_export.zip

# 5) mi került be, kihez?
uv run vitalmart status
uv run vitalmart persons
```

További parancsok:

| Parancs | Leírás |
|---|---|
| `vitalmart init` | adatbázis létrehozása / migrálása |
| `vitalmart import <zip/mappa/xml/fit> [--person KOD] [--force] [--preview] [--source apple_health\|garmin]` | import |
| `vitalmart persons [--set-name KOD NEV]` | személyek és eszközeik |
| `vitalmart quality [--person KOD]` | adatminőségi összefoglaló: lefedettség, hézagok, megjelölt értékek, nem viselés, forrásváltások |
| `vitalmart schema <export>` | az export szerkezete (mezőnevek, típusok) **értékek nélkül** – biztonságosan megosztható |
| `vitalmart rebuild` | a core réteg teljes újraépítése a stagingből (pl. időzóna-váltás után) |
| `vitalmart status` | táblák sorszáma |
| `vitalmart backup` | adatbázis-másolat a `data/backups/` mappába |
| `vitalmart wipe --yes` | **minden** adat törlése (adatbázis, exportok, mentések, beállítások) |
| `vitalmart ui [--port 8501]` | grafikus felület (csak 127.0.0.1) |
| `vitalmart report [--person KOD] [--days 90 \| --from ÉÉÉÉ-HH-NN --to ÉÉÉÉ-HH-NN] [--out f.html] [--pdf]` | egészségügyi riport (HTML, `--pdf`-fel PDF is) |
| `vitalmart export [--tables T …] [--person KOD …] [--from …] [--to …] [--format xlsx\|csv\|parquet]` | adatexport; `--list` a táblák, `--history` a korábbi exportok, `--regenerate ID` újrakészítés |
| `vitalmart diagnose <export> [--person KOD] [--out diag.md] [--quick]` | **önellenőrzés**: a teljes feldolgozás egy ideiglenes adatmappában (a saját adatbázist nem érinti); megosztható, értékek nélküli jelentés – hibabejelentéshez |

Globális kapcsolók: `--data-dir`, `--config`, `-v` (technikai napló).

## Apple Health export készítése

1. iPhone-on nyisd meg az **Egészség** appot.
2. Jobb felül a profilképed → **Összes egészségügyi adat exportálása** → Exportálás.
3. Az elkészült `export.zip`-et juttasd el a gépedre (AirDrop, kábel, Fájlok app).
4. `uv run vitalmart import --person partner export.zip` – a ZIP-et nem kell kicsomagolni
   (a kicsomagolt `apple_health_export/` mappa vagy maga az XML is megadható).

> Magyar nyelvű iPhone-on a ZIP `exportálás.zip`, a fő fájl `exportálás.xml` néven jön
> (ékezetek felbontott, NFD formában). A felismerés a tartalom alapján történik, így a fájlnév
> nyelve nem számít.

Feldolgozott tartalom: `export.xml` (`Record`, `Workout`, `ActivitySummary`);
az EKG CSV-k változatlanul tárolódnak (értelmezés nélkül); a GPX útvonalak nyilvántartásba
kerülnek; az `export_cda.xml` figyelmen kívül marad.

## Garmin export készítése

**A) Garmin Connect adatexport (ajánlott, ez a teljes előzmény)**
1. connect.garmin.com → Fiókbeállítások → **Adatkezelés → Adatok exportálása** → Kérelem.
2. Az e-mailben kapott linkről töltsd le a ZIP-et.
3. `uv run vitalmart import --person me <letöltött>.zip` – kicsomagolva (mappa) is megadható.

Feldolgozott tartalom (`config/garmin_mapping.yaml`):

| Fájl | Hová kerül |
|---|---|
| `DI-Connect-Aggregator/UDSFile_*.json` | `fact_daily_summary` (a Garmin napi összesítője az elsődleges), stressz-bontás, Body Battery, légzés, hidratáció → `fact_measurement` |
| `DI-Connect-Wellness/*_sleepData.json` | `fact_sleep_session` (fázis-összegek, alvás-pontszám, SpO₂, légzés, alvás közbeni stressz) |
| `DI-Connect-Fitness/*_summarizedActivities.json` | `fact_activity`, `fact_activity_hr_zone` |
| `DI-Connect-Metrics/MetricsMaxMetData_*.json` | `fact_vo2max` |
| `DI-Connect-Wellness/*_userBioMetrics.json` | `fact_body_measure` (súly), magasság |
| `DI-Connect-Uploaded-Files/UploadedFiles_*.zip` | a benne lévő FIT-aktivitások (perc/másodperc-pulzus); a JSON-ban is meglévő edzés nem duplikálódik |
| többi JSON az egészség-mappákban | nyers tárolás; a dátumos, numerikus mezők automatikus `garmin_<fájl>_<mező>` metrikaként |
| `DI-Connect-User/…` (profil, e-mail, fotó) és egyéb mappák | **nem olvassa be**, csak nyilvántartja |

Egységek az exportban (a leképezés átváltja): időtartam ms, távolság cm, sebesség cm/ms,
`calories` **kJ**, szintemelkedés cm, zónaidő ms, testsúly g, magasság cm.

**B) FIT-fájlok közvetlenül az óráról (USB)**: a `GARMIN/Activity` és `GARMIN/Monitor`
mappát (vagy egyetlen `.fit` fájlt) add meg. A Monitor-fájlokból percenkénti pulzus
(`fact_heart_rate_sample`) és stressz (`fact_stress_sample`) lesz. A Connect-exportban
ilyen napon belüli adat nincs.

> A Venu Sq 2 nem mér HRV-t, ezért Garmin-adatból `fact_hrv_nightly` nem keletkezik
> (a riport ezt „nincs adat" jelzéssel kezeli, SPEC 4.1 megjegyzés).

**Séma-változás esetén** (a Garmin időnként átnevez mezőket): futtasd a
`uv run vitalmart schema <export>` parancsot – csak mezőneveket és típusokat ír ki, értékeket
nem –, és a `config/garmin_mapping.yaml`-ban igazítsd az útvonalakat. Kód módosítása nem kell.

## Több személy (háztartás)

Minden import egy személyhez tartozik (`--person`, alapértelmezés: `config/app.yaml`
→ `default_person`). A személy a forráseszközön keresztül kapcsolódik a tényadatokhoz
(`dim_source.person_key`). Egy eszköz csak egy személyé lehet: ha ugyanaz az óra másik
személy alatt érkezik, az import hibával leáll és visszagörget. A megjelenítendő név
(`vitalmart persons --set-name partner "…"`) csak a helyi adatbázisban tárolódik.

## Adatvédelem és biztonság

- **Minden helyben marad.** Az adatbázis (`vitalmart.duckdb`), az exportok (`exports/`), a mentések
  (`backups/`) és a beállítások (`settings.yaml`) az adatmappában vannak, ami `.gitignore`-olt.
  Nincs telemetria, nincs külső API-hívás, a riport és az elemzések szabályalapúak (nincs LLM),
  a Plotly-könyvtár a HTML-be ágyazott (nincs CDN).
- **A felület csak a 127.0.0.1-en** érhető el; más címre kötve el sem indul.
- **A napló** csak technikai információt tartalmaz (darabszámok, fájlnevek, időtartamok), értéket nem.
- **Mentés:** `vitalmart backup` vagy Beállítások → Mentés (másolat a `backups/` mappába).
  **Teljes törlés:** `vitalmart wipe --yes` vagy Beállítások → Teljes törlés (megerősítéssel).
- **Titkosítás (ajánlott):** az adatmappát titkosított köteten tárold – Windows: BitLocker,
  macOS: FileVault, Linux: LUKS / titkosított home. Beépített adatbázis-titkosítás nincs (a DuckDB
  titkosított fájlja még nem minden platformon/verzióban stabil); a titkosított kötet ugyanezt nyújtja,
  és a mentésekre, exportokra is kiterjed.
- **Hibabejelentéshez** csak a `vitalmart schema` (mezőnevek, típusok) és a `vitalmart diagnose`
  (lépések, darabszámok, időtartamok, hibatípusok) kimenetét oszd meg – ezek nem tartalmaznak
  mért értéket, dátumot, eszköz- vagy forrásnevet.

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
- **Garmin forráskulcs:** `garmin:<deviceId>`; a Connect `deviceId` az óra egységazonosítója,
  ami a FIT `serial_number` mezőjével egyezik, így a JSON és a FIT adat ugyanahhoz a forráshoz kerül.
- **Adatminőség:** az érvényességi tartományon kívüli értékek (`config/metrics.yaml`:
  `valid_min`/`valid_max`, pl. pulzus 25–230, SpO₂ ≥ 70 %) `quality_flag = 'out_of_range'`
  jelölést kapnak, nem törlődnek; a napi összesítő kizárja őket.
- **Ismeretlen rekordtípus:** automatikusan `apple_<snake_case>` metrikaként a
  `fact_measurement`-be kerül, és a `dim_metric`-be `is_auto = true` jelöléssel.
- **Konfiguráció:** `config/app.yaml` (időzóna, alvás, memória), `config/metrics.yaml`
  (metrika-katalógus), `config/apple_mapping.yaml` (HK típusok, mértékegységek, edzéstípusok).

## Származtatott mutatók

Minden import után automatikusan újraépülnek, **személyenként**:

| Tábla | Tartalom |
|---|---|
| `derived_person_daily` | személy × nap × metrika egy értéke az **elsődleges forrásból** (`derived.source_priority`: óra > telefon > összesítő > app; metrikánként felülírható: `derived.primary_source`) |
| `derived_daily_stats` | 7/28 napos mozgóátlag és -medián, trend (MA7 − MA28), **személyes alapvonal** (megelőző 60 nap, min. 28 érvényes nap: átlag, szórás, medián, MAD), z-pont, robusztus z, alapvonal-sávon kívül? |
| `derived_sleep_regularity` | lefekvés/ébredés szórása 7/14 napon, alvási adósság (cél: `sleep.goal_hours`) |
| `derived_weekly_activity` | heti WHO-egyenértékes percek (mérsékelt + 2 × intenzív), erősítő napok, edzésszám, pihenőnapok és szabályosságuk |
| `derived_training_load` | napi terhelés (eszköz-terhelés → Edwards-TRIMP a pulzuszónákból → időtartam), akut:krónikus arány 7:28 – **módszertani közelítés** |

Fontos döntések:
- Az alapvonal a **megelőző** napokból számol (az aznapi érték nincs benne), a szórás mintaszórás (ddof = 1),
  a MAD skálázatlan; robusztus z = (érték − medián) / (1,4826 × MAD). A tesztek ezeket egy független
  pandas/scipy implementációval vetik össze (relatív eltérés < 1e-9).
- HRV módszerenként külön (`hrv_sdnn`, `hrv_rmssd`, ill. `ln_hrv_*` a trendhez) – sosem keveredik.
- Apple-nél nincs mérsékelt/intenzív bontás: az Edzés-gyűrű perce **mérsékeltként** számít (konzervatív);
  a heti sor `who_method` oszlopa jelzi, melyik módszer szerepel.
- Ugyanannak a személynek két forrásból rögzített, időben átfedő edzése egyszer számít (jobb forrás).
- A reproduktív egészségi és az automatikusan felvett (ismeretlen) metrikák nem kerülnek a származtatott rétegbe.

Adatminőség (`dq_*` táblák, `vitalmart quality`): lefedettség forrásonként és metrikánként, hézagok
(csak napi gyakoriságú metrikákra, `quality.gap_min_days`), megjelölt értékek összesítése, nem viselés jelei
(hosszú szünet a pulzusmintákban, konstans pulzus-sorozat, Garmin „nem viselt” stressz-jelzés),
elsődleges forrás váltása (legalább `quality.switch_min_days` napig tartó új forrás).

## Teljesítmény

Szintetikus, ≈ 1 GB-os Apple-export (`tools/benchmark.py --days 1700`: 4,7 év, percenkénti pulzus,
2,68 M rekord), 4 magos Linux, 16 GB RAM:

| Lépés | Idő |
|---|---|
| első import (staging + core + származtatott réteg + adatminőség) | **69 s** (cél: < 10 perc) |
| ugyanannak a fájlnak az újratöltése (`--force`) | 42 s |
| teljes újraépítés (`vitalmart rebuild`) | 21 s |
| riport (90 nap): számítás / HTML / PDF | 0,8 s / 0,1 s / 2,2 s |
| Elemző: minden nap heti × hónap bontásban; 1 év percenkénti pulzus óra × hét napja hőtérképe | < 0,1 s |
| XLSX-export (1 év, 2 tábla) | 0,4 s |

Adatbázis: 478 MB, csúcsmemória ≈ 3,5 GB. Valós, ≈ 0,9 M rekordos Apple-exporton: import 13 s,
újratöltés 11 s, a teljes `diagnose` (riport, PDF, minden beépített elemzés, exportok) 30 s alatt.

**Inkrementális újraépítés** (SPEC 10.3, SHOULD): szándékosan nincs – minden import után a core és
a származtatott réteg determinisztikusan, teljesen újraépül. 1 GB-nyi adaton ez ~20 s, és így nem
fordulhat elő, hogy egy időszakon átnyúló számítás (alapvonal, mozgóátlag, „a legújabb batch nyer”
deduplikáció, forrásváltás) részlegesen frissül. Az import többi része (staging) eleve csak az új
sorokat írja.

A DuckDB memóriakorlátja `config/app.yaml` → `database.memory_limit` (alapérték 2 GB);
e fölött az adatmappa `tmp/` alkönyvtárába lapoz. Kisebb gépen állítsd lejjebb.

## Ismert korlátok

- A core réteg minden importnál **teljesen** újraépül (1 GB-on ~20 s; lásd Teljesítmény).
- Az Apple napi összesítő (`fact_daily_summary`) forrásonként külön sor (óra és iPhone
  lépései nem adódnak össze); az „elsődleges forrás metrikánként" választás az M4 Beállítások része.
- Az Apple nem ad mérsékelt/intenzív perc bontást: a `moderate_min`/`vigorous_min` üres,
  az `exercise_min` az Edzés-gyűrű perce. A WHO-egyenértékes percek az M3-ban.
- Edzésenkénti pulzuszóna-idő (`fact_activity_hr_zone`) a felhasználói profillal (M4) együtt készül.
- GPX útvonalak csak nyilvántartásba kerülnek; EKG csak tárolás.
- Sérült (nem jól formált) `export.xml` esetén az import leáll és visszagörget
  (nincs „helyreállító" parse-olás, hogy csendben ne vesszen el adat).
- Intervallum-mennyiségek (pl. lépés) a kezdő időpont helyi napjához számítanak.
- Alvás-appok (Apple Health-be író külső alkalmazások) egymásba lógó szakaszokat írhatnak: a
  rendszer a szakaszok **unióját** számolja (nem az összegüket), az ébren-szakasz elsőbbséget
  kap az átfedő alvással szemben, a `sleep.max_interval_hours`-nál (16 h) hosszabb egyedi
  intervallum megjelölve megmarad, de a szakaszképzésből kimarad.
- Apple HRV: az `SDNN` mellett az újabb watchOS `RMSSD`-t is ad – külön `hrv_method` sorként.
- Reproduktív egészségi rekordok (pl. menstruáció) `reproductive` kategóriával, csak tárolásra
  kerülnek be; a riport nem használja őket.
- Garmin: a Connect-export alvásadata csak fázis-összegeket tartalmaz (hipnogram-intervallum
  nincs); a napon belüli pulzus/stressz csak az óráról másolt FIT Monitor-fájlokból jön.
- Garmin: a testsúly a Connect-beállításból (`USER_SETTING`) is származhat, nem csak mérlegből.
- A Streamlit számbeviteli mezői tizedespontot mutatnak (a böngésző-komponens korlátja);
  minden kijelzett szám, dátum és idő magyar formátumú (tizedesvessző, ÉÉÉÉ.HH.NN., 24 órás).
- A riport egyelőre csak magyar nyelvű; a riport-szövegek egy része
  (nem orvosi leíró mondatok) az i18n-fájlokban, az orvosi tartalmú szövegek a `rules.yaml`-ban vannak.
- A korrelációk p-értéke nincs többszörös összehasonlításra korrigálva (a riport jelzi).
- Az Elemző mozgóátlaga a megjelenített időszakok (pl. hetek) számán dolgozik, nem naptári napokon;
  a naptári ablakos mutatók a „Személyes alapvonal és z-pont” témában vannak.
- A lag-elemzés és az összehasonlító mód egy személyre fut; a heti WHO-téma nem választható
  (nem napi). Sok eltolás vizsgálatakor a „legerősebb” eltolás véletlenül is adódhat (a felület jelzi).
- Mentett elemzés riport-mellékletként (SPEC 8.3 SHOULD) még nincs; a nézetek PDF-exportja sem.
- Egyszerre egy folyamat írhatja az adatbázist (felület VAGY parancssor).
- PDF: az ábrák statikus képek (a HTML-riport interaktív); a Plotly-ábrák matplotlib-átírása a
  használt ábratípusokat fedi le (vonal, terület, oszlop, hisztogram, box/violin → boxplot,
  hőtérkép, radar, idővonal); ismeretlen típusnál az ábrán jelzés jelenik meg.
- A nagyon nagy XLSX-exportok (milliós sorszám) lassúak (kb. 1 perc / millió sor) – erre a
  felület figyelmeztet; CSV/Parquet ajánlott.
- A CSV vesszővel tagolt, tizedesponttal (gépi feldolgozásra); a magyar Excel-megnyitáshoz az
  XLSX ajánlott.
- Csak FIT Monitor-fájlokból (Connect-export nélkül) még nem képződik napi összesítő, így
  származtatott napi mutató sem; a pulzus- és stresszminták és az adatminőségi jelek megvannak.
- Az ACWR terhelési módszere személyen belül változhat (pl. Garmin TRIMP vs. Apple időtartam);
  a `load_method` oszlop jelzi, a riport (M6) ezt figyelmeztetéssel kezeli.
- A Garmin-tevékenységtípusok egységesítése (`activity_types` a YAML-ban) a te edzéseidhez
  igazítható; pl. az `indoor_cardio` jelenleg „egyéb / aerob".

## Fejlesztés

```bash
uv run pytest                      # összes teszt (szintetikus adaton; ~4 perc)
uv run pytest --cov=vitalmart      # lefedettség
uv run ruff check && uv run mypy src
uv run python tools/make_sample_data.py --out data/sample [--garmin] [--days 365]
uv run python tools/benchmark.py --days 1700 --work /tmp/vm-bench    # ≈ 1 GB teljesítményteszt
```

Szabályok (részletesen a [`CLAUDE.md`](CLAUDE.md)-ben): valódi egészségügyi adat soha nem kerülhet a
repóba, tesztbe vagy naplóba; orvosi küszöb és szöveg csak a `config/rules.yaml`-ban; mezőleképezés
csak a `config/*_mapping.yaml`-ban; mérőszám/dimenzió csak a `config/semantic.yaml`-ban; minden
felhasználói érték SQL-paraméter; a felhasználói szövegek az `src/vitalmart/i18n/{hu,en}.yaml`-ban.

## Hibaelhárítás

| Jelenség | Teendő |
|---|---|
| „Ezt a fájlt már betöltötted” | azonos fájl; `--force`-szal újratölthető (duplikáció nem keletkezik) |
| „Az XML-fájl sérült vagy csonka” | exportáld / töltsd le újra; az adatbázis változatlan maradt (rollback) |
| „Az adatbázist egy másik folyamat használja” | egyszerre egy folyamat írhat: zárd be a felületet a parancssori import előtt, vagy importálj a felületről |
| A felület nem indul: a 8501-es port foglalt | `uv run vitalmart ui --port 8502` |
| Kevés a memória / lassú a nagy import | csökkentsd a `database.memory_limit` értékét (`config/app.yaml`); a DuckDB a `tmp/` mappába lapoz |
| Időzóna vagy alvásnap-konvenció módosítása után furcsa napok | `uv run vitalmart rebuild` (a felületi mentés ezt automatikusan elvégzi) |
| A riport egy szekciónál „nincs elegendő adat” | az időszakban kevés érvényes nap van (pl. új óra); válassz hosszabb/korábbi időszakot – ez szándékos, a riport nem következtet kevés adatból |
| A Garmin megváltoztatta az export szerkezetét | `uv run vitalmart schema <export>` → útvonalak igazítása a `config/garmin_mapping.yaml`-ban |
| Bármilyen más hiba | `uv run vitalmart diagnose <export> --out diag.md` – a megosztható jelentés megmutatja, melyik lépés hibázik |
