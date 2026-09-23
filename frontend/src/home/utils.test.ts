import { describe, expect, it } from "vitest";

import { safeInternalDestination } from "./utils";

describe("home utilities", () => {
  it.each([
    [null, null],
    [undefined, null],
    ["", null],
    ["https://example.test", null],
    ["//example.test/path", null],
    ["/corpora/?tool=kwic", "/corpora/?tool=kwic"],
  ])("accepts only same-origin destinations: %s", (value, expected) => {
    expect(safeInternalDestination(value)).toBe(expected);
  });
});
