import type { EntryDto, FigureMentionDto } from "../api/types";

// Keep figure-link spans anchored to the corrected text users actually see.
// The extraction artifact retains source-text mentions for provenance, but
// provider alignment can change their offsets or recover references OCR missed.
const FIGURE_ID = String.raw`\d+(?:[A-Za-z]|\s+[A-Za-z](?![A-Za-z]))?`;
const FIGURE_REFERENCE = new RegExp(
  String.raw`\b(?:FIGS?|FIGURES?)\s*\.?\s*(${FIGURE_ID}(?:\s*(?:[-–,]|and|to)\s*${FIGURE_ID})*)`,
  "gi",
);
const FIGURE_RANGE = /^(\d+)\s*([A-Za-z]?)\s*(?:[-–]|to)\s*(\d+)\s*([A-Za-z]?)$/i;
const FIGURE_SINGLE = /^(\d+)\s*([A-Za-z]?)$/;

function expandFigureExpression(expression: string): string[] {
  const ids: string[] = [];
  for (const rawPart of expression.split(/\s*(?:,|\band\b)\s*/i)) {
    const part = rawPart.trim();
    if (!part) continue;

    const range = FIGURE_RANGE.exec(part);
    if (range) {
      const [, firstNumber, firstLetter, lastNumber, lastLetter] = range;
      if (firstNumber === lastNumber && firstLetter && lastLetter) {
        for (
          let code = firstLetter.toUpperCase().charCodeAt(0);
          code <= lastLetter.toUpperCase().charCodeAt(0);
          code += 1
        ) {
          ids.push(`${firstNumber}${String.fromCharCode(code)}`);
        }
      } else if (!firstLetter && !lastLetter) {
        const first = Number(firstNumber);
        const last = Number(lastNumber);
        if (last >= first && last - first <= 100) {
          for (let number = first; number <= last; number += 1) ids.push(String(number));
        }
      } else {
        ids.push(`${firstNumber}${firstLetter.toUpperCase()}`);
        ids.push(`${lastNumber}${lastLetter.toUpperCase()}`);
      }
      continue;
    }

    const single = FIGURE_SINGLE.exec(part);
    if (single) ids.push(`${single[1]}${single[2].toUpperCase()}`);
  }
  return ids;
}

/** Detect clickable figure references against provider-corrected display text. */
export function detectDisplayFigureMentions(entries: EntryDto[]): FigureMentionDto[] {
  const mentions: FigureMentionDto[] = [];
  for (const entry of entries) {
    for (const match of entry.display_text.matchAll(FIGURE_REFERENCE)) {
      if (match.index === undefined) continue;
      const figureIds = expandFigureExpression(match[1]);
      if (figureIds.length === 0) continue;
      mentions.push({
        entry_id: entry.entry_id,
        raw_text: match[0],
        span: [match.index, match.index + match[0].length],
        figure_ids: figureIds,
      });
    }
  }
  return mentions;
}
