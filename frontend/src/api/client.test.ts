import { afterEach, describe, expect, it, vi } from "vitest";

import { anonymousSession, jsonResponse, publicOverview } from "../test/fixtures";
import { fetchPublicCorpusOverview, fetchSession } from "./client";

describe("API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requests JSON with session credentials", async () => {
    const fetchMock = vi.fn((path: RequestInfo | URL) => {
      return Promise.resolve(
        String(path).includes("public-corpora")
          ? jsonResponse(publicOverview)
          : jsonResponse(anonymousSession),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchSession()).resolves.toEqual(anonymousSession);
    await expect(fetchPublicCorpusOverview()).resolves.toEqual(publicOverview);
    expect(fetchMock).toHaveBeenCalledWith("/api/session/", {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  });

  it("surfaces the HTTP status when a request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(null, { status: 503, statusText: "Service Unavailable" })),
      ),
    );

    await expect(fetchSession()).rejects.toThrow("503 Service Unavailable");
  });
});
