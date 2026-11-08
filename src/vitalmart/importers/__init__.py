"""Source adapters (one per export format)."""

from vitalmart.importers.apple_health import AppleHealthAdapter
from vitalmart.importers.base import SourceAdapter

ADAPTERS: list[type[SourceAdapter]] = [AppleHealthAdapter]

__all__ = ["ADAPTERS", "AppleHealthAdapter", "SourceAdapter"]
