import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { authenticatedSession, anonymousSession } from "../test/fixtures";
import { LoginPanel, type LoginPanelProps } from "./LoginPanel";

function loginPanelProps(overrides: Partial<LoginPanelProps> = {}): LoginPanelProps {
  return {
    isAttentionVisible: false,
    isSubmitting: false,
    loginMessage: "",
    loginPrompt: "",
    password: "",
    session: anonymousSession,
    username: "",
    containerRef: createRef<HTMLDivElement>(),
    usernameInputRef: createRef<HTMLInputElement>(),
    onDismissPrompt: vi.fn(),
    onPasswordChange: vi.fn(),
    onSubmit: vi.fn((event) => event.preventDefault()),
    onUsernameChange: vi.fn(),
    ...overrides,
  };
}

describe("LoginPanel", () => {
  it("exposes a controlled login form and validation feedback", () => {
    const props = loginPanelProps({ loginMessage: "账号或密码错误" });
    render(<LoginPanel {...props} />);

    fireEvent.change(screen.getByPlaceholderText("请输入用户名"), {
      target: { value: "test" },
    });
    fireEvent.change(screen.getByPlaceholderText("请输入密码"), {
      target: { value: "secret" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "登录平台" }).closest("form")!);

    expect(props.onUsernameChange).toHaveBeenCalledWith("test");
    expect(props.onPasswordChange).toHaveBeenCalledWith("secret");
    expect(props.onSubmit).toHaveBeenCalledOnce();
    expect(screen.getByText("账号或密码错误")).toBeInTheDocument();
  });

  it("shows and dismisses the protected-navigation prompt", async () => {
    const user = userEvent.setup();
    const props = loginPanelProps({
      isAttentionVisible: true,
      loginPrompt: "请先登录",
    });
    render(<LoginPanel {...props} />);

    expect(screen.getByRole("alert")).toHaveTextContent("请先登录");
    await user.click(screen.getByRole("button", { name: "关闭登录提示" }));
    expect(props.onDismissPrompt).toHaveBeenCalledOnce();
  });

  it("renders the authenticated account state instead of the form", () => {
    render(<LoginPanel {...loginPanelProps({ session: authenticatedSession })} />);

    expect(screen.getByText("研究员，可以使用全部已授权功能。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "进入工作台" })).toHaveAttribute(
      "href",
      "/accounts/dashboard/",
    );
    expect(screen.queryByRole("button", { name: "登录平台" })).not.toBeInTheDocument();
  });
});
