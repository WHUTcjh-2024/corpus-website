import type { FormEvent, MouseEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { fetchPublicCorpusOverview, fetchSession } from "./api/client";
import type { SessionPayload } from "./api/types";
import { CorpusShowcase } from "./home/CorpusShowcase";
import { EMPTY_PUBLIC_CORPUS_OVERVIEW } from "./home/constants";
import { HomeFooter } from "./home/HomeFooter";
import { HomeHeader } from "./home/HomeHeader";
import { HomeHero } from "./home/HomeHero";
import { safeInternalDestination } from "./home/utils";

function App() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [publicCorpusOverview, setPublicCorpusOverview] = useState(
    EMPTY_PUBLIC_CORPUS_OVERVIEW,
  );
  const [publicCorpusLoading, setPublicCorpusLoading] = useState(true);
  const [publicCorpusError, setPublicCorpusError] = useState(false);
  const [loginMessage, setLoginMessage] = useState("");
  const [loginPrompt, setLoginPrompt] = useState("");
  const [pendingDestination, setPendingDestination] = useState<string | null>(null);
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [loginAttention, setLoginAttention] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const loginSectionRef = useRef<HTMLDivElement>(null);
  const usernameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchPublicCorpusOverview()
      .then((payload) => {
        setPublicCorpusOverview(payload);
        setPublicCorpusError(false);
      })
      .catch(() => setPublicCorpusError(true))
      .finally(() => setPublicCorpusLoading(false));
  }, []);

  useEffect(() => {
    let isActive = true;

    fetchSession()
      .then((payload) => {
        if (isActive) {
          setSession(payload);
        }
      })
      .catch(() => {
        if (isActive) {
          setSession(null);
        }
      })
      .finally(() => {
        if (isActive) {
          setSessionLoaded(true);
        }
      });

    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedDestination = safeInternalDestination(params.get("next"));
    if (requestedDestination) {
      setPendingDestination(requestedDestination);
      setLoginPrompt("此功能需要登录，请在首页完成登录后继续。");
      setLoginAttention(true);
    } else if (params.get("login") === "required") {
      setLoginPrompt("请先登录，再使用平台检索与分析功能。");
      setLoginAttention(true);
    }
  }, []);

  useEffect(() => {
    if (!loginAttention) {
      return;
    }

    loginSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    const focusTimer = window.setTimeout(() => usernameInputRef.current?.focus(), 450);
    const attentionTimer = window.setTimeout(() => setLoginAttention(false), 1600);
    return () => {
      window.clearTimeout(focusTimer);
      window.clearTimeout(attentionTimer);
    };
  }, [loginAttention]);

  function promptForLogin(label: string, href: string) {
    setPendingDestination(href);
    setLoginPrompt(`“${label}”需要登录后使用，请先完成首页登录。`);
    setLoginAttention(false);
    window.setTimeout(() => setLoginAttention(true), 0);
  }

  async function handleProtectedNavigation(
    event: MouseEvent<HTMLAnchorElement>,
    label: string,
    href: string,
  ) {
    event.preventDefault();
    let currentSession = session;

    if (!sessionLoaded) {
      try {
        currentSession = await fetchSession();
        setSession(currentSession);
      } catch {
        currentSession = null;
      } finally {
        setSessionLoaded(true);
      }
    }

    if (currentSession?.is_authenticated) {
      window.location.assign(href);
      return;
    }

    promptForLogin(label, href);
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginMessage("");
    setIsSubmitting(true);

    try {
      const csrfResponse = await fetch("/api/csrf/", {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!csrfResponse.ok) {
        throw new Error("登录初始化失败，请刷新页面后重试。");
      }
      const { csrf_token: csrfToken } = (await csrfResponse.json()) as {
        csrf_token: string;
      };

      const loginResponse = await fetch("/api/auth/login/", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({ username, password }),
      });

      if (!loginResponse.ok) {
        const payload = (await loginResponse.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(payload?.detail ?? "登录失败，请检查账号和密码。");
      }

      const payload = (await loginResponse.json()) as { redirect_to?: string };
      window.location.assign(
        safeInternalDestination(pendingDestination) ??
          safeInternalDestination(payload.redirect_to) ??
          "/accounts/dashboard/",
      );
    } catch (error) {
      setLoginMessage(error instanceof Error ? error.message : "登录失败，请稍后重试。");
      setIsSubmitting(false);
    }
  }

  return (
    <div className="home-body">
      <HomeHeader
        isAuthenticated={Boolean(session?.is_authenticated)}
        onProtectedNavigation={(event, label, href) =>
          void handleProtectedNavigation(event, label, href)
        }
      />
      <main className="campus-home" aria-label="智能语料检索与分析平台">
        <HomeHero
          loginPanel={{
            isAttentionVisible: loginAttention,
            isSubmitting,
            loginMessage,
            loginPrompt,
            password,
            session,
            username,
            containerRef: loginSectionRef,
            usernameInputRef,
            onDismissPrompt: () => setLoginPrompt(""),
            onPasswordChange: setPassword,
            onSubmit: handleLogin,
            onUsernameChange: setUsername,
          }}
        />
        <CorpusShowcase
          hasError={publicCorpusError}
          isLoading={publicCorpusLoading}
          overview={publicCorpusOverview}
          onProtectedNavigation={(event, label, href) =>
            void handleProtectedNavigation(event, label, href)
          }
        />
        <HomeFooter />
      </main>
    </div>
  );
}

export default App;
