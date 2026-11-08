"""Synthetic, deterministic Apple Health export generator (SPEC 10.4).

NEVER use real personal data as test fixtures. This module produces an
``export.zip`` that mimics the structure of an iPhone Health export with
built-in, known patterns so parsers, metrics and report rules are testable:

* poor-sleep period   (``POOR_SLEEP``): ~5.5 h sleep, later bedtime
* illness period      (``ILLNESS``, 8 days): resting HR +8 bpm, HRV −45 %, no workouts
* training block      (``TRAINING``):   5–6 workouts / week instead of ~3
* DST transitions inside the default range (2024-03-31 spring forward)
* data-quality traps: one impossible HR value (250 bpm), an exact duplicate
  record, an unknown record type, a GPX route, an ECG CSV, export_cda.xml.
"""

from __future__ import annotations

import io
import math
import random
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Budapest")
WATCH = "Teszt Apple Watch"
PHONE = "Teszt iPhone"
SCALE = "Teszt Mérleg"
WATCH_DEVICE = ("<<HKDevice: 0x0>, name:Apple Watch, manufacturer:Apple Inc., model:Watch, "
                "hardware:Watch6,2, software:10.3>")
PHONE_DEVICE = ("<<HKDevice: 0x0>, name:iPhone, manufacturer:Apple Inc., model:iPhone, "
                "hardware:iPhone14,2, software:17.4>")

# day-offset ranges (inclusive start, exclusive end) relative to the start date
POOR_SLEEP = (20, 34)
ILLNESS = (45, 53)
TRAINING = (60, 81)


@dataclass(frozen=True)
class SampleSpec:
    start: date = date(2024, 2, 15)
    days: int = 120
    seed: int = 42
    hr_interval_min: int = 10


DEFAULT_SPEC = SampleSpec()


def _fmt(ts: datetime) -> str:
    """Instant → Apple string in local time with that instant's own offset.

    Naive values are interpreted as Budapest wall time; aware values (the
    generator works in absolute UTC so DST nights get real 23/25 h) are converted.
    """
    aware = ts.replace(tzinfo=TZ) if ts.tzinfo is None else ts
    return aware.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def _local_midnight_utc(d: date) -> datetime:
    return datetime.combine(d, time()).replace(tzinfo=TZ).astimezone(UTC)


def _in(day: int, rng_: tuple[int, int]) -> bool:
    return rng_[0] <= day < rng_[1]


class _Writer:
    def __init__(self) -> None:
        self.buf = io.StringIO()

    def record(
        self, rtype: str, source: str, start: datetime, end: datetime | None = None,
        value: str | float | int | None = None, unit: str | None = None,
        device: str | None = None, metadata: dict[str, str] | None = None,
        version: str = "10.3",
    ) -> None:
        end = end or start
        attrs = [("type", rtype), ("sourceName", source), ("sourceVersion", version)]
        if device:
            attrs.append(("device", device))
        if unit:
            attrs.append(("unit", unit))
        attrs += [("creationDate", _fmt(end)), ("startDate", _fmt(start)), ("endDate", _fmt(end))]
        if value is not None:
            v = f"{value:.4f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
            attrs.append(("value", v))
        a = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs)
        if metadata:
            self.buf.write(f" <Record {a}>\n")
            for k, v in metadata.items():
                self.buf.write(f"  <MetadataEntry key={quoteattr(k)} value={quoteattr(v)}/>\n")
            self.buf.write(" </Record>\n")
        else:
            self.buf.write(f" <Record {a}/>\n")

    def raw(self, text: str) -> None:
        self.buf.write(text)


HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary|ClinicalRecord)*)>
<!ATTLIST HealthData locale CDATA #REQUIRED>
<!ELEMENT ExportDate EMPTY>
<!ATTLIST ExportDate value CDATA #REQUIRED>
]>
<HealthData locale="hu_HU">
 <ExportDate value="{export_date}"/>
 <Me HKCharacteristicTypeIdentifierDateOfBirth="1985-01-01"
     HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexMale"
     HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet"
     HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet"
     HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None"/>
"""

WORKOUT_PLAN = [
    # type, minutes, km per min (None = no distance), avg hr
    ("HKWorkoutActivityTypeRunning", 40, 0.18, 152),
    ("HKWorkoutActivityTypeTraditionalStrengthTraining", 45, None, 118),
    ("HKWorkoutActivityTypeWalking", 50, 0.09, 104),
    ("HKWorkoutActivityTypeHighIntensityIntervalTraining", 25, None, 160),
    ("HKWorkoutActivityTypeYoga", 30, None, 92),
    ("HKWorkoutActivityTypeCycling", 60, 0.42, 138),
]


def generate_export_xml(spec: SampleSpec = DEFAULT_SPEC) -> str:
    rnd = random.Random(spec.seed)
    w = _Writer()
    end_day = spec.start + timedelta(days=spec.days)
    w.raw(HEADER.format(export_date=_fmt(_local_midnight_utc(end_day))))

    # one-off records
    first = _local_midnight_utc(spec.start) + timedelta(hours=8)
    w.record("HKQuantityTypeIdentifierHeight", PHONE, first, value=1.80, unit="m",
             version="17.4")

    workouts: list[str] = []
    summaries: list[str] = []
    duplicate_done = False

    for d in range(spec.days):
        day = spec.start + timedelta(days=d)
        midnight = _local_midnight_utc(day)
        next_midnight = _local_midnight_utc(day + timedelta(days=1))
        ill = _in(d, ILLNESS)
        poor = _in(d, POOR_SLEEP)
        training = _in(d, TRAINING)
        weekend = day.weekday() >= 5

        # ---------------- sleep: night ending on `day` (wake-day convention)
        bed_h = 23.0 + (1.2 if poor else 0.0) + (0.6 if weekend else 0.0) + rnd.uniform(-0.4, 0.4)
        sleep_h = (5.5 if poor else 7.4) + (0.4 if weekend else 0) + rnd.uniform(-0.5, 0.5)
        if ill:
            sleep_h += 0.8
        bed = _local_midnight_utc(day - timedelta(days=1)) + timedelta(hours=bed_h)
        next_bed = midnight + timedelta(hours=bed_h)
        wake = bed + timedelta(hours=sleep_h)
        # iPhone in-bed record (local wall-clock arithmetic is fine: generator only)
        w.record("HKCategoryTypeIdentifierSleepAnalysis", PHONE, bed - timedelta(minutes=10),
                 wake + timedelta(minutes=8), value="HKCategoryValueSleepAnalysisInBed",
                 version="17.4")
        # watch stages: 90-minute cycles
        t = bed
        cycle = 0
        stage_seq: list[tuple[str, datetime, datetime]] = []
        while t < wake:
            deep_m = max(5, 30 - cycle * 7 + rnd.randint(-4, 4))
            core_m = 40 + rnd.randint(-8, 8)
            rem_m = 12 + cycle * 6 + rnd.randint(-3, 3)
            for stage, mins in (("AsleepCore", core_m // 2), ("AsleepDeep", deep_m),
                                ("AsleepCore", core_m // 2), ("AsleepREM", rem_m)):
                s, e = t, min(wake, t + timedelta(minutes=mins))
                if e > s:
                    stage_seq.append((stage, s, e))
                t = e
                if t >= wake:
                    break
            if t < wake and rnd.random() < 0.5:
                e = min(wake, t + timedelta(minutes=rnd.randint(2, 8)))
                stage_seq.append(("Awake", t, e))
                t = e
            cycle += 1
        for stage, s, e in stage_seq:
            w.record("HKCategoryTypeIdentifierSleepAnalysis", WATCH, s, e,
                     value=f"HKCategoryValueSleepAnalysis{stage}", device=WATCH_DEVICE)

        # nightly HRV (SDNN), respiratory rate, SpO2
        hrv_base = 48.0 * (0.55 if ill else 1.0) * (0.85 if poor else 1.0)
        for k in range(4):
            ts = bed + timedelta(minutes=45 + k * 80)
            if ts < wake:
                w.record("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", WATCH, ts,
                         ts + timedelta(seconds=60), value=round(hrv_base + rnd.gauss(0, 6), 2),
                         unit="ms", device=WATCH_DEVICE)
        for k in range(3):
            ts = bed + timedelta(minutes=60 + k * 120)
            if ts < wake:
                w.record("HKQuantityTypeIdentifierRespiratoryRate", WATCH, ts,
                         value=round(14.5 + (1.5 if ill else 0) + rnd.gauss(0, 0.6), 1),
                         unit="count/min", device=WATCH_DEVICE)
                w.record("HKQuantityTypeIdentifierOxygenSaturation", WATCH, ts,
                         value=round(min(1.0, 0.965 + rnd.gauss(0, 0.008)), 3), unit="%",
                         device=WATCH_DEVICE)

        # ---------------- heart rate samples across the day
        rest_base = 56 + (8 if ill else 0) + (2 if poor else 0)
        step = timedelta(minutes=spec.hr_interval_min)
        ts = midnight
        while ts < next_midnight:
            loc = ts.astimezone(TZ)
            hour = loc.hour + loc.minute / 60
            asleep = ts < wake or ts >= next_bed
            if asleep:
                bpm = rest_base - 6 + rnd.gauss(0, 2)
            else:
                bpm = rest_base + 18 + 10 * math.sin(hour / 24 * math.pi) + rnd.gauss(0, 6)
            md = {"HKMetadataKeyHeartRateMotionContext": "1"} if asleep else None
            w.record("HKQuantityTypeIdentifierHeartRate", WATCH, ts, value=round(bpm),
                     unit="count/min", device=WATCH_DEVICE, metadata=md)
            ts += step
        if d == 10:  # data-quality trap: physiologically impossible value
            w.record("HKQuantityTypeIdentifierHeartRate", WATCH, midnight + timedelta(hours=13,
                     minutes=3), value=250, unit="count/min", device=WATCH_DEVICE)

        # daily resting / walking HR
        w.record("HKQuantityTypeIdentifierRestingHeartRate", WATCH, midnight + timedelta(hours=7),
                 midnight + timedelta(hours=23, minutes=59),
                 value=round(rest_base + rnd.gauss(0, 1.5)), unit="count/min",
                 device=WATCH_DEVICE)
        w.record("HKQuantityTypeIdentifierWalkingHeartRateAverage", WATCH,
                 midnight + timedelta(hours=8), midnight + timedelta(hours=22),
                 value=round(rest_base + 38 + rnd.gauss(0, 3)), unit="count/min",
                 device=WATCH_DEVICE)

        # ---------------- hourly activity (watch + phone; phone overlaps on purpose)
        steps_day = 0
        for h in range(7, 23):
            s = midnight + timedelta(hours=h)
            e = s + timedelta(minutes=59)
            steps = max(0, int(rnd.gauss(450 if not ill else 180, 180)))
            steps_day += steps
            w.record("HKQuantityTypeIdentifierStepCount", WATCH, s, e, value=steps, unit="count",
                     device=WATCH_DEVICE)
            w.record("HKQuantityTypeIdentifierStepCount", PHONE, s, e,
                     value=int(steps * 0.8), unit="count", device=PHONE_DEVICE, version="17.4")
            w.record("HKQuantityTypeIdentifierDistanceWalkingRunning", WATCH, s, e,
                     value=round(steps * 0.00072, 4), unit="km", device=WATCH_DEVICE)
            w.record("HKQuantityTypeIdentifierActiveEnergyBurned", WATCH, s, e,
                     value=round(steps * 0.04 + rnd.uniform(2, 8), 2), unit="kcal",
                     device=WATCH_DEVICE)
            w.record("HKQuantityTypeIdentifierBasalEnergyBurned", WATCH, s, e,
                     value=round(70 + rnd.uniform(-2, 2), 2), unit="kcal", device=WATCH_DEVICE)
            if rnd.random() < 0.75:
                w.record("HKQuantityTypeIdentifierAppleStandTime", WATCH, s,
                         s + timedelta(minutes=5), value=rnd.randint(1, 5), unit="min",
                         device=WATCH_DEVICE)
            if rnd.random() < 0.15:
                w.record("HKQuantityTypeIdentifierFlightsClimbed", PHONE, s, e,
                         value=rnd.randint(1, 4), unit="count", device=PHONE_DEVICE,
                         version="17.4")
        if not duplicate_done and d == 3:  # exact duplicate inside the same file
            s = midnight + timedelta(hours=9)
            for _ in range(2):
                w.record("HKQuantityTypeIdentifierFlightsClimbed", PHONE, s,
                         s + timedelta(minutes=2), value=2, unit="count", device=PHONE_DEVICE,
                         version="17.4")
            duplicate_done = True

        # unknown types (must land in fact_measurement as auto metrics)
        w.record("HKQuantityTypeIdentifierEnvironmentalAudioExposure", WATCH,
                 midnight + timedelta(hours=12), midnight + timedelta(hours=12, minutes=30),
                 value=round(62 + rnd.gauss(0, 5), 1), unit="dBASPL", device=WATCH_DEVICE)
        if d % 3 == 0:
            w.record("HKCategoryTypeIdentifierMindfulSession", WATCH,
                     midnight + timedelta(hours=21), midnight + timedelta(hours=21, minutes=5),
                     value="HKCategoryValueNotApplicable", device=WATCH_DEVICE)

        # ---------------- workouts
        per_week = 0 if ill else (6 if training else 3)
        do_workout = per_week and rnd.random() < per_week / 7
        exercise_min = 0
        if do_workout:
            wtype, mins, kmpm, hr = WORKOUT_PLAN[rnd.randrange(len(WORKOUT_PLAN))]
            mins = int(mins * rnd.uniform(0.8, 1.25))
            exercise_min = mins
            s = midnight + timedelta(hours=17, minutes=30 + rnd.randint(0, 60))
            e = s + timedelta(minutes=mins)
            kcal = round(mins * (hr - 60) * 0.11, 1)
            hr_avg = hr + rnd.gauss(0, 4)
            legacy = d % 2 == 0  # old exports: totals as attributes; new: statistics only
            attrs = (f'workoutActivityType="{wtype}" duration="{mins}" durationUnit="min" '
                     f'sourceName="{WATCH}" sourceVersion="10.3" device={quoteattr(WATCH_DEVICE)} '
                     f'creationDate="{_fmt(e)}" startDate="{_fmt(s)}" endDate="{_fmt(e)}"')
            if legacy:
                attrs += f' totalEnergyBurned="{kcal}" totalEnergyBurnedUnit="kcal"'
                if kmpm:
                    attrs += f' totalDistance="{round(mins * kmpm, 3)}" totalDistanceUnit="km"'
            lines = [f" <Workout {attrs}>"]
            lines.append('  <MetadataEntry key="HKIndoorWorkout" value="0"/>')
            if kmpm:
                lines.append(f'  <MetadataEntry key="HKElevationAscended" '
                             f'value="{rnd.randint(1000, 9000)} cm"/>')
            lines.append(f'  <WorkoutEvent type="HKWorkoutEventTypeSegment" date="{_fmt(s)}" '
                         f'duration="{mins}" durationUnit="min"/>')
            lines.append(f'  <WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" '
                         f'startDate="{_fmt(s)}" endDate="{_fmt(e)}" average="{hr_avg:.1f}" '
                         f'minimum="{hr - 35}" maximum="{hr + 22}" unit="count/min"/>')
            lines.append(f'  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" '
                         f'startDate="{_fmt(s)}" endDate="{_fmt(e)}" sum="{kcal}" unit="kcal"/>')
            if kmpm:
                dist_type = ("HKQuantityTypeIdentifierDistanceCycling"
                             if "Cycling" in wtype else
                             "HKQuantityTypeIdentifierDistanceWalkingRunning")
                lines.append(f'  <WorkoutStatistics type="{dist_type}" startDate="{_fmt(s)}" '
                             f'endDate="{_fmt(e)}" sum="{round(mins * kmpm, 3)}" unit="km"/>')
            if "Running" in wtype:
                route = f"/workout-routes/route_{day.isoformat()}.gpx"
                lines.append(f'  <WorkoutRoute sourceName="{WATCH}" creationDate="{_fmt(e)}" '
                             f'startDate="{_fmt(s)}" endDate="{_fmt(e)}">')
                lines.append(f'   <FileReference path="{route}"/>')
                lines.append("  </WorkoutRoute>")
            lines.append(" </Workout>")
            workouts.append("\n".join(lines) + "\n")
            w.record("HKQuantityTypeIdentifierAppleExerciseTime", WATCH, s, e, value=mins,
                     unit="min", device=WATCH_DEVICE)
            w.record("HKQuantityTypeIdentifierHeartRateRecoveryOneMinute", WATCH,
                     e + timedelta(minutes=1), e + timedelta(minutes=2),
                     value=round(24 + rnd.gauss(0, 4)), unit="count/min", device=WATCH_DEVICE)
        else:
            w.record("HKQuantityTypeIdentifierAppleExerciseTime", WATCH,
                     midnight + timedelta(hours=12), midnight + timedelta(hours=12, minutes=10),
                     value=10, unit="min", device=WATCH_DEVICE)
            exercise_min = 10

        # ---------------- weekly metrics
        if d % 7 == 0:
            w.record("HKQuantityTypeIdentifierVO2Max", WATCH, midnight + timedelta(hours=18),
                     value=round(44 + d / 60 + rnd.gauss(0, 0.5), 2), unit="mL/min·kg",
                     device=WATCH_DEVICE)
        if d % 7 == 1:
            ts_w = midnight + timedelta(hours=7, minutes=5)
            mass = 81.5 - d * 0.02 + rnd.gauss(0, 0.3)
            w.record("HKQuantityTypeIdentifierBodyMass", SCALE, ts_w, value=round(mass, 1),
                     unit="kg", version="2.1")
            w.record("HKQuantityTypeIdentifierBodyFatPercentage", SCALE, ts_w,
                     value=round(0.21 - d * 0.0001 + rnd.gauss(0, 0.003), 3), unit="%",
                     version="2.1")

        # ---------------- activity summary (rings)
        summaries.append(
            f' <ActivitySummary dateComponents="{day.isoformat()}" '
            f'activeEnergyBurned="{round(steps_day * 0.04 + 120, 1)}" '
            f'activeEnergyBurnedGoal="500" activeEnergyBurnedUnit="kcal" '
            f'appleMoveTime="0" appleMoveTimeGoal="0" '
            f'appleExerciseTime="{exercise_min}" appleExerciseTimeGoal="30" '
            f'appleStandHours="{rnd.randint(8, 14)}" appleStandHoursGoal="12"/>\n'
        )

    for wk in workouts:
        w.raw(wk)
    for sm in summaries:
        w.raw(sm)
    w.raw("</HealthData>\n")
    return w.buf.getvalue()


GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="VitalMart synthetic"><trk><name>Szintetikus útvonal</name>
<trkseg><trkpt lat="47.5000" lon="19.0400"><ele>110</ele><time>2024-04-10T16:30:00Z</time></trkpt>
<trkpt lat="47.5010" lon="19.0410"><ele>111</ele><time>2024-04-10T16:31:00Z</time></trkpt>
</trkseg></trk></gpx>
"""

ECG = """Név,Szintetikus Teszt
Születési dátum,1985-01-01
Rögzítve,2024-03-01 10:00:00 +0100
Besorolás,Szinusz ritmus
Mértékegység,µV
-12.1
-10.4
35.2
"""


def write_export_zip(out_dir: Path, spec: SampleSpec = DEFAULT_SPEC) -> Path:
    """Write ``export.zip`` (Apple folder layout) into ``out_dir`` and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "export.zip"
    xml = generate_export_xml(spec)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("apple_health_export/export.xml", xml)
        zf.writestr("apple_health_export/export_cda.xml",
                    '<?xml version="1.0"?><ClinicalDocument/>')
        zf.writestr("apple_health_export/workout-routes/route_2024-04-10.gpx", GPX)
        zf.writestr("apple_health_export/electrocardiograms/ecg_2024-03-01.csv", ECG)
    return target


def write_export_dir(out_dir: Path, spec: SampleSpec = DEFAULT_SPEC) -> Path:
    """Write the unzipped folder layout (``apple_health_export/``)."""
    root = out_dir / "apple_health_export"
    (root / "workout-routes").mkdir(parents=True, exist_ok=True)
    (root / "electrocardiograms").mkdir(parents=True, exist_ok=True)
    (root / "export.xml").write_text(generate_export_xml(spec), encoding="utf-8")
    (root / "workout-routes" / "route_2024-04-10.gpx").write_text(GPX, encoding="utf-8")
    (root / "electrocardiograms" / "ecg_2024-03-01.csv").write_text(ECG, encoding="utf-8")
    return root
