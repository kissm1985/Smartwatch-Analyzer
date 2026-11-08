"""Generate a synthetic Apple Health export (SPEC 10.4).

    uv run python tools/make_sample_data.py --out data/sample [--days 120] [--unzipped]
    uv run python tools/make_sample_data.py --out data/sample --garmin [--usb-days 3]
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from vitalmart import sample_data_garmin
from vitalmart.sample_data import SampleSpec, write_export_dir, write_export_zip


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=Path("data/sample"))
    p.add_argument("--start", type=date.fromisoformat, default=SampleSpec.start)
    p.add_argument("--days", type=int, default=SampleSpec.days)
    p.add_argument("--seed", type=int, default=SampleSpec.seed)
    p.add_argument("--hr-interval", type=int, default=SampleSpec.hr_interval_min,
                   help="pulzusminta-gyakoriság percben (kisebb = nagyobb fájl)")
    p.add_argument("--unzipped", action="store_true", help="mappa a ZIP helyett")
    p.add_argument("--garmin", action="store_true",
                   help="Garmin Connect export (+FIT) az Apple helyett")
    p.add_argument("--usb-days", type=int, default=0,
                   help="Garmin: ennyi nap FIT Monitor-fájl (GARMIN/Monitor) is")
    a = p.parse_args()
    if a.garmin:
        gspec = sample_data_garmin.GarminSpec(seed=a.seed)
        out = (sample_data_garmin.write_export_dir(a.out / "garmin_export", gspec) if a.unzipped
               else sample_data_garmin.write_export_zip(a.out, gspec))
        print(f"Szintetikus Garmin export: {out}")
        if a.usb_days:
            usb = sample_data_garmin.write_usb_folder(a.out / "garmin_usb", gspec.start, a.usb_days)
            print(f"Szintetikus Garmin USB-mappa: {usb}")
        return
    spec = SampleSpec(start=a.start, days=a.days, seed=a.seed, hr_interval_min=a.hr_interval)
    out = write_export_dir(a.out, spec) if a.unzipped else write_export_zip(a.out, spec)
    print(f"Szintetikus Apple export: {out}")


if __name__ == "__main__":
    main()
