import { describe, expect, it } from "vitest";

import { documentMenuPlacement } from "./DocumentsView";

describe("patent action menu placement", () => {
  it("opens below a row when there is sufficient room", () => {
    const placement = documentMenuPlacement(
      { top: 100, right: 900, bottom: 140 },
      1024,
      800,
      300,
    );

    expect(placement.top).toBe(146);
    expect(placement.bottom).toBeUndefined();
    expect(placement.left).toBe(660);
  });

  it("opens above a row at the bottom of the library", () => {
    const placement = documentMenuPlacement(
      { top: 720, right: 900, bottom: 760 },
      1024,
      800,
      300,
    );

    expect(placement.top).toBeUndefined();
    expect(placement.bottom).toBe(86);
    expect(placement.maxHeight).toBe(706);
  });
});
