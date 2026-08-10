import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EMPTY_PUBLIC_CORPUS_OVERVIEW } from "./constants";
import { publicOverview } from "../test/fixtures";
import { CorpusShowcase } from "./CorpusShowcase";

describe("CorpusShowcase", () => {
  it("renders metrics and sorts corpus records by supported corpus type", () => {
    render(
      <CorpusShowcase
        hasError={false}
        isLoading={false}
        overview={publicOverview}
        onProtectedNavigation={vi.fn()}
      />,
    );

    expect(screen.getAllByText("12,345")).toHaveLength(2);
    const records = screen.getAllByRole("article");
    expect(within(records[0]).getByRole("heading", { name: "项目样本" })).toBeInTheDocument();
    expect(
      within(records[1]).getByRole("heading", { name: "中国社会各阶级的分析" }),
    ).toBeInTheDocument();
    expect(within(records[0]).getAllByText("—")).toHaveLength(4);
  });

  it("renders loading and failed catalogue states", () => {
    const { rerender } = render(
      <CorpusShowcase
        hasError={false}
        isLoading
        overview={EMPTY_PUBLIC_CORPUS_OVERVIEW}
        onProtectedNavigation={vi.fn()}
      />,
    );
    expect(screen.getByText("正在读取老师项目语料库…")).toBeInTheDocument();

    rerender(
      <CorpusShowcase
        hasError
        isLoading={false}
        overview={EMPTY_PUBLIC_CORPUS_OVERVIEW}
        onProtectedNavigation={vi.fn()}
      />,
    );
    expect(screen.getByText("公开目录暂时无法读取，请刷新页面重试。")).toBeInTheDocument();
  });

  it("guards access to the research library", async () => {
    const user = userEvent.setup();
    const onProtectedNavigation = vi.fn((event: React.MouseEvent<HTMLAnchorElement>) => {
      event.preventDefault();
    });
    render(
      <CorpusShowcase
        hasError={false}
        isLoading={false}
        overview={EMPTY_PUBLIC_CORPUS_OVERVIEW}
        onProtectedNavigation={onProtectedNavigation}
      />,
    );

    await user.click(screen.getByRole("link", { name: /进入研究库/ }));
    expect(onProtectedNavigation).toHaveBeenCalledWith(
      expect.anything(),
      "项目语料库",
      "/corpora/",
    );
  });
});
