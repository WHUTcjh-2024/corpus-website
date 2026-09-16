import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HomeFooter } from "./HomeFooter";


describe("HomeFooter", () => {
  it("links every public legal notice", () => {
    render(<HomeFooter />);

    expect(screen.getByRole("link", { name: "隐私政策" })).toHaveAttribute(
      "href",
      "/privacy/",
    );
    expect(screen.getByRole("link", { name: "用户协议" })).toHaveAttribute(
      "href",
      "/terms/",
    );
    expect(screen.getByRole("link", { name: "版权投诉" })).toHaveAttribute(
      "href",
      "/copyright/",
    );
  });

  it("renders a configurable ICP filing link and a safe placeholder", () => {
    document.body.dataset.icpLicense = "鄂ICP备12345678号-1";
    const { rerender } = render(<HomeFooter />);
    expect(screen.getByRole("link", { name: "鄂ICP备12345678号-1" })).toHaveAttribute(
      "href",
      "https://beian.miit.gov.cn/",
    );

    delete document.body.dataset.icpLicense;
    rerender(<HomeFooter />);
    expect(screen.getByText("ICP备案号：待填写")).toBeInTheDocument();
  });
});
