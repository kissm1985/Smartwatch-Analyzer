from pathlib import Path

from vitalmart.cli import main


def test_cli_end_to_end(tmp_path: Path, sample_zip: Path, capsys):
    d = ["--data-dir", str(tmp_path / "data")]
    assert main([*d, "init"]) == 0
    assert main([*d, "import", "--preview", "-q", str(sample_zip)]) == 0
    out = capsys.readouterr().out
    assert "HKQuantityTypeIdentifierHeartRate" in out and "Előnézet" in out
    assert main([*d, "import", "-q", str(sample_zip)]) == 0
    assert "Import kész" in capsys.readouterr().out
    assert main([*d, "import", "-q", str(sample_zip)]) == 0
    assert "már betöltötted" in capsys.readouterr().out
    assert main([*d, "rebuild"]) == 0
    assert main([*d, "status"]) == 0
    assert "fact_sleep_session" in capsys.readouterr().out
    assert main([*d, "backup"]) == 0
    assert main([*d, "report"]) == 0
    assert main([*d, "wipe"]) == 2
    assert main([*d, "wipe", "--yes"]) == 0
    assert not (tmp_path / "data" / "vitalmart.duckdb").exists()


def test_cli_import_error_message(tmp_path: Path, capsys):
    rc = main(["--data-dir", str(tmp_path / "data"), "import", str(tmp_path / "missing.zip")])
    assert rc == 1
    assert "nem létezik" in capsys.readouterr().err


def test_cli_reports_locked_database(tmp_path: Path, capsys):
    import subprocess
    import sys

    d = tmp_path / "data"
    assert main(["--data-dir", str(d), "init"]) == 0
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import duckdb, sys, time; c = duckdb.connect(sys.argv[1]); print('ok', flush=True); "
         "time.sleep(30)", str(d / "vitalmart.duckdb")], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ok"
        assert main(["--data-dir", str(d), "status"]) == 3
        assert "másik folyamat" in capsys.readouterr().err
    finally:
        holder.kill()
