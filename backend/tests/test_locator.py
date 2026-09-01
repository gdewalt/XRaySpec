"""First real tests: the typed locator and config contracts (DESIGN.md §8.2, §25.3).

These pass today and pin the two contracts feature code will build on. Golden
extraction tests seeded from the labeled corpus arrive with Phase 2/3.
"""

from __future__ import annotations

from app.extraction import ApplicationLocator, DEFAULT_CONFIG, ExtractionConfig, GrantLocator


def test_grant_locator_renders_col_line() -> None:
    assert GrantLocator(column=3, printed_line=15).render() == "3:15"


def test_application_locator_preserves_leading_zeros() -> None:
    assert ApplicationLocator(paragraph="0042").render() == "[0042]"


def test_locator_kind_is_discriminated() -> None:
    assert GrantLocator(column=1, printed_line=1).kind == "grant"
    assert ApplicationLocator(paragraph="0001").kind == "application"


def test_config_hash_is_stable_and_sensitive() -> None:
    # Same config -> same hash (reproducibility anchor).
    assert DEFAULT_CONFIG.config_hash() == ExtractionConfig().config_hash()
    # A changed threshold -> different hash (invalidates cached artifacts).
    changed = ExtractionConfig(ocr_dpi=200)
    assert changed.config_hash() != DEFAULT_CONFIG.config_hash()
