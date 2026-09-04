# Extraction benchmark corpus (DESIGN.md §19.1)

Ground-truth labels for the golden harness (`app/eval/`). Each `*.json` file is one
hand-labeled document: what a **correct** extraction of that patent should produce.
The harness (`python -m app.eval.run`) scores the engine's actual output against
these labels and gates it on the §19.2 provisional release targets.

The seed corpus that ships in the repo is **synthetic** (`app/eval/cases.py`) so the
gates run in CI without copyrighted PDFs or the OCR/PDF libraries. This directory is
where **real labeled patents** go. Grow it per §19.1: 20–30 documents before moving
algorithms, toward ≥150 for launch, stratified across grants/applications,
born-digital/scanned/hybrid, with a locked holdout set. Do **not** commit the source
PDFs here; store them out of band and reference by `doc_id`/hash.

**Don't hand-transcribe — bootstrap.** Run the scaffold generator to pre-fill a label
file from an extraction run, then correct the engine's guesses in place:

```bash
python -m app.eval.scaffold /path/to/US12262260B2.pdf --doc-id US12262260B2 --doc-type auto
```

The full review procedure is in **[LABELING.md](LABELING.md)**.

## Label file schema

```jsonc
{
  "doc_id": "US12262260B2",          // stable id; matches the stored PDF
  "doc_type": "grant",               // "grant" | "application"
  "spec_page_indices": [66, 67, 68], // 0-based specification pages (page roles)
  "references": [                    // one per labeled line, matched by entry ordinal
    {
      "ordinal": 0,                  // 0-based reading order the engine assigns
      "column": 1,                   // grant col:line  (omit for applications)
      "printed_line": 1,
      "paragraph": null,             // application [NNNN] (omit for grants)
      "box": [0.12, 0.09, 0.30, 0.11] // normalized top-left [x0,y0,x1,y1], optional
    }
  ],
  "callouts": [                      // drawing reference-numeral labels
    { "value": "104", "box": [0.20, 0.30, 0.24, 0.33], "page_index": 70 }
  ],
  "associations": [                  // correct mention→callout status
    { "entry_ordinal": 12, "value": "104", "status": "verified" }
  ],
  "wrong_document": false,           // true = clean text is a DIFFERENT patent
  "notes": "era: 2025; two-column; center gutter numbers"
}
```

Coordinates are normalized to `[0,1]` with a top-left origin (§8.2). `status` is one
of `verified | probable | ambiguous | unresolved`; include deliberately ambiguous and
unresolved cases — they calibrate the deferred association ranker (§12.7).

## What the harness measures (§19.1)

- printed/paragraph reference **exact** and **within-one** accuracy;
- entry geometry **box IoU** (median);
- drawing callout **precision/recall**;
- verified mention→callout **association precision**, and **zero** silent verified
  destinations on ambiguous fixtures;
- external aligned **substitution rejection** (100% on wrong-document fixtures).

## Calibration

`app/eval/calibrate.py` sweeps a config threshold across the corpus and reports the
metric curve, so a threshold is chosen from evidence rather than by eye (§19.2, §20).
The shipped demonstration sweeps `alignment_min_ratio` (coverage vs wrong-document
rejection). The **two limits the README defers to §19** — grant printed-line precision
on center-gutter layouts, and drawing-callout yield — are calibrated the same way once
enough real scanned documents are labeled here.
