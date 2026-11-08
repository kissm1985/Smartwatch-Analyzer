"""Command line interface: ``vitalmart <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from vitalmart import __version__
from vitalmart.config import AppConfig, load_config
from vitalmart.datamart import db
from vitalmart.datamart.sqlutil import fetch_scalar
from vitalmart.i18n import set_language, t
from vitalmart.importers.base import ImportFailure

STATUS_TABLES = [
    "dim_import_batch", "stg_apple_record", "stg_apple_workout", "stg_apple_activity_summary",
    "dim_source", "dim_metric", "dim_activity_type", "fact_measurement",
    "fact_heart_rate_sample", "fact_sleep_session", "fact_sleep_stage_interval",
    "fact_hrv_nightly", "fact_activity", "fact_body_measure", "fact_vo2max",
    "fact_daily_summary", "stg_garmin_record", "stg_garmin_fit_file", "stg_garmin_fit_message",
    "fact_stress_sample", "fact_activity_hr_zone", "dim_person",
    "derived_person_daily", "derived_daily_stats", "derived_sleep_regularity",
    "derived_weekly_activity", "derived_training_load",
]


_last_pct = [-1]


def _progress(frac: float) -> None:
    pct = int(frac * 100)
    if pct == _last_pct[0]:
        return
    _last_pct[0] = pct
    sys.stderr.write("\r" + t("import.progress", pct=pct))
    if pct >= 100:
        sys.stderr.write("\n")
    sys.stderr.flush()


def cmd_init(cfg: AppConfig, args: argparse.Namespace) -> int:
    con = db.connect(cfg)
    print(t("db.initialized", path=cfg.db_path, version=db.schema_version(con)))
    con.close()
    return 0


def cmd_import(cfg: AppConfig, args: argparse.Namespace) -> int:
    from vitalmart.importers import service

    path = Path(args.path).expanduser()
    if args.preview:
        pv = service.preview(path, args.source, None if args.quiet else _progress)
        print(t("import.preview_header", adapter=pv.discovery.adapter))
        print(t("import.preview_files"))
        for f in pv.discovery.files:
            print(f"  [{f.kind}] {f.path} ({f.size_bytes:,} B)".replace(",", " "))
        for wmsg in pv.discovery.warnings:
            print(f"  ! {wmsg}")
        print(t("import.preview_types"))
        for k, n in sorted(pv.type_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {k}: {n}")
        print(t("import.preview_range", date_from=pv.date_from, date_to=pv.date_to))
        return 0
    con = db.connect(cfg)
    try:
        res = service.run_import(
            con, cfg, path, force=args.force, adapter_name=args.source,
            progress=None if args.quiet else _progress, person=args.person,
        )
    finally:
        con.close()
    print(res.message)
    return 0 if res.status in ("success", "skipped") else 1


def cmd_rebuild(cfg: AppConfig, args: argparse.Namespace) -> int:
    from vitalmart.transform.rebuild import rebuild

    con = db.connect(cfg)
    secs = rebuild(con, cfg)
    con.close()
    print(t("rebuild.done", seconds=f"{secs:.1f}".replace(".", ",")))
    return 0


def cmd_status(cfg: AppConfig, args: argparse.Namespace) -> int:
    con = db.connect(cfg)
    print(t("status.header", path=cfg.db_path, version=db.schema_version(con)))
    for tbl in STATUS_TABLES:
        n = fetch_scalar(con, f"SELECT count(*) FROM {tbl}")
        print("  " + t("status.table_rows", table=tbl, rows=f"{n:,}".replace(",", " ")))
    con.close()
    return 0


def cmd_backup(cfg: AppConfig, args: argparse.Namespace) -> int:
    con = db.connect(cfg)
    target = db.backup(cfg, con)
    con.close()
    print(t("db.backup_done", path=target))
    return 0


def cmd_wipe(cfg: AppConfig, args: argparse.Namespace) -> int:
    if not args.yes:
        print(t("db.wipe_confirm"))
        return 2
    db.wipe(cfg)
    print(t("db.wiped", path=cfg.data_path))
    return 0


def cmd_persons(cfg: AppConfig, args: argparse.Namespace) -> int:
    con = db.connect(cfg)
    if args.set_name:
        key, name = args.set_name
        con.execute("INSERT INTO dim_person (person_key, display_name) VALUES (?, ?) "
                    "ON CONFLICT (person_key) DO UPDATE SET display_name = excluded.display_name",
                    [key, name])
    rows = con.execute("""
        SELECT p.person_key, coalesce(p.display_name, ''), s.source_key, s.vendor,
               s.first_date, s.last_date
        FROM dim_person p LEFT JOIN dim_source s ON s.person_key = p.person_key
        ORDER BY 1, 3""").fetchall()
    con.close()
    for key, name, src, vendor, d0, d1 in rows:
        print(t("persons.row", person=key, name=name, source=src or "–", vendor=vendor or "",
                date_from=d0 or "", date_to=d1 or ""))
    return 0


def cmd_schema(cfg: AppConfig, args: argparse.Namespace) -> int:
    from vitalmart.importers.schema import describe

    for line in describe(Path(args.path).expanduser()):
        print(line)
    return 0


def cmd_quality(cfg: AppConfig, args: argparse.Namespace) -> int:
    from vitalmart.analytics.quality import quality_report

    con = db.connect(cfg)
    for line in quality_report(con, args.person):
        print(line)
    con.close()
    return 0


def _open_when_ready(url: str, timeout_s: float = 60.0) -> None:
    """Open the browser once the local server answers (headless Streamlit never prompts)."""
    import time
    import urllib.request
    import webbrowser

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "_stcore/health", timeout=1):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.5)


def cmd_ui(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Start the Streamlit UI bound to 127.0.0.1 only (SPEC 10.1)."""
    import os
    import subprocess
    import threading

    from vitalmart.paths import PROJECT_ROOT

    cmd = [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app" / "main.py"),
           "--server.address", "127.0.0.1", "--server.port", str(args.port),
           "--server.headless", "true", "--browser.gatherUsageStats", "false"]
    env = dict(os.environ, VITALMART_DATA_DIR=str(cfg.data_path))
    url = f"http://127.0.0.1:{args.port}/"
    print(t("ui.started", url=url), flush=True)
    if args.open:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    try:
        return subprocess.call(cmd, cwd=PROJECT_ROOT, env=env)
    except KeyboardInterrupt:
        return 0


def cmd_report(cfg: AppConfig, args: argparse.Namespace) -> int:
    from datetime import date

    from vitalmart.report.engine import default_period, generate

    con = db.connect(cfg)
    person = args.person or cfg.default_person
    if args.date_from and args.date_to:
        start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    else:
        rng = default_period(con, person, args.days)
        if rng is None:
            print(t("report.ui.no_data"), file=sys.stderr)
            return 1
        start, end = rng
        if args.date_to:
            end = date.fromisoformat(args.date_to)
            start = end - (rng[1] - rng[0])
    out = Path(args.out).expanduser() if args.out else None
    rep, _html, path = generate(con, cfg, person, start, end,
                                out=out.with_suffix(".html") if out and args.pdf else out)
    print(t("report.ui.saved_to", path=path))
    if args.pdf:
        from vitalmart.export.service import export_report_pdf

        res = export_report_pdf(con, cfg, person, start, end, generated_at=rep.generated_at,
                                out=out.with_suffix(".pdf") if out else None)
        print(t("report.ui.saved_to", path=res.path))
    con.close()
    return 0


def cmd_export(cfg: AppConfig, args: argparse.Namespace) -> int:
    from datetime import date

    from vitalmart.export import service, tabular

    reg = tabular.load_registry()
    if args.list:
        for k, tb in reg.tables.items():
            print(f"{k:28s} {tb.label_hu}")
        return 0
    con = db.connect(cfg)
    try:
        if args.regenerate:
            res = service.regenerate(con, cfg, args.regenerate)
        elif args.history:
            for r in service.list_history(con).itertuples():
                print(f"{r.export_id}  {r.created_at:%Y-%m-%d %H:%M:%S}  {r.kind:8s} "
                      f"{r.fmt:7s} {r.title}")
            return 0
        else:
            sel = tabular.Selection(
                tables=args.tables or reg.default_tables, persons=args.person or [],
                date_from=date.fromisoformat(args.date_from) if args.date_from else None,
                date_to=date.fromisoformat(args.date_to) if args.date_to else None)
            res = service.export_raw(con, cfg, sel, args.format)
    except (ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        con.close()
    for w in res.warnings:
        print(w, file=sys.stderr)
    print(t("export.ui.done", path=res.path, rows=res.rows))
    return 0


def cmd_diagnose(cfg: AppConfig, args: argparse.Namespace) -> int:
    from vitalmart import diagnose

    path = Path(args.path).expanduser()
    if not path.exists():
        print(t("import.not_found", path=path), file=sys.stderr)
        return 2
    print(t("diagnose.running"), file=sys.stderr)
    diag = diagnose.run(path, person=args.person or cfg.default_person, quick=args.quick,
                        progress=lambda m: print(f"  … {m}", file=sys.stderr, flush=True))
    md = diagnose.to_markdown(diag, path)
    if args.out:
        out = Path(args.out).expanduser()
        out.write_text(md, encoding="utf-8")
        print(t("diagnose.saved", path=out), file=sys.stderr)
    else:
        print(md)
    return 0 if diag.ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vitalmart", description="VitalMart – okosóra-adatpiac")
    p.add_argument("--version", action="version", version=f"vitalmart {__version__}")
    p.add_argument("--config", type=Path, help="config/app.yaml helyett másik konfiguráció")
    p.add_argument("--data-dir", type=Path, help="adatmappa felülírása")
    p.add_argument("-v", "--verbose", action="store_true", help="részletes (technikai) napló")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="adatbázis létrehozása / migrálása").set_defaults(func=cmd_init)

    imp = sub.add_parser("import", help="export betöltése (ZIP, mappa vagy export.xml)")
    imp.add_argument("path")
    imp.add_argument("--source", choices=["apple_health", "garmin"],
                     help="forrás kézi megadása")
    imp.add_argument("--person", help="kinek az adata (személykód, pl. me, partner); "
                                      "alapértelmezés: config default_person")
    imp.add_argument("--force", action="store_true", help="azonos fájl újratöltése")
    imp.add_argument("--preview", action="store_true", help="csak előnézet, betöltés nélkül")
    imp.add_argument("-q", "--quiet", action="store_true", help="nincs haladásjelző")
    imp.set_defaults(func=cmd_import)

    sub.add_parser("rebuild", help="core réteg újraépítése a stagingből").set_defaults(
        func=cmd_rebuild)
    sub.add_parser("status", help="táblák sorszáma").set_defaults(func=cmd_status)
    sub.add_parser("backup", help="biztonsági mentés").set_defaults(func=cmd_backup)

    wipe = sub.add_parser("wipe", help="MINDEN adat törlése")
    wipe.add_argument("--yes", action="store_true", help="megerősítés")
    wipe.set_defaults(func=cmd_wipe)

    per = sub.add_parser("persons", help="személyek és eszközeik")
    per.add_argument("--set-name", nargs=2, metavar=("KOD", "NEV"),
                     help="megjelenítendő név beállítása (csak helyben tárolódik)")
    per.set_defaults(func=cmd_persons)

    sch = sub.add_parser("schema", help="export szerkezete (mezőnevek, típusok) ÉRTÉKEK NÉLKÜL")
    sch.add_argument("path")
    sch.set_defaults(func=cmd_schema)

    qa = sub.add_parser("quality", help="adatminőségi összefoglaló (lefedettség, hézagok, …)")
    qa.add_argument("--person", help="csak ennek a személynek")
    qa.set_defaults(func=cmd_quality)

    ui = sub.add_parser("ui", help="grafikus felület indítása (csak 127.0.0.1)")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--open", action="store_true", help="böngésző automatikus megnyitása")
    ui.set_defaults(func=cmd_ui)

    rep = sub.add_parser("report", help="egészségügyi riport (HTML)")
    rep.add_argument("--person", help="személykód (alapértelmezés: default_person)")
    rep.add_argument("--from", dest="date_from", help="ÉÉÉÉ-HH-NN")
    rep.add_argument("--to", dest="date_to", help="ÉÉÉÉ-HH-NN")
    rep.add_argument("--days", type=int, default=90,
                     help="időszak hossza napban, ha nincs --from (alapértelmezés: 90)")
    rep.add_argument("--out", help="kimeneti fájl (HTML; --pdf esetén mellé PDF is)")
    rep.add_argument("--pdf", action="store_true", help="PDF is készüljön (exports/reports)")
    rep.set_defaults(func=cmd_report)

    exp = sub.add_parser("export", help="nyers / származtatott adatok exportja (XLSX/CSV/Parquet)")
    exp.add_argument("--tables", nargs="+", help="táblák (lásd --list); alapértelmezés: "
                                                "export_tables.yaml default_tables")
    exp.add_argument("--person", nargs="+", help="személykód(ok); alapértelmezés: mindenki")
    exp.add_argument("--from", dest="date_from", help="ÉÉÉÉ-HH-NN")
    exp.add_argument("--to", dest="date_to", help="ÉÉÉÉ-HH-NN")
    exp.add_argument("--format", choices=["xlsx", "csv", "parquet"], default="xlsx")
    exp.add_argument("--list", action="store_true", help="exportálható táblák listája")
    exp.add_argument("--history", action="store_true", help="korábbi exportok listája")
    exp.add_argument("--regenerate", metavar="EXPORT_ID",
                     help="korábbi export újrakészítése ugyanazokkal a paraméterekkel")
    exp.set_defaults(func=cmd_export)

    dg = sub.add_parser("diagnose", help="önellenőrzés egy exporton ideiglenes adatmappában; "
                                         "megosztható, értékek nélküli jelentés")
    dg.add_argument("path")
    dg.add_argument("--person", help="személykód (alapértelmezés: default_person)")
    dg.add_argument("--out", help="a jelentés mentése Markdown-fájlba")
    dg.add_argument("--quick", action="store_true", help="PDF és exportok kihagyása")
    dg.set_defaults(func=cmd_diagnose)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = (load_config(args.config, data_dir=str(args.data_dir)) if args.data_dir
           else load_config(args.config))
    set_language(cfg.language)
    try:
        return int(args.func(cfg, args))
    except db.DatabaseLocked:
        print(t("db.locked", path=cfg.db_path), file=sys.stderr)
        return 3
    except ImportFailure as exc:
        msg = t(exc.i18n_key, **exc.kwargs)
        if exc.i18n_key != "import.failed":
            msg = t("import.failed", reason=msg)
        print(msg, file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
