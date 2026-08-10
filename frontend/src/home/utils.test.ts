import { describe, expect, it } from "vitest";

import { pairedCorpus, rawChineseCorpus } from "../test/fixtures";
import { formatCount, publicCorpusMode, publicCorpusTitle, safeInternalDestination } from "./utils";

describe("home utilities", () => {
  it("derives the public title and display mode from teacher corpus names", () => {
    expect(publicCorpusTitle(rawChineseCorpus)).toBe("中国社会各阶级的分析");
    expect(publicCorpusMode(rawChineseCorpus)).toBe("单语中文");
  });

  it("falls back to API labels for non-teacher and incomplete names", () => {
    const publicCorpus = { ...pairedCorpus, name: "公开项目样本" };
    const incompleteTeacherCorpus = { ...pairedCorpus, name: "老师语料" };

    expect(publicCorpusTitle(publicCorpus)).toBe("公开项目样本");
    expect(publicCorpusMode(publicCorpus)).toBe("中英原文配对");
    expect(publicCorpusMode(incompleteTeacherCorpus)).toBe("中英原文配对");
  });

  it("formats counts for the Chinese locale", () => {
    expect(formatCount(12345)).toBe("12,345");
  });

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
