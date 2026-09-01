"""Workers (DESIGN.md §5, §6, §10).

Two deployables share this package as separate entrypoints:
  - the extraction worker (native + OCR + coordination) — NO network egress;
  - the enrichment/fetch worker — restricted egress (Google Patents + images).

The security boundary between them is the no-internet vs allowlisted-egress
split (§5.1), not scale.
"""
