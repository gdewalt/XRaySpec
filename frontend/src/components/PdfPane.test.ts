import { describe, expect, it } from "vitest";

import { adjustedPdfScale } from "./PdfPane";

describe("PDF magnification", () => {
  it("moves one step in either direction", () => {
    expect(adjustedPdfScale(1.15, 1)).toBe(1.3);
    expect(adjustedPdfScale(1.15, -1)).toBe(1);
  });

  it("stays inside the supported scale range", () => {
    expect(adjustedPdfScale(0.5, -1)).toBe(0.5);
    expect(adjustedPdfScale(3, 1)).toBe(3);
  });
});
