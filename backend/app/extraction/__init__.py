"""Clean-room, deterministic, geometry-anchored extraction engine (DESIGN.md §12, §25.1)."""

from .artifact import Artifact, Entry, Provenance
from .config import DEFAULT_CONFIG, ExtractionConfig
from .core import extract
from .locator import ApplicationLocator, GrantLocator, Locator

__all__ = [
    "Artifact",
    "Entry",
    "Provenance",
    "ExtractionConfig",
    "DEFAULT_CONFIG",
    "extract",
    "Locator",
    "GrantLocator",
    "ApplicationLocator",
]
