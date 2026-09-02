"""Portable import validation + migration (DESIGN.md §11.3)."""

from .portable import ImportLimits, ImportValidationError, ParsedImport, analyze_import

__all__ = ["analyze_import", "ImportLimits", "ImportValidationError", "ParsedImport"]
