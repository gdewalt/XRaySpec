"""Pydantic wire schemas — the API shape (DESIGN.md §14).

Kept deliberately separate from ORM models (storage shape) and from the
extraction dataclasses (domain shape). These types are what generate the
frontend's TypeScript via the OpenAPI schema (§25.2).

TODO: add request/response models per API route as Phase 1+ lands.
"""
