"""Minimal FIT encoder – ONLY for synthetic test/sample data (SPEC 10.4).

Supports little-endian definition/data messages with the handful of base types
needed for activity (file_id, record, session) and monitoring (monitoring,
stress_level) files, and the FIT CRC-16.
"""

from __future__ import annotations

import struct
from datetime import datetime

FIT_EPOCH = datetime(1989, 12, 31)

# base type code -> (size, struct fmt)
BASE = {
    "enum": (0x00, 1, "B"), "sint8": (0x01, 1, "b"), "uint8": (0x02, 1, "B"),
    "sint16": (0x83, 2, "h"), "uint16": (0x84, 2, "H"), "sint32": (0x85, 4, "i"),
    "uint32": (0x86, 4, "I"), "uint32z": (0x8C, 4, "I"),
}

_CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def fit_time(dt_utc: datetime) -> int:
    return int((dt_utc - FIT_EPOCH).total_seconds())


# global message numbers + field definitions: name -> (field_num, base_type)
MESSAGES: dict[str, tuple[int, dict[str, tuple[int, str]]]] = {
    "file_id": (0, {"type": (0, "enum"), "manufacturer": (1, "uint16"), "product": (2, "uint16"),
                    "serial_number": (3, "uint32z"), "time_created": (4, "uint32")}),
    "record": (20, {"timestamp": (253, "uint32"), "heart_rate": (3, "uint8"),
                    "distance": (5, "uint32"), "speed": (6, "uint16")}),
    "session": (18, {"timestamp": (253, "uint32"), "start_time": (2, "uint32"),
                     "total_elapsed_time": (7, "uint32"), "total_timer_time": (8, "uint32"),
                     "total_distance": (9, "uint32"), "total_calories": (11, "uint16"),
                     "avg_heart_rate": (16, "uint8"), "max_heart_rate": (17, "uint8"),
                     "sport": (5, "enum"), "sub_sport": (6, "enum"),
                     "total_ascent": (22, "uint16"), "total_training_effect": (24, "uint8"),
                     "total_anaerobic_training_effect": (137, "uint8")}),
    "monitoring": (55, {"timestamp": (253, "uint32"), "timestamp_16": (26, "uint16"),
                        "heart_rate": (27, "uint8")}),
    "stress_level": (227, {"stress_level_value": (0, "sint16"),
                           "stress_level_time": (1, "uint32")}),
    # real activity files carry the user profile; wake/sleep time decode to datetime.time
    "user_profile": (3, {"wake_time": (28, "uint32"), "sleep_time": (29, "uint32")}),
}


class FitWriter:
    def __init__(self) -> None:
        self._body = bytearray()
        self._defs: dict[tuple[str, tuple[str, ...]], int] = {}

    def message(self, name: str, **values: int) -> None:
        gnum, spec = MESSAGES[name]
        names = tuple(k for k in spec if k in values)
        key = (name, names)
        if key not in self._defs:
            assert len(self._defs) < 16, "max 16 local message types"
            local = len(self._defs)
            self._defs[key] = local
            hdr = struct.pack("<BBBHB", 0x40 | local, 0, 0, gnum, len(names))
            fdefs = b"".join(
                struct.pack("<BBB", spec[n][0], BASE[spec[n][1]][1], BASE[spec[n][1]][0])
                for n in names)
            self._body += hdr + fdefs
        local = self._defs[key]
        data = bytes([local]) + b"".join(
            struct.pack("<" + BASE[spec[n][1]][2], values[n]) for n in names)
        self._body += data

    def to_bytes(self) -> bytes:
        body = bytes(self._body)
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
        header += struct.pack("<H", crc16(header))
        out = header + body
        return out + struct.pack("<H", crc16(out))
