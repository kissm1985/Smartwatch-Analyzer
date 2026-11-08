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
    "fact_daily_summary",
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
            progress=None if args.quiet else _progress,
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


def cmd_report(cfg: AppConfig, args: argparse.Namespace) -> int:
    print(t("report.not_implemented"))
    return 2


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
    imp.add_argument("--source", choices=["apple_health"], help="forrás kézi megadása")
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

    rep = sub.add_parser("report", help="egészségügyi riport (M6)")
    rep.add_argument("--from", dest="date_from")
    rep.add_argument("--to", dest="date_to")
    rep.add_argument("--out")
    rep.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    overrides = {"data_dir": str(args.data_dir)} if args.data_dir else {}
    cfg = load_config(args.config, **overrides)
    set_language(cfg.language)
    try:
        return int(args.func(cfg, args))
    except ImportFailure as exc:
        msg = t(exc.i18n_key, **exc.kwargs)
        if exc.i18n_key != "import.failed":
            msg = t("import.failed", reason=msg)
        print(msg, file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
