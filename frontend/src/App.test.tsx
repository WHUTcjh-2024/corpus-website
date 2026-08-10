import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  anonymousSession,
  authenticatedSession,
  jsonResponse,
  publicOverview,
} from "./test/fixtures";

type ApiScenario = {
  isPublicOverviewAvailable?: boolean;
  loginError?: string;
  session?: typeof anonymousSession;
};

function installApiScenario({
  isPublicOverviewAvailable = true,
  loginError,
  session = anonymousSession,
}: ApiScenario = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/session/") {
      return Promise.resolve(jsonResponse(session));
    }
    if (path === "/api/public-corpora/") {
      return Promise.resolve(
        isPublicOverviewAvailable
          ? jsonResponse(publicOverview)
          : new Response(null, { status: 503, statusText: "Unavailable" }),
      );
    }
    if (path === "/api/csrf/") {
      return Promise.resolve(jsonResponse({ csrf_token: "csrf-test-token" }));
    }
    if (path === "/api/auth/login/") {
      return Promise.resolve(
        loginError
          ? jsonResponse({ detail: loginError }, { status: 400 })
          : jsonResponse({ redirect_to: "/accounts/dashboard/" }),
      );
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("App", () => {
  it("hydrates the public catalogue and authenticated session", async () => {
    installApiScenario({ session: authenticatedSession });
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "中国社会各阶级的分析" }),
    ).toBeInTheDocument();
    expect(screen.getByText("研究员，可以使用全部已授权功能。")).toBeInTheDocument();
    expect(screen.getByText("2 组样本")).toBeInTheDocument();
  });

  it("prompts anonymous users before protected navigation", async () => {
    const user = userEvent.setup();
    installApiScenario();
    render(<App />);
    await screen.findByText("2 组样本");

    await user.click(screen.getByRole("link", { name: "检索中心" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "“检索中心”需要登录后使用，请先完成首页登录。",
    );
  });

  it("renders server-provided login failures and restores the submit button", async () => {
    const user = userEvent.setup();
    const fetchMock = installApiScenario({ loginError: "账号或密码错误" });
    render(<App />);
    await screen.findByText("2 组样本");

    await user.type(screen.getByPlaceholderText("请输入用户名"), "test");
    await user.type(screen.getByPlaceholderText("请输入密码"), "wrong");
    await user.click(screen.getByRole("button", { name: "登录平台" }));

    expect(await screen.findByText("账号或密码错误")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录平台" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login/",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ username: "test", password: "wrong" }),
      }),
    );
  });

  it("respects the initial login-required query parameter", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?login=required");
    installApiScenario();
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "请先登录，再使用平台检索与分析功能。",
    );
    await user.click(screen.getByRole("button", { name: "关闭登录提示" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a public catalogue outage without breaking login", async () => {
    installApiScenario({ isPublicOverviewAvailable: false });
    render(<App />);

    expect(
      await screen.findByText("公开目录暂时无法读取，请刷新页面重试。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录平台" })).toBeInTheDocument();
  });
});
