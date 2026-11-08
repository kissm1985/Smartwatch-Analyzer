from datetime import datetime

from vitalmart.timeutil import date_key, offset_transitions, to_local


def test_budapest_transitions_2024():
    trans = dict(offset_transitions("Europe/Budapest", 2024, 2024))
    assert trans[datetime(2024, 3, 31, 1, 0)] == 120   # 02:00 CET -> 03:00 CEST
    assert trans[datetime(2024, 10, 27, 1, 0)] == 60   # 03:00 CEST -> 02:00 CET
    assert trans[datetime(2024, 1, 1)] == 60


def test_utc_zone_has_single_row():
    assert offset_transitions("UTC", 2020, 2021) == [(datetime(2020, 1, 1), 0)]


def test_to_local_dst():
    assert to_local(datetime(2024, 3, 31, 0, 59), "Europe/Budapest") == (datetime(2024, 3, 31, 1, 59), 60)
    assert to_local(datetime(2024, 3, 31, 1, 0), "Europe/Budapest") == (datetime(2024, 3, 31, 3, 0), 120)
    # the repeated hour on 2024-10-27
    assert to_local(datetime(2024, 10, 27, 0, 30), "Europe/Budapest") == (datetime(2024, 10, 27, 2, 30), 120)
    assert to_local(datetime(2024, 10, 27, 1, 30), "Europe/Budapest") == (datetime(2024, 10, 27, 2, 30), 60)


def test_date_key():
    assert date_key(datetime(2024, 3, 5).date()) == 20240305
