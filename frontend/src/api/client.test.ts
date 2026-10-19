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

  it("downloads the source PDF with the authenticated API client", async () => {
    const pdf = new Blob(["pdf-bytes"], { type: "application/pdf" });
    const fetchMock = vi.fn().mockResolvedValue(new Response(pdf, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getSourcePdf("doc_1")).resolves.toEqual(pdf);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/doc_1/source.pdf",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });
});
