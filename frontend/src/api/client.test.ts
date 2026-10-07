import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./client";

describe("API error handling", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reports a plain-text server error instead of throwing a JSON syntax error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("Internal Server Error", {
          status: 500,
          statusText: "Internal Server Error",
          headers: { "Content-Type": "text/plain" },
        }),
      ),
    );

    await expect(api.deleteDocument("doc_1")).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: "ApiError",
        status: 500,
        message: "Internal Server Error",
      }),
    );
  });
});
