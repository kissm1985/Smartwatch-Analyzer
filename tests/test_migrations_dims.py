from datetime import date
from pathlib import Path

import pytest

from tests.conftest import scalar
from vitalmart.datamart import db
from vitalmart.datamart.seed import easter_sunday, hungarian_holidays, part_of_day


def test_schema_version_and_idempotent_reconnect(cfg):
    con = db.connect(cfg)
    v = db.schema_version(con)
    assert v == max(n for n, _, _ in db.list_migrations())
    n_dates = scalar(con, "SELECT count(*) FROM dim_date")
    con.close()
    con2 = db.connect(cfg)  # migrations/seeds must not re-run or duplicate
    assert db.schema_version(con2) == v
    assert scalar(con2, "SELECT count(*) FROM dim_date") == n_dates
    assert scalar(con2, "SELECT count(*) FROM schema_migrations") == len(db.list_migrations())
    con2.close()


def test_bad_migration_name(tmp_path: Path):
    (tmp_path / "1_bad.sql").write_text("SELECT 1")
    with pytest.raises(ValueError):
        db.list_migrations(tmp_path)


def test_failed_migration_rolls_back(tmp_path: Path):
    import duckdb

    (tmp_path / "001_ok.sql").write_text("CREATE TABLE a (x INT);")
    (tmp_path / "002_broken.sql").write_text("CREATE TABLE b (x INT); SELEKT nonsense;")
    con = duckdb.connect()
    with pytest.raises(duckdb.Error):
        db.migrate(con, tmp_path)
    assert db.schema_version(con) == 1
    assert scalar(con, "SELECT count(*) FROM information_schema.tables WHERE table_name='b'") == 0


def test_dim_date(con):
    assert scalar(con, "SELECT min(date) FROM dim_date") == date(2015, 1, 1)
    assert scalar(con, "SELECT max(date) FROM dim_date") == date(2040, 12, 31)
    assert scalar(con, "SELECT count(*) FROM dim_date") == (date(2040, 12, 31) - date(2015, 1, 1)).days + 1
    row = con.execute(
        "SELECT day_of_week, day_name_hu, is_weekend, is_holiday_hu, holiday_name_hu, iso_week, "
        "season, quarter FROM dim_date WHERE date_key = 20240401").fetchall()[0]
    assert row == (1, "hétfő", False, True, "Húsvéthétfő", 14, "spring", 2)
    assert scalar(con, "SELECT is_holiday_hu FROM dim_date WHERE date_key = 20240329")  # Nagypéntek
    assert scalar(con, "SELECT holiday_name_hu FROM dim_date WHERE date_key = 20241023") == "Nemzeti ünnep"
    assert scalar(con, "SELECT is_workday_hu FROM dim_date WHERE date_key = 20240402")
    assert scalar(con, "SELECT season FROM dim_date WHERE date_key = 20241201") == "winter"


@pytest.mark.parametrize("year,expected", [
    (2019, date(2019, 4, 21)), (2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)),
    (2026, date(2026, 4, 5)), (2038, date(2038, 4, 25)),
])
def test_easter(year, expected):
    assert easter_sunday(year) == expected


def test_holidays_count_and_good_friday_from_2017():
    assert len(hungarian_holidays(2024)) == 13
    assert len(hungarian_holidays(2016)) == 12


def test_time_of_day(con):
    assert scalar(con, "SELECT count(*) FROM dim_time_of_day") == 1440
    assert part_of_day(5) == ("night", "éjszaka")
    assert part_of_day(6)[0] == "morning"
    assert part_of_day(12)[0] == "afternoon"
    assert part_of_day(23)[0] == "evening"
    assert scalar(con, "SELECT part_of_day FROM dim_time_of_day WHERE minute_of_day = 600") == "forenoon"


def test_static_dims(con):
    stages = {r[0] for r in con.execute("SELECT stage_key FROM dim_sleep_stage").fetchall()}
    assert stages == {"awake", "light", "deep", "rem", "in_bed", "unspecified"}
    assert scalar(con, "SELECT count(*) FROM dim_hr_zone") == 5


def test_backup(cfg, con):
    target = db.backup(cfg, con)
    assert target.exists() and target.stat().st_size > 0


def test_wipe(cfg):
    c = db.connect(cfg)
    db.backup(cfg, c)
    c.close()
    removed = db.wipe(cfg)
    assert not cfg.db_path.exists()
    assert not cfg.backups_path.exists()
    assert cfg.db_path in removed
