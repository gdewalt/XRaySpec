"""Persistent PDF annotation wire schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PdfAnnotationKind = Literal["bookmark", "note", "highlight", "drawing"]


def _unit(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _validate_geometry(kind: PdfAnnotationKind, geometry: dict[str, Any]) -> None:
    version = geometry.get("version", 1)
    if not isinstance(version, int) or not 1 <= version <= 2:
        raise ValueError("annotation geometry version must be 1 or 2")
    opacity = geometry.get("opacity")
    if opacity is not None and (
        not isinstance(opacity, (int, float))
        or isinstance(opacity, bool)
        or not 0.1 <= opacity <= 1
    ):
        raise ValueError("annotation opacity is invalid")
    if kind in ("bookmark", "note"):
        if not (_unit(geometry.get("x")) and _unit(geometry.get("y"))):
            raise ValueError("point annotations require normalized x and y")
        collapsed = geometry.get("collapsed")
        if collapsed is not None and not isinstance(collapsed, bool):
            raise ValueError("collapsed must be a boolean")
        for key in ("width", "height"):
            value = geometry.get(key)
            if value is not None and not _unit(value):
                raise ValueError(f"{key} must be normalized")
        return
    if kind == "highlight":
        values = [geometry.get(key) for key in ("x0", "y0", "x1", "y1")]
        if not all(_unit(value) for value in values):
            raise ValueError("highlights require a normalized box")
        if values[2] <= values[0] or values[3] <= values[1]:
            raise ValueError("highlight box must have positive area")
        rects = geometry.get("rects")
        if rects is not None and (
            not isinstance(rects, list)
            or len(rects) > 500
            or not all(
                isinstance(rect, list)
                and len(rect) == 4
                and all(_unit(value) for value in rect)
                and rect[2] > rect[0]
                and rect[3] > rect[1]
                for rect in rects
            )
        ):
            raise ValueError("highlight rects must be normalized positive boxes")
        ranges = geometry.get("text_ranges")
        if ranges is not None and (
            not isinstance(ranges, list)
            or len(ranges) > 500
            or not all(
                isinstance(item, dict)
                and isinstance(item.get("entry_id"), str)
                and 0 <= item.get("start", -1) <= item.get("end", -1)
                and isinstance(item.get("quote", ""), str)
                for item in ranges
            )
        ):
            raise ValueError("highlight text ranges are invalid")
        return
    points = geometry.get("points")
    if not isinstance(points, list) or not 2 <= len(points) <= 2000:
        raise ValueError("drawings require 2 to 2000 normalized points")
    if not all(
        isinstance(point, list)
        and len(point) == 2
        and _unit(point[0])
        and _unit(point[1])
        for point in points
    ):
        raise ValueError("drawing points must be normalized coordinate pairs")
    width = geometry.get("stroke_width")
    if width is not None and (
        not isinstance(width, (int, float)) or isinstance(width, bool) or not 0.001 <= width <= 0.03
    ):
        raise ValueError("drawing stroke width is invalid")


class PdfAnnotationCreate(BaseModel):
    kind: PdfAnnotationKind
    page_index: int = Field(ge=0, le=10_000)
    geometry: dict[str, Any]
    color: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_geometry(self):
        _validate_geometry(self.kind, self.geometry)
        return self


class PdfAnnotationUpdate(BaseModel):
    geometry: dict[str, Any] | None = None
    color: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=4000)


class PdfAnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    kind: PdfAnnotationKind
    page_index: int
    geometry: dict[str, Any]
    color: str | None = None
    note: str | None = None
    created_at: datetime
