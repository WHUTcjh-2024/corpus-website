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
});
