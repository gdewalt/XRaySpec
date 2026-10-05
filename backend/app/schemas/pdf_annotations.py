"""Persistent PDF annotation wire schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PdfAnnotationKind = Literal["bookmark", "note", "highlight", "drawing"]


def _unit(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


class PdfAnnotationCreate(BaseModel):
    kind: PdfAnnotationKind
    page_index: int = Field(ge=0, le=10_000)
    geometry: dict[str, Any]
    color: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_geometry(self):
        geometry = self.geometry
        if self.kind in ("bookmark", "note"):
            if not (_unit(geometry.get("x")) and _unit(geometry.get("y"))):
                raise ValueError("point annotations require normalized x and y")
        elif self.kind == "highlight":
            values = [geometry.get(key) for key in ("x0", "y0", "x1", "y1")]
            if not all(_unit(value) for value in values):
                raise ValueError("highlights require a normalized box")
            if values[2] <= values[0] or values[3] <= values[1]:
                raise ValueError("highlight box must have positive area")
        else:
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
        if self.kind == "note" and not (self.note or "").strip():
            raise ValueError("notes require text")
        return self


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
