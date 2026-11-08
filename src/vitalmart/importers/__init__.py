"""Source adapters (one per export format)."""

from vitalmart.importers.apple_health import AppleHealthAdapter
from vitalmart.importers.base import SourceAdapter
from vitalmart.importers.garmin import GarminAdapter

ADAPTERS: list[type[SourceAdapter]] = [AppleHealthAdapter, GarminAdapter]

__all__ = ["ADAPTERS", "AppleHealthAdapter", "GarminAdapter", "SourceAdapter"]
