import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HomeHeader } from "./HomeHeader";

describe("HomeHeader", () => {
  it("routes protected navigation through the authentication guard", async () => {
    const user = userEvent.setup();
    const onProtectedNavigation = vi.fn((event: React.MouseEvent<HTMLAnchorElement>) => {
      event.preventDefault();
    });
    render(
      <HomeHeader
        isAuthenticated={false}
        onProtectedNavigation={onProtectedNavigation}
      />,
    );

    const searchLink = screen.getByRole("link", { name: "检索中心" });
    expect(searchLink).toHaveAttribute("title", "登录后可访问");
    await user.click(searchLink);

    expect(onProtectedNavigation).toHaveBeenCalledWith(
      expect.anything(),
      "检索中心",
      "/corpora/?tool=kwic",
    );
  });

  it("removes the login affordance for authenticated users", () => {
    render(<HomeHeader isAuthenticated onProtectedNavigation={vi.fn()} />);

    const searchLink = screen.getByRole("link", { name: "检索中心" });
    expect(searchLink).not.toHaveAttribute("title");
    expect(searchLink).not.toHaveClass("requires-auth");
  });
});
