"""Synthetic Garmin Connect export (+ FIT files) generator – NO real data (SPEC 10.4).

Mirrors the structure and field names of a real Garmin Connect "export your data"
archive (DI_CONNECT/…) with fake identifiers and generated values, including the
real export's units (duration ms, distance cm, speed cm/ms, calories kJ,
elevation cm, zone times ms, weight g, height cm).

Built-in patterns (day offsets relative to ``start``): poor sleep ``POOR_SLEEP``,
illness ``ILLNESS`` (resting HR +8, stress +15, Body Battery −25), training
block ``TRAINING``. Traps: an unknown UDS field (``someFutureScore``), stress −1
in FIT, a FIT-only activity (not in the JSON summary), an ignored personal
profile file.
"""

from __future__ import annotations

import io
import json
import random
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from vitalmart.fit_writer import FitWriter, fit_time

TZ = ZoneInfo("Europe/Budapest")
USER_PK = 100000001          # fake Garmin account id
DEVICE_ID = 3900000001       # fake unit id (= FIT serial number)
EMAIL = "teszt.felhasznalo@example.com"

POOR_SLEEP = (10, 17)
ILLNESS = (22, 26)
TRAINING = (30, 41)


@dataclass(frozen=True)
class GarminSpec:
    start: date = date(2024, 9, 20)     # spans the 2024-10-27 DST change by default
    days: int = 45
    seed: int = 11


DEFAULT_SPEC = GarminSpec()


def _in(d: int, r: tuple[int, int]) -> bool:
    return r[0] <= d < r[1]


def _gmt(dt_utc: datetime) -> str:
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.0")


def _local_utc(d: date, hours: float) -> datetime:
    """Local wall time on day ``d`` + ``hours`` → naive UTC."""
    base = datetime.combine(d, time()).replace(tzinfo=TZ).astimezone(UTC).replace(tzinfo=None)
    return base + timedelta(hours=hours)


def _epoch_ms(dt_utc: datetime) -> float:
    return float(int(dt_utc.replace(tzinfo=UTC).timestamp() * 1000))


ACTIVITY_PLAN = [
    # activityType, sportType, minutes, km/min, avg hr
    ("running", "RUNNING", 40, 0.18, 152),
    ("indoor_cardio", "FITNESS_EQUIPMENT", 45, None, 125),
    ("walking", "STEPS", 50, 0.09, 104),
    ("strength_training", "TRAINING", 40, None, 112),
]


def build(spec: GarminSpec = DEFAULT_SPEC) -> dict[str, bytes]:
    """Return {relative path: bytes} of the export archive content."""
    rnd = random.Random(spec.seed)
    uds: list[dict] = []
    sleeps: list[dict] = []
    acts: list[dict] = []
    maxmet: list[dict] = []
    hydration: list[dict] = []
    fitness_age: list[dict] = []
    fit_files: dict[str, bytes] = {}

    for d in range(spec.days):
        day = spec.start + timedelta(days=d)
        ill, poor, train = _in(d, ILLNESS), _in(d, POOR_SLEEP), _in(d, TRAINING)
        rhr = 54 + (8 if ill else 0) + rnd.randint(-2, 2)
        stress = 28 + (15 if ill else 0) + (6 if poor else 0) + rnd.randint(-4, 4)

        # ---- sleep (night ending on `day`)
        bed = _local_utc(day - timedelta(days=1), 22.8 + (1.3 if poor else 0) + rnd.uniform(-.3, .3))
        hours = (5.4 if poor else 7.6) + rnd.uniform(-.4, .4)
        wake = bed + timedelta(hours=hours)
        total_s = int(hours * 3600)
        deep = int(total_s * 0.18)
        rem = int(total_s * 0.21)
        awake = rnd.choice([0, 120, 300, 600])
        light = total_s - deep - rem - awake
        sleeps.append({
            "sleepStartTimestampGMT": _gmt(bed), "sleepEndTimestampGMT": _gmt(wake),
            "calendarDate": day.isoformat(), "sleepWindowConfirmationType": "ENHANCED_CONFIRMED_FINAL",
            "deepSleepSeconds": deep, "lightSleepSeconds": light, "remSleepSeconds": rem,
            "awakeSleepSeconds": awake, "unmeasurableSeconds": 0,
            "spo2SleepSummary": {"userProfilePk": USER_PK, "deviceId": DEVICE_ID,
                                 "sleepMeasurementStartGMT": _gmt(bed),
                                 "sleepMeasurementEndGMT": _gmt(wake),
                                 "averageSPO2": round(95.5 + rnd.uniform(-1, 1), 2),
                                 "averageHR": float(rhr - 4), "lowestSPO2": rnd.randint(86, 92)},
            "averageRespiration": round(13.5 + (1.5 if ill else 0) + rnd.uniform(-.5, .5), 2),
            "lowestRespiration": 9.0, "highestRespiration": 19.0, "retro": False,
            "awakeCount": rnd.randint(0, 3), "avgSleepStress": round(12 + (8 if ill else 0) + rnd.uniform(-3, 3), 2),
            "sleepScores": {"overallScore": 58 if poor else 85 + rnd.randint(-5, 5),
                            "qualityScore": 80, "durationScore": 55 if poor else 95,
                            "recoveryScore": 60 if ill else 88, "feedback": "POSITIVE_OPTIMAL_STRUCTURE",
                            "insight": "NONE"},
            "restlessMomentCount": rnd.randint(20, 40),
        })

        # ---- activities
        per_week = 0 if ill else (6 if train else 3)
        moderate = vigorous = 0
        if per_week and rnd.random() < per_week / 7:
            atype, stype, mins, kmpm, hr = ACTIVITY_PLAN[rnd.randrange(len(ACTIVITY_PLAN))]
            mins = int(mins * rnd.uniform(0.85, 1.2))
            start = _local_utc(day, 17.5 + rnd.uniform(0, 1))
            dur_ms = mins * 60_000.0
            dist_cm = (mins * kmpm * 100_000.0) if kmpm else 0.0
            kj = round(mins * (hr - 60) * 0.11 * 4.184, 1)
            zone_share = [0.05, 0.10, 0.25, 0.35, 0.20, 0.05, 0.0]
            moderate, vigorous = mins // 2, mins // 4
            act = {
                "activityId": 20000000000 + d, "uuidMsb": 1, "uuidLsb": 2,
                "name": "Szintetikus edzés", "activityType": atype, "userProfileId": USER_PK,
                "timeZoneId": 124, "beginTimestamp": int(_epoch_ms(start)), "eventTypeId": 9,
                "rule": "private", "sportType": stype,
                "startTimeGmt": _epoch_ms(start),
                "startTimeLocal": _epoch_ms(start) + 3_600_000 * (2 if start.month in (4, 5, 6, 7, 8, 9) else 1),
                "duration": dur_ms, "distance": dist_cm, "elapsedDuration": dur_ms + 30_000,
                "movingDuration": dur_ms - 10_000, "avgSpeed": (dist_cm / dur_ms) if dist_cm else 0.0,
                "avgHr": float(hr), "maxHr": float(hr + 22), "minHr": float(hr - 40),
                "calories": kj, "bmrCalories": round(mins * 6.1, 1), "steps": float(mins * 150),
                "deviceId": DEVICE_ID, "manufacturer": "GARMIN", "lapCount": 1,
                "moderateIntensityMinutes": moderate, "vigorousIntensityMinutes": vigorous,
                "waterEstimated": 400.0, "favorite": False, "pr": False, "parent": False,
                "purposeful": False, "autoCalcCalories": False, "atpActivity": False,
                "elevationCorrected": False, "decoDive": False,
                **{f"hrTimeInZone_{z}": int(dur_ms * zone_share[z]) for z in range(7)},
            }
            if kmpm:
                act["elevationGain"] = float(rnd.randint(1000, 9000))
                act["vO2MaxValue"] = 53.0
            acts.append(act)
            # FIT copy of every 2nd activity (must NOT duplicate the JSON activity)
            if d % 2 == 0:
                fit_files[f"{USER_PK}_{20000000000 + d}_ACTIVITY.fit"] = _activity_fit(
                    start, mins, (dist_cm / 100.0) if dist_cm else 0.0, hr, rnd)

        # ---- daily summary (UDS) – field names as in the real export
        steps = int(rnd.gauss(4000 if ill else 9000, 1500))
        bb_hi = max(5, 85 - (25 if ill else 0) - (10 if poor else 0) + rnd.randint(-5, 5))
        uds.append({
            "userProfilePK": USER_PK, "calendarDate": day.isoformat(), "uuid": f"u{d}",
            "durationInMilliseconds": 86_400_000, "totalKilocalories": 2500.0 + steps * 0.04,
            "activeKilocalories": 400.0 + steps * 0.04, "bmrKilocalories": 2100.0,
            "wellnessKilocalories": 2500.0, "totalSteps": steps, "dailyStepGoal": 8000,
            "totalDistanceMeters": int(steps * 0.75), "wellnessDistanceMeters": int(steps * 0.75),
            "wellnessStartTimeGmt": _gmt(_local_utc(day, 0)),
            "wellnessStartTimeLocal": f"{day.isoformat()}T00:00:00.0",
            "highlyActiveSeconds": 1800, "activeSeconds": 5400,
            "moderateIntensityMinutes": moderate, "vigorousIntensityMinutes": vigorous,
            "isVigorousDay": vigorous > 0, "minHeartRate": rhr - 6, "maxHeartRate": 150 + rnd.randint(0, 25),
            "restingHeartRate": rhr, "currentDayRestingHeartRate": rhr,
            "restingHeartRateTimestamp": int(_epoch_ms(_local_utc(day, 0))),
            "includesWellnessData": True, "source": 0, "version": 3,
            "allDayStress": {"userProfilePK": USER_PK, "calendarDate": day.isoformat(),
                             "aggregatorList": [
                                 {"type": "TOTAL", "averageStressLevel": stress, "maxStressLevel": 95,
                                  "totalDuration": 80000, "restDuration": 30000, "lowDuration": 25000,
                                  "mediumDuration": 15000, "highDuration": 10000 + (8000 if ill else 0)},
                                 {"type": "AWAKE", "averageStressLevel": stress + 8, "maxStressLevel": 95,
                                  "totalDuration": 52000, "restDuration": 8000},
                                 {"type": "ASLEEP", "averageStressLevel": 14, "maxStressLevel": 40,
                                  "totalDuration": 28000, "restDuration": 22000}]},
            "bodyBattery": {"userProfilePK": USER_PK, "calendarDate": day.isoformat(),
                            "chargedValue": bb_hi - 20, "drainedValue": bb_hi - 15,
                            "bodyBatteryVersion": 3.0,
                            "bodyBatteryStatList": [
                                {"bodyBatteryStatType": "HIGHEST", "statsValue": bb_hi,
                                 "bodyBatteryStatus": "MEASURED", "statTimestamp": f"{day}T07:00:00.0"},
                                {"bodyBatteryStatType": "LOWEST", "statsValue": max(5, bb_hi - 60),
                                 "bodyBatteryStatus": "MEASURED", "statTimestamp": f"{day}T22:00:00.0"},
                                {"bodyBatteryStatType": "SLEEPSTART", "statsValue": 25,
                                 "bodyBatteryStatus": "MEASURED", "statTimestamp": f"{day}T00:00:00.0"},
                                {"bodyBatteryStatType": "SLEEPEND", "statsValue": bb_hi,
                                 "bodyBatteryStatus": "MEASURED", "statTimestamp": f"{day}T07:00:00.0"}]},
            "averageSpo2Value": 95.4, "lowestSpo2Value": 88, "latestSpo2Value": 96,
            "respiration": {"userProfilePK": USER_PK, "calendarDate": day.isoformat(),
                            "avgWakingRespirationValue": 15.0, "highestRespirationValue": 22.0,
                            "lowestRespirationValue": 9.0, "algorithmVersion": 100},
            "someFutureScore": 40 + d % 7,          # unknown field -> fact_measurement
            "someFutureNested": {"value": 1},       # unknown non-numeric -> raw only
        })
        if d % 3 == 0:
            hydration.append({"calendarDate": day.isoformat(), "valueInML": 250.0 * (1 + d % 4),
                              "capped": False, "timestampLocal": f"{day}T10:00:00.0",
                              "userProfilePK": USER_PK})
        if d % 7 == 0:
            maxmet.append({"userProfilePK": USER_PK, "calendarDate": day.isoformat(),
                           "deviceId": DEVICE_ID, "updateTimestamp": f"{day}T17:00:00.0",
                           "sport": "RUNNING", "subSport": "GENERIC",
                           "vo2MaxValue": 52.0 + (d // 14), "maxMet": 15.0,
                           "maxMetCategory": "GENERIC", "calibratedData": 1})
            fitness_age.append({"calendarDate": day.isoformat(), "chronologicalAge": 40,
                                "fitnessAge": 34.5 - d / 100, "userProfilePK": USER_PK})

    # FIT-only activity (not in the JSON summary) -> must be added from FIT
    extra_start = _local_utc(spec.start + timedelta(days=spec.days - 1), 7.0)
    fit_files[f"{USER_PK}_99_ACTIVITY.fit"] = _activity_fit(extra_start, 30, 5000.0, 145, rnd)

    half = spec.days // 2
    d0, d1, d2 = spec.start, spec.start + timedelta(days=half), spec.start + timedelta(days=spec.days)

    def win(lst: list[dict], key: str = "calendarDate") -> tuple[list[dict], list[dict]]:
        return ([x for x in lst if x[key] < d1.isoformat()],
                [x for x in lst if x[key] >= d1.isoformat()])

    uds_a, uds_b = win(uds)
    sl_a, sl_b = win(sleeps)
    biometrics = [
        {"version": 1, "metaData": {"userProfilePK": USER_PK, "calendarDate": f"{d0}T21:00:00.0",
                                    "sequence": 1}, "height": 180.0},
        {"version": 2, "metaData": {"userProfilePK": USER_PK, "calendarDate": f"{d0}T21:01:00.0",
                                    "sequence": 2},
         "weight": {"weight": 81000.0, "sourceType": "USER_SETTING",
                    "timestampGMT": _gmt(_local_utc(d0, 21.02))}},
        {"version": 3, "metaData": {"userProfilePK": USER_PK, "calendarDate": f"{d1}T08:00:00.0",
                                    "sequence": 3},
         "weight": {"weight": 80200.0, "sourceType": "USER_SETTING",
                    "timestampGMT": _gmt(_local_utc(d1, 8))}},
    ]
    zones: list[dict[str, object]] = [{"trainingMethod": "HR_MAX", "restingHeartRateUsed": 55, "zone1Floor": 90,
              "zone2Floor": 108, "zone3Floor": 126, "zone4Floor": 144, "zone5Floor": 162,
              "maxHeartRateUsed": 180, "sport": "DEFAULT"}]

    def j(obj: object) -> bytes:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    up = io.BytesIO()
    with zipfile.ZipFile(up, "w") as zf:
        for name, data in fit_files.items():
            zf.writestr(name, data)

    C = "DI_CONNECT"
    return {
        f"{C}/DI-Connect-Aggregator/UDSFile_{d0}_{d1}.json": j(uds_a),
        f"{C}/DI-Connect-Aggregator/UDSFile_{d1}_{d2}.json": j(uds_b),
        f"{C}/DI-Connect-Aggregator/HydrationLogFile_{d0}_{d2}.json": j(hydration),
        f"{C}/DI-Connect-Wellness/{d0}_{d1}_{USER_PK}_sleepData.json": j(sl_a),
        f"{C}/DI-Connect-Wellness/{d1}_{d2}_{USER_PK}_sleepData.json": j(sl_b),
        f"{C}/DI-Connect-Wellness/{USER_PK}_userBioMetrics.json": j(biometrics),
        f"{C}/DI-Connect-Wellness/{USER_PK}_heartRateZones.json": j(zones),
        f"{C}/DI-Connect-Wellness/{USER_PK}_fitnessAgeData.json": j(fitness_age),
        f"{C}/DI-Connect-Fitness/{EMAIL}_0_summarizedActivities.json":
            j([{"summarizedActivitiesExport": acts}]),
        f"{C}/DI-Connect-Fitness/{EMAIL}_gear.json": j([]),
        f"{C}/DI-Connect-Metrics/MetricsMaxMetData_{d0:%Y%m%d}_{d2:%Y%m%d}_{USER_PK}.json":
            j(maxmet),
        f"{C}/DI-Connect-User/user_profile.json":
            j({"userName": "szintetikus", "emailAddress": EMAIL}),
        f"{C}/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip": up.getvalue(),
    }


def _activity_fit(start: datetime, mins: int, dist_m: float, hr: int,
                  rnd: random.Random) -> bytes:
    w = FitWriter()
    w.message("file_id", type=4, manufacturer=1, product=4115, serial_number=DEVICE_ID,
              time_created=fit_time(start))
    for s in range(0, mins * 60, 10):
        w.message("record", timestamp=fit_time(start + timedelta(seconds=s)),
                  heart_rate=max(60, min(200, hr + rnd.randint(-8, 8))),
                  distance=int(dist_m * 100 * s / (mins * 60)), speed=2800)
    end = start + timedelta(minutes=mins)
    w.message("session", timestamp=fit_time(end), start_time=fit_time(start),
              total_elapsed_time=mins * 60_000, total_timer_time=mins * 60_000,
              total_distance=int(dist_m * 100), total_calories=int(mins * (hr - 60) * 0.11),
              avg_heart_rate=hr, max_heart_rate=hr + 20, sport=1 if dist_m else 10,
              sub_sport=0, total_ascent=40, total_training_effect=31,
              total_anaerobic_training_effect=12)
    return w.to_bytes()


def monitoring_fit(day: date, rnd: random.Random | None = None) -> bytes:
    """One day of monitoring (HR every minute via timestamp_16, stress every 3 min)."""
    rnd = rnd or random.Random(day.toordinal())
    t0 = _local_utc(day, 0)
    w = FitWriter()
    w.message("file_id", type=32, manufacturer=1, product=4115, serial_number=DEVICE_ID,
              time_created=fit_time(t0))
    w.message("monitoring", timestamp=fit_time(t0), heart_rate=55)
    for m in range(1, 24 * 60):
        ts = t0 + timedelta(minutes=m)
        w.message("monitoring", timestamp_16=fit_time(ts) & 0xFFFF,
                  heart_rate=52 + rnd.randint(0, 6) if ts.hour < 6 else 70 + rnd.randint(-8, 15))
        if m % 3 == 0:
            v = -1 if m == 600 else (10 + rnd.randint(0, 10) if ts.hour < 6 else 30 + rnd.randint(0, 40))
            w.message("stress_level", stress_level_value=v, stress_level_time=fit_time(ts))
    return w.to_bytes()


def write_export_zip(out_dir: Path, spec: GarminSpec = DEFAULT_SPEC) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "garmin_export.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, data in build(spec).items():
            zf.writestr(rel, data)
    return target


def write_export_dir(out_dir: Path, spec: GarminSpec = DEFAULT_SPEC) -> Path:
    for rel, data in build(spec).items():
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return out_dir


def write_usb_folder(out_dir: Path, start: date, days: int) -> Path:
    """GARMIN/Monitor/*.fit as copied from the watch over USB."""
    mon = out_dir / "GARMIN" / "Monitor"
    mon.mkdir(parents=True, exist_ok=True)
    for i in range(days):
        d = start + timedelta(days=i)
        (mon / f"{d:%y%m%d}00.FIT").write_bytes(monitoring_fit(d))
    return out_dir
