"""Portable-save validation and v1->v2 migration (DESIGN.md §11.3).

Imports are **untrusted data**. This module turns raw ``.patent-viewer.json``
bytes into a safe, normalized structure under strict bounds, and never restores
server identity or authorization fields:

- byte / nesting-depth / entry / bookmark / string-length limits;
- typed-locator, page-index, normalized-box, enum, and confidence validation;
- legacy schema-version-1 (grant ``ref`` strings) migrated to version-2 typed
  locators, with missing provenance labeled;
- all IDs, storage keys, trust claims, and engine attestations in the file are
  ignored and regenerated.

Pure and framework-free so it is exhaustively unit-testable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..extraction.artifact import valid_box

_REF_RE = re.compile(r"(\d+):(\d+)")
_CONFIDENCE = {"high", "medium", "low"}
_MAX_PARAGRAPH = 16


@dataclass(frozen=True, slots=True)
class ImportLimits:
    max_bytes: int = 10 * 1024 * 1024
    max_depth: int = 64
    max_entries: int = 200_000
    max_bookmarks: int = 10_000
    max_text_len: int = 20_000
    max_string_len: int = 2_000
    max_page_index: int = 10_000


@dataclass(slots=True)
class ParsedImport:
    schema_version: int
    needs_migration: bool
    doc_type: str
    title: str | None
    source_sha256: str | None
    entries: list[dict]
    bookmarks: list[dict]
    warnings: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        return {
            "schema_version": 2,
            "origin_schema_version": self.schema_version,
            "doc_type": self.doc_type,
            "title": self.title,
            "source_sha256": self.source_sha256,
            "entries": self.entries,
            "bookmarks": self.bookmarks,
            "warnings": self.warnings,
        }


class ImportValidationError(ValueError):
    """Raised for input that fails validation. ``reason`` is a stable slug."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _err(reason: str, message: str) -> ImportValidationError:
    return ImportValidationError(reason, message)


def _pos_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _nonneg_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _too_deep(obj: object, max_depth: int) -> bool:
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            return True
        if isinstance(node, dict):
            for v in node.values():
                stack.append((v, depth + 1))
        elif isinstance(node, list):
            for v in node:
                stack.append((v, depth + 1))
    return False


def _clean_str(value: object, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\x00", "").strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _render_ref(locator: dict) -> str:
    if locator["kind"] == "grant":
        return f"{locator['column']}:{locator['printed_line']}"
    return f"[{locator['paragraph']}]"


def _locator(entry: dict, schema_version: int) -> dict:
    if schema_version == 1:
        ref = entry.get("ref")
        if not isinstance(ref, str):
            raise _err("invalid_locator", "legacy entry is missing a 'ref' string")
        m = _REF_RE.fullmatch(ref.strip())
        if not m:
            raise _err("invalid_locator", f"legacy ref {ref!r} is not 'column:line'")
        return {"kind": "grant", "column": int(m.group(1)), "printed_line": int(m.group(2))}

    loc = entry.get("locator")
    if not isinstance(loc, dict):
        raise _err("invalid_locator", "entry is missing a 'locator' object")
    kind = loc.get("kind")
    if kind == "grant":
        col, line = loc.get("column"), loc.get("printed_line")
        if not (_pos_int(col) and _pos_int(line)):
            raise _err("invalid_locator", "grant locator needs positive column/printed_line")
        return {"kind": "grant", "column": int(col), "printed_line": int(line)}
    if kind == "application":
        para = _clean_str(loc.get("paragraph"), _MAX_PARAGRAPH)
        if para is None:
            raise _err("invalid_locator", "application locator needs a paragraph string")
        return {"kind": "application", "paragraph": para}
    raise _err("invalid_locator", f"unknown locator kind {kind!r}")


def _box(value: object) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _err("invalid_box", "box must be four numbers")
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in value):
        raise _err("invalid_box", "box values must be numbers")
    box = tuple(float(n) for n in value)
    if not valid_box(box):  # 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        raise _err("invalid_box", "box is not a valid normalized rectangle")
    return list(box)


def _confidence(value: object) -> str:
    return value if value in _CONFIDENCE else "low"


def _entry(raw: object, schema_version: int, index: int, limits: ImportLimits) -> dict:
    if not isinstance(raw, dict):
        raise _err("invalid_entry", f"entry {index} is not an object")

    page_index = raw.get("page_index")
    if not _nonneg_int(page_index) or page_index >= limits.max_page_index:
        raise _err("invalid_entry", f"entry {index} has an invalid page_index")

    source_text = _clean_str(raw.get("source_text"), limits.max_text_len)
    if source_text is None:
        raise _err("invalid_entry", f"entry {index} has no source_text")
    display_text = _clean_str(raw.get("display_text"), limits.max_text_len) or source_text

    return {
        "entry_id": f"line_{index:07d}",  # regenerated; file IDs are ignored
        "ordinal": index,
        "page_index": page_index,
        "locator": _locator(raw, schema_version),
        "box": _box(raw.get("box")),
        "source_text": source_text,
        "display_text": display_text,
        "text_confidence": _confidence(raw.get("confidence")),
        "reference_confidence": _confidence(raw.get("reference_confidence")),
        "provenance": {"imported": True, "migrated": schema_version == 1},
        "warnings": [],
    }


def _bookmarks(
    data: dict, entries: list[dict], limits: ImportLimits, warnings: list[str]
) -> list[dict]:
    raw = data.get("bookmarks", [])
    if not isinstance(raw, list):
        raise _err("invalid_structure", "'bookmarks' must be a list")
    if len(raw) > limits.max_bookmarks:
        raise _err("too_many_bookmarks", "import exceeds the bookmark limit")

    by_ref: dict[str, str] = {}
    for ent in entries:
        by_ref.setdefault(_render_ref(ent["locator"]), ent["entry_id"])

    result: list[dict] = []
    for bm in raw:
        if not isinstance(bm, dict):
            continue
        entry_id: str | None = None
        idx = bm.get("entry_index")
        if _nonneg_int(idx) and idx < len(entries):
            entry_id = entries[idx]["entry_id"]
        elif isinstance(bm.get("ref"), str):
            entry_id = by_ref.get(bm["ref"].strip())
        if entry_id is None:
            warnings.append("dropped a bookmark whose target entry could not be resolved")
            continue
        result.append(
            {
                "entry_id": entry_id,
                "label": _clean_str(bm.get("label"), limits.max_string_len),
                "color": _clean_str(bm.get("color"), limits.max_string_len),
            }
        )
    return result


def analyze_import(raw: bytes, limits: ImportLimits | None = None) -> ParsedImport:
    """Validate and normalize raw portable-save bytes, or raise ImportValidationError."""
    limits = limits or ImportLimits()

    if len(raw) > limits.max_bytes:
        raise _err("too_large", "import file exceeds the size limit")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise _err("invalid_json", "file is not valid JSON") from None
    if not isinstance(data, dict):
        raise _err("invalid_json", "top-level value must be an object")
    if _too_deep(data, limits.max_depth):
        raise _err("too_deep", "JSON nesting is too deep")

    schema_version = data.get("schema_version")
    if schema_version not in (1, 2):
        raise _err("unsupported_schema", "only schema versions 1 and 2 are supported")

    entries_raw = data.get("entries")
    if not isinstance(entries_raw, list):
        raise _err("invalid_structure", "'entries' must be a list")
    if not entries_raw:
        raise _err("empty", "import contains no entries")
    if len(entries_raw) > limits.max_entries:
        raise _err("too_many_entries", "import exceeds the entry limit")

    entries = [_entry(e, schema_version, i, limits) for i, e in enumerate(entries_raw)]

    warnings: list[str] = []
    if schema_version == 1:
        warnings.append("Legacy version-1 save migrated; provenance labeled as imported.")
    warnings.append("No verified source PDF: document is imported_unverified and text-only.")

    bookmarks = _bookmarks(data, entries, limits, warnings)

    kinds = {e["locator"]["kind"] for e in entries}
    doc_type = "application" if kinds == {"application"} else "grant"
    if len(kinds) > 1:
        warnings.append("Entries mix grant and application locators; treated as grant.")

    source = data.get("source")
    sha = source.get("sha256") if isinstance(source, dict) else None
    source_sha256 = sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha) else None

    return ParsedImport(
        schema_version=schema_version,
        needs_migration=schema_version == 1,
        doc_type=doc_type,
        title=_clean_str(data.get("title"), limits.max_string_len),
        source_sha256=source_sha256,
        entries=entries,
        bookmarks=bookmarks,
        warnings=warnings,
    )
