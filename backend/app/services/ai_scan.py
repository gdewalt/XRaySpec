"""Optional, metered AI review of an already-published patent artifact.

The service never replaces source OCR. It creates a new artifact version whose
``display_text`` and drawing indices contain validated AI suggestions. Patent
content is sent with ``store: false`` and usage metadata is recorded per owner.
"""

from __future__ import annotations

import base64
import copy
import gzip
import io
import json
import re
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from typing import Any

import httpx

from ..config import Settings

UsageCallback = Callable[[dict[str, int]], Awaitable[None]]
ProgressCallback = Callable[[str, str, int, int], Awaitable[None]]

_FIGURE_RE = re.compile(r"\bfig(?:ure)?s?\.?\s*([0-9]{1,3}[a-z]?)", re.IGNORECASE)
_FIGURE_ID_RE = re.compile(r"^([0-9]{1,3})([a-z]?)$", re.IGNORECASE)
_CALLOUT_RE = re.compile(r"^[0-9]{1,5}[A-Za-z]?$", re.ASCII)


class StructuredResponseIncomplete(Exception):
    """The provider returned a charged response without a complete JSON object."""


def _figure_id(value: object) -> str | None:
    match = _FIGURE_ID_RE.fullmatch(str(value).strip().replace(" ", ""))
    if not match:
        return None
    return f"{int(match.group(1))}{match.group(2).lower()}"


def _response_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    return text
    raise ValueError("AI response did not contain structured output")


class ResponsesClient:
    def __init__(self, settings: Settings, on_usage: UsageCallback) -> None:
        self.settings = settings
        self.on_usage = on_usage

    async def structured(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        content: list[dict[str, Any]],
        instructions: str,
        max_output_tokens: int = 8_000,
    ) -> dict[str, Any]:
        body = {
            "model": self.settings.openai_model,
            "store": False,
            "max_output_tokens": max_output_tokens,
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {self.settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        usage = payload.get("usage") or {}
        normalized_usage = {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
        await self.on_usage(normalized_usage)
        try:
            text = _response_text(payload)
            parsed = json.loads(text)
        except (ValueError, json.JSONDecodeError) as exc:
            status = str(payload.get("status") or "unknown")
            reason = str((payload.get("incomplete_details") or {}).get("reason") or "unknown")
            raise StructuredResponseIncomplete(
                f"structured response was incomplete (status={status}, reason={reason})"
            ) from exc
        if not isinstance(parsed, dict):
            raise StructuredResponseIncomplete("AI structured output was not an object")
        return parsed


_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entry_id": {"type": "string"},
                    "corrected_text": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["entry_id", "corrected_text", "confidence", "reason"],
            },
        },
        "figure_mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entry_id": {"type": "string"},
                    "raw_text": {"type": "string"},
                    "figure_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
                "required": ["entry_id", "raw_text", "figure_ids", "confidence"],
            },
        },
        "callout_mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entry_id": {"type": "string"},
                    "value": {"type": "string"},
                    "component_label": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["entry_id", "value", "component_label", "confidence"],
            },
        },
    },
    "required": ["corrections", "figure_mentions", "callout_mentions"],
}

_DRAWING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "figures": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "figure_id": {"type": "string"},
                    "box": {"type": "array", "items": {"type": "number"}},
                    "confidence": {"type": "number"},
                },
                "required": ["figure_id", "box", "confidence"],
            },
        },
        "callouts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": {"type": "string"},
                    "figure_id": {"type": "string"},
                    "box": {"type": "array", "items": {"type": "number"}},
                    "confidence": {"type": "number"},
                },
                "required": ["value", "figure_id", "box", "confidence"],
            },
        },
    },
    "required": ["figures", "callouts"],
}


def _chunks(entries: list[dict[str, Any]], limit: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for entry in entries:
        text = str(entry.get("display_text") or entry.get("source_text") or "")
        row = {"entry_id": str(entry.get("entry_id", "")), "text": text}
        row_size = len(text) + len(row["entry_id"]) + 30
        if current and size + row_size > max(2_000, limit):
            chunks.append(current)
            current, size = [], 0
        current.append(row)
        size += row_size
    if current:
        chunks.append(current)
    return chunks


def _safe_box(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        coords = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    # Vision responses use integer-like 0..1000 normalized coordinates.
    if max(coords) > 1.0:
        coords = [item / 1000.0 for item in coords]
    x0, y0, x1, y1 = [min(1.0, max(0.0, item)) for item in coords]
    if x0 >= x1 or y0 >= y1:
        return None
    return [x0, y0, x1, y1]


def _span(text: str, raw: str) -> list[int] | None:
    start = text.lower().find(raw.lower())
    return [start, start + len(raw)] if start >= 0 else None


_TEXT_REVIEW_INSTRUCTIONS = (
    "You review patent OCR with extreme fidelity. Correct only obvious OCR corruption, "
    "duplicated word fragments, and broken characters. Never paraphrase, summarize, "
    "modernize, or change legal meaning. Return only genuinely needed corrections. "
    "Identify explicit FIG./FIGURE references and clear component reference "
    "numerals in prose; do not treat years, claim numbers, citations, quantities, "
    "or line numbers as callouts. Confidence is 0 to 1."
)


async def _review_text_batch(
    client: ResponsesClient, chunk: list[dict[str, Any]], *, _depth: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """Retry a truncated text response by bisecting only the affected batch."""
    try:
        result = await client.structured(
            name="patent_text_review",
            schema=_TEXT_SCHEMA,
            instructions=_TEXT_REVIEW_INSTRUCTIONS,
            content=[
                {
                    "type": "input_text",
                    "text": "Review these stable patent lines:\n"
                    + json.dumps(chunk, ensure_ascii=False),
                }
            ],
        )
        return [result], 0
    except StructuredResponseIncomplete:
        if len(chunk) <= 1 or _depth >= 3:
            # One pathological line must not discard successful review work for
            # the rest of the patent. Bound retries so a provider-side problem
            # cannot create an unexpectedly large bill.
            return [], len(chunk)
        middle = len(chunk) // 2
        left, left_skipped = await _review_text_batch(
            client, chunk[:middle], _depth=_depth + 1
        )
        right, right_skipped = await _review_text_batch(
            client, chunk[middle:], _depth=_depth + 1
        )
        return left + right, left_skipped + right_skipped


async def review_text(
    client: ResponsesClient,
    payload: dict[str, Any],
    settings: Settings,
    *,
    on_progress: ProgressCallback | None = None,
    progress_offset: int = 0,
    total_units: int = 1,
) -> dict[str, int]:
    entries = payload.get("entries") or []
    by_id = {str(entry.get("entry_id")): entry for entry in entries if isinstance(entry, dict)}
    corrections = 0
    skipped_text_batches = 0
    figure_mentions: list[dict[str, Any]] = []
    numeral_mentions: list[dict[str, Any]] = []
    chunks = _chunks(entries, settings.ai_scan_text_chunk_chars)
    for chunk_index, chunk in enumerate(chunks, start=1):
        results, skipped = await _review_text_batch(client, chunk)
        skipped_text_batches += skipped
        for result in results:
            corrections += _apply_text_review_result(
                result,
                by_id=by_id,
                figure_mentions=figure_mentions,
                numeral_mentions=numeral_mentions,
                settings=settings,
            )
        if on_progress is not None:
            await on_progress(
                "ai_scan_text",
                f"Reviewing patent text ({chunk_index}/{len(chunks)})",
                progress_offset + chunk_index,
                total_units,
            )
    if figure_mentions:
        existing = payload.get("figure_mentions") or []
        keyed = {(x.get("entry_id"), tuple(x.get("span", []))): x for x in existing}
        for mention in figure_mentions:
            keyed[(mention["entry_id"], tuple(mention["span"]))] = mention
        payload["figure_mentions"] = list(keyed.values())
    if numeral_mentions:
        existing = payload.get("numeral_mentions") or []
        keyed = {(x.get("entry_id"), x.get("value"), tuple(x.get("span", []))): x for x in existing}
        for mention in numeral_mentions:
            keyed[(mention["entry_id"], mention["value"], tuple(mention["span"]))] = mention
        payload["numeral_mentions"] = list(keyed.values())
    return {
        "corrected_lines": corrections,
        "figure_mentions": len(figure_mentions),
        "callout_mentions": len(numeral_mentions),
        "skipped_text_batches": skipped_text_batches,
    }


def _apply_text_review_result(
    result: dict[str, Any],
    *,
    by_id: dict[str, dict[str, Any]],
    figure_mentions: list[dict[str, Any]],
    numeral_mentions: list[dict[str, Any]],
    settings: Settings,
) -> int:
    corrections = 0
    for item in result.get("corrections", []):
        entry = by_id.get(str(item.get("entry_id")))
        corrected = str(item.get("corrected_text", "")).strip()
        confidence = float(item.get("confidence", 0) or 0)
        if not entry or confidence < 0.8 or not corrected or len(corrected) > 8_000:
            continue
        original = str(entry.get("display_text") or entry.get("source_text") or "")
        ratio = SequenceMatcher(None, original, corrected).ratio()
        if ratio < 0.72 or corrected == original:
            continue
        entry["display_text"] = corrected
        provenance = entry.setdefault("provenance", {})
        provenance["alignment_method"] = "ai_cleanup"
        provenance["alignment_score"] = round(confidence, 4)
        provenance["provider"] = settings.openai_model
        corrections += 1
    for item in result.get("figure_mentions", []):
        if float(item.get("confidence", 0) or 0) < 0.7:
            continue
        entry = by_id.get(str(item.get("entry_id")))
        raw = str(item.get("raw_text", "")).strip()
        text = str(entry.get("display_text", "")) if entry else ""
        span = _span(text, raw)
        ids = [fid for value in item.get("figure_ids", []) if (fid := _figure_id(value))]
        if entry and span and ids:
            figure_mentions.append(
                {
                    "entry_id": entry["entry_id"],
                    "raw_text": raw,
                    "span": span,
                    "figure_ids": ids,
                }
            )
    for item in result.get("callout_mentions", []):
        if float(item.get("confidence", 0) or 0) < 0.82:
            continue
        entry = by_id.get(str(item.get("entry_id")))
        value = str(item.get("value", "")).strip().upper()
        text = str(entry.get("display_text", "")) if entry else ""
        match = re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", text)
        if entry and match and _CALLOUT_RE.fullmatch(value):
            numeral_mentions.append(
                {
                    "entry_id": entry["entry_id"],
                    "value": value,
                    "component_label": str(item.get("component_label", "")).strip() or None,
                    "span": [match.start(), match.end()],
                }
            )
    return corrections


def _drawing_pages(payload: dict[str, Any], max_pages: int) -> tuple[list[int], bool]:
    page_count = int(payload.get("page_count", 0) or 0)
    occurrence_pages = {
        int(item.get("page_index", -1)) for item in payload.get("figure_occurrences", [])
    }
    entry_pages = [
        int(item.get("page_index", 0))
        for item in payload.get("entries", [])
        if isinstance(item, dict)
    ]
    first_spec = min(entry_pages) if entry_pages else min(page_count, 20)
    pages = sorted(
        page
        for page in (occurrence_pages | set(range(1, max(1, first_spec))))
        if 0 <= page < page_count
    )
    if not pages:
        pages = list(range(1, min(page_count, 20)))
    return pages[:max_pages], len(pages) > max_pages


def _page_image(pdf: Any, page_index: int) -> str:
    page = pdf[page_index]
    image = page.render(scale=1.7).to_pil().convert("RGB")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


async def review_drawings(
    client: ResponsesClient,
    payload: dict[str, Any],
    pdf_bytes: bytes,
    settings: Settings,
    *,
    on_progress: ProgressCallback | None = None,
    progress_offset: int = 0,
    total_units: int = 1,
) -> dict[str, int | bool]:
    import pypdfium2 as pdfium

    pages, capped = _drawing_pages(payload, settings.ai_scan_max_drawing_pages)
    expected: set[str] = set()
    for mention in payload.get("figure_mentions", []):
        expected.update(
            fid for value in mention.get("figure_ids", []) if (fid := _figure_id(value))
        )
    for entry in payload.get("entries", []):
        for match in _FIGURE_RE.finditer(str(entry.get("display_text", ""))):
            if fid := _figure_id(match.group(1)):
                expected.add(fid)

    figures: list[dict[str, Any]] = []
    callouts: list[dict[str, Any]] = []
    skipped_drawing_pages = 0
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        for drawing_index, page_index in enumerate(pages, start=1):
            try:
                result = await client.structured(
                    name="patent_drawing_review",
                    schema=_DRAWING_SCHEMA,
                    max_output_tokens=16_000,
                    instructions=(
                        "Inspect one US patent drawing sheet. Ignore every header, including "
                        "patent numbers and 'Sheet X of Y'. Locate only visibly printed "
                        "FIG/FIGURE labels "
                        "and unambiguous component reference-number callouts in or immediately "
                        "around the drawings. Subfigures such as 14a and 14b are distinct. Do not "
                        "infer a hidden label. Boxes use [x0,y0,x1,y1] coordinates normalized from "
                        "0 to 1000. Confidence is 0 to 1. Associate a callout with a figure only "
                        "when visually clear."
                    ),
                    content=[
                        {
                            "type": "input_text",
                            "text": (
                                f"PDF page index: {page_index}. Figure IDs referenced by the "
                                "specification: "
                                f"{', '.join(sorted(expected)) or 'unknown'}."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": _page_image(pdf, page_index),
                            "detail": "high",
                        },
                    ],
                )
            except StructuredResponseIncomplete:
                skipped_drawing_pages += 1
                if on_progress is not None:
                    await on_progress(
                        "ai_scan_drawings",
                        f"Reviewing drawing pages ({drawing_index}/{len(pages)})",
                        progress_offset + drawing_index,
                        total_units,
                    )
                continue
            for item in result.get("figures", []):
                fid = _figure_id(item.get("figure_id"))
                box = _safe_box(item.get("box"))
                confidence = float(item.get("confidence", 0) or 0)
                if fid and box and confidence >= 0.65 and (not expected or fid in expected):
                    figures.append(
                        {
                            "figure_id": fid,
                            "page_index": page_index,
                            "box": box,
                            "confidence": confidence,
                            "detection_score": confidence,
                            "method": "ai_vision",
                        }
                    )
            for index, item in enumerate(result.get("callouts", [])):
                value = str(item.get("value", "")).strip().upper()
                fid = _figure_id(item.get("figure_id"))
                box = _safe_box(item.get("box"))
                confidence = float(item.get("confidence", 0) or 0)
                if box and confidence >= 0.72 and _CALLOUT_RE.fullmatch(value):
                    callouts.append(
                        {
                            "callout_id": f"ai:{page_index}:{value}:{index}",
                            "value": value,
                            "page_index": page_index,
                            "box": box,
                            "figure_id": fid,
                            "confidence": confidence,
                            "detection_score": confidence,
                            "method": "ai_vision",
                        }
                    )
            if on_progress is not None:
                await on_progress(
                    "ai_scan_drawings",
                    f"Reviewing drawing pages ({drawing_index}/{len(pages)})",
                    progress_offset + drawing_index,
                    total_units,
                )
    finally:
        pdf.close()

    if figures:
        # A confident AI location replaces prior locations for that exact ID;
        # unrelated deterministic detections remain available.
        ai_ids = {item["figure_id"] for item in figures}
        retained = [
            item
            for item in payload.get("figure_occurrences", [])
            if item.get("figure_id") not in ai_ids
        ]
        payload["figure_occurrences"] = retained + figures
    if callouts:
        keyed = {
            (item.get("page_index"), item.get("value"), tuple(item.get("box", []))): item
            for item in payload.get("callout_occurrences", [])
        }
        for item in callouts:
            keyed[(item["page_index"], item["value"], tuple(item["box"]))] = item
        payload["callout_occurrences"] = list(keyed.values())
    _rebuild_associations(payload)
    return {
        "figures": len(figures),
        "drawing_callouts": len(callouts),
        "pages_capped": capped,
        "skipped_drawing_pages": skipped_drawing_pages,
    }


def _rebuild_associations(payload: dict[str, Any]) -> None:
    by_value: dict[str, list[dict[str, Any]]] = {}
    for occurrence in payload.get("callout_occurrences", []):
        by_value.setdefault(str(occurrence.get("value", "")).upper(), []).append(occurrence)
    associations: list[dict[str, Any]] = []
    for mention in payload.get("numeral_mentions", []):
        matches = by_value.get(str(mention.get("value", "")).upper(), [])
        ids = [str(item.get("callout_id")) for item in matches]
        associations.append(
            {
                "entry_id": mention.get("entry_id"),
                "value": mention.get("value"),
                "span": mention.get("span", [0, 0]),
                "status": "verified" if len(ids) == 1 else "ambiguous" if ids else "unresolved",
                "selected_callout_ids": ids if len(ids) == 1 else [],
                "candidate_callout_ids": ids,
            }
        )
    payload["mention_associations"] = associations


async def scan_artifact(
    *,
    artifact_blob: bytes,
    pdf_bytes: bytes,
    settings: Settings,
    on_usage: UsageCallback,
    on_progress: ProgressCallback | None = None,
) -> tuple[dict[str, Any], dict[str, int | bool]]:
    payload = json.loads(gzip.decompress(artifact_blob))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError("Active artifact has an invalid payload")
    reviewed = copy.deepcopy(payload)
    client = ResponsesClient(settings, on_usage)
    text_units = len(_chunks(reviewed.get("entries") or [], settings.ai_scan_text_chunk_chars))
    drawing_pages, _ = _drawing_pages(reviewed, settings.ai_scan_max_drawing_pages)
    drawing_units = len(drawing_pages)
    total_units = max(1, text_units + drawing_units + 1)
    if on_progress is not None:
        await on_progress(
            "ai_scan_text",
            f"Reviewing patent text (0/{text_units})",
            0,
            total_units,
        )
    text_summary = await review_text(
        client,
        reviewed,
        settings,
        on_progress=on_progress,
        total_units=total_units,
    )
    drawing_summary = await review_drawings(
        client,
        reviewed,
        pdf_bytes,
        settings,
        on_progress=on_progress,
        progress_offset=text_units,
        total_units=total_units,
    )
    summary: dict[str, int | bool] = {**text_summary, **drawing_summary}
    quality = reviewed.setdefault("quality", {})
    quality["ai_scan"] = {"model": settings.openai_model, **summary}
    if drawing_summary["pages_capped"]:
        reviewed.setdefault("warnings", []).append(
            "AI drawing review reached the configured page limit"
        )
    skipped_text = int(text_summary.get("skipped_text_batches", 0))
    skipped_drawings = int(drawing_summary.get("skipped_drawing_pages", 0))
    if skipped_text or skipped_drawings:
        reviewed.setdefault("warnings", []).append(
            "AI review retained original data for "
            f"{skipped_text} text lines and {skipped_drawings} drawing pages "
            "with incomplete responses"
        )
    return reviewed, summary
