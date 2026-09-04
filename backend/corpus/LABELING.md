# Labeling a corpus document (DESIGN.md §19.1)

The golden harness scores the engine against hand-labeled ground truth. Labeling
from scratch is impractical, so the flow is **bootstrap → correct → verify**: the
scaffold generator pre-fills a label file from an extraction run, and you correct
the engine's guesses in place. A scaffold is *not* ground truth until reviewed.

## 0. Pick the document

Choose to fill gaps in the [stratification](README.md) (grant vs application;
born-digital / scanned / hybrid; center-gutter vs single-column; irregular gutter
numbers; rotated / multi-figure drawings). Keep the PDF out of the repo; reference
it only by `doc_id` (e.g. `US12262260B2`).

## 1. Generate the scaffold

```bash
cd backend
python -m app.eval.scaffold /path/to/US12262260B2.pdf --doc-id US12262260B2 --doc-type auto
```

Options: `--doc-type grant|application|auto`, `--sample N` (keep N evenly spaced
reference lines instead of all — 10–20 is plenty per doc), `--out DIR` (default
`corpus/`), `--force` (overwrite). It writes `corpus/<doc_id>.json` pre-filled with
every field, plus a `_scaffold` block. `_scaffold` (and any unknown key) is ignored
by the loader, so the file already scores — reviewing is what makes the score mean
something.

## 2. Correct, field by field

Open the document in the viewer (`Open` in the app) beside the JSON; the text pane
shows each line's `col:line` and the PDF pane shows its box. The pre-filled values
are **guesses** — fix them.

- **`spec_page_indices`** — 0-based pages that are specification prose (not cover,
  drawings, or back matter). The engine already dropped drawings; add any spec page
  it missed and remove any non-spec page.
- **`references[]`** — for each line: is `column`/`printed_line` (grant) or
  `paragraph` (application) the number *printed on the page*? Fix wrong ones. This
  is the point of the corpus: center-gutter line numbers are the known weak spot.
  - **Delete** a reference whose line the engine hallucinated or mis-split.
  - **Add** a reference the engine missed: `{"ordinal": <n>, "column": …,
    "printed_line": …}` — `ordinal` is reading-order position; keep the list ordered.
  - **`box`** — only needs correcting where it's visibly off; median IoU is forgiving.
    Drop the `box` key on a line you don't want to score geometrically.
- **`callouts[]`** — on each drawing sheet, one entry per printed reference numeral:
  `{"value": "104", "box": [...], "page_index": <n>}`. Add numerals Tesseract missed
  (the measured low-yield case), and delete anything that isn't a callout (a year, a
  sheet/figure number, a dimension).
- **`associations[]`** — for numeral mentions you care about, set the correct
  `status`: `verified` (unambiguous single callout in the figure in context),
  `probable`, `ambiguous` (same numeral in >1 in-scope figure — the chooser case),
  or `unresolved`. **Include a few deliberately `ambiguous` ones** — they calibrate
  the deferred ranker and guard the "no silent verified" invariant.
- **`wrong_document`** — set `true` only for a fixture built to test substitution
  rejection (its provider clean text is a *different* patent). Leave `false` normally.
- **`notes`** — era, layout, anything a future reviewer should know.

## 3. Verify

Set `_scaffold.verified` to `true` when the file is reviewed. (Leaving it `false`
is the signal that the file is still raw engine output.) You can drop the whole
`_scaffold` block once verified — it's only a review aid.

## 4. Check it scores

Put the PDF where the harness can find it — `corpus/pdfs/<doc_id>.pdf` by default,
or set `XRAY_CORPUS_PDF_DIR` (both are gitignored) — then:

```bash
python -m app.eval.run          # seed corpus + every VERIFIED corpus/*.json, gated
```

The harness discovers verified label files, extracts each one's PDF, and folds the
results into the aggregate; a label whose PDF (or the extraction libraries) is
unavailable is reported as *skipped*, not failed, and an unreviewed scaffold
(`_scaffold.verified` false) is ignored until you flip it. As real documents land,
the two deferred limits (§19.1) start getting real numbers.

## Effort

Aim for 20–30 documents before trusting the gates, toward ≥150 for launch. Reserve a
locked holdout set you never tune against. Sampling ~10–20 references per doc keeps
each file to a few minutes of review once the scaffold has done the transcription.
