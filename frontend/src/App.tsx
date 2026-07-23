import {
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  Clock3,
  Download,
  ListFilter,
  LockKeyhole,
  LogIn,
  Mail,
  Search,
  Shuffle,
  UserPlus,
  X,
} from "lucide-react";
import { FormEvent, MouseEvent, useEffect, useRef, useState } from "react";

import { fetchSession } from "./api/client";
import type { SessionPayload } from "./api/types";

const navItems = [
  { label: "首页", href: "/", requiresAuth: false },
  { label: "语料资源", href: "/corpora/", requiresAuth: true },
  { label: "检索中心", href: "/corpora/?tool=kwic", requiresAuth: true },
  { label: "用户语料", href: "/corpora/mine/", requiresAuth: true },
  { label: "对齐工具", href: "/corpora/?tool=parallel", requiresAuth: true },
  { label: "统计分析", href: "/corpora/?tool=statistics", requiresAuth: true },
  { label: "帮助中心", href: "#platform-guide", requiresAuth: false },
];

const resources = [
  { icon: "平", label: "平行语料", value: "128", meta: "双语对齐资源", tone: "blue" },
  { icon: "单", label: "单语语料", value: "96", meta: "多语种研究文本", tone: "green" },
  { icon: "用", label: "用户语料", value: "43", meta: "个人研究空间", tone: "violet" },
  { icon: "容", label: "资源容量", value: "328.7 GB", meta: "安全存储总量", tone: "orange" },
];

const tools = [
  { Icon: Search, title: "KWIC 检索", desc: "关键词上下文检索", href: "/corpora/?tool=kwic", tone: "blue" },
  { Icon: Shuffle, title: "对齐检索", desc: "中英平行语料对照", href: "/corpora/?tool=parallel", tone: "cyan" },
  { Icon: ListFilter, title: "复杂查询", desc: "多条件组合检索", href: "/corpora/", tone: "violet" },
  { Icon: BarChart3, title: "统计分析", desc: "词频、搭配与趋势", href: "/corpora/?tool=statistics", tone: "green" },
  { Icon: Clock3, title: "可视化图表", desc: "多维分析结果呈现", href: "/corpora/?tool=statistics", tone: "orange" },
  { Icon: Download, title: "导出结果", desc: "保存检索分析结果", href: "/exports/", tone: "slate" },
];

const news = [
  { title: "系统维护通知（2025-07-15）", date: "07-14", tag: "公告" },
  { title: "新增语料资源：学术论文语料库", date: "07-12", tag: "资源" },
  { title: "用户指南更新：复杂检索使用说明", date: "07-10", tag: "指南" },
  { title: "平台功能优化升级完成", date: "07-08", tag: "更新" },
  { title: "暑期使用高峰期资源调度说明", date: "07-06", tag: "通知" },
];

function safeInternalDestination(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return null;
  }
  return value;
}

function App() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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
      const { csrf_token: csrfToken } = (await csrfResponse.json()) as { csrf_token: string };

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
        const payload = (await loginResponse.json().catch(() => null)) as { detail?: string } | null;
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
      <header className="platform-navbar">
        <div className="platform-navbar__inner">
          <a className="platform-brand" href="/" aria-label="在线语料库平台首页">
            <img
              className="platform-brand__mark"
              src="/static/img/translation-center-wordmark-black.png"
              alt="武汉理工大学与外国语学院联合标识"
              width="656"
              height="80"
            />
            <span className="platform-brand__center">
              <strong>翻译跨学科研究中心</strong>
              <small>Translation Interdisciplinary Research Center</small>
            </span>
          </a>
          <nav className="public-nav" aria-label="公共导航">
            {navItems.map((item, index) => (
              <a
                className={`${index === 0 ? "active" : ""} ${
                  item.requiresAuth && !session?.is_authenticated ? "requires-auth" : ""
                }`}
                href={item.href}
                key={item.label}
                onClick={
                  item.requiresAuth
                    ? (event) => void handleProtectedNavigation(event, item.label, item.href)
                    : undefined
                }
                title={item.requiresAuth && !session?.is_authenticated ? "登录后可访问" : undefined}
              >
                {item.label}
                {item.requiresAuth && !session?.is_authenticated && (
                  <LockKeyhole aria-hidden="true" size={12} strokeWidth={2.4} />
                )}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <main className="campus-home" aria-label="智能语料检索与分析平台">
        <section className="campus-hero">
          <img
            className="campus-hero__image"
            src="/static/img/whut-campus-hero-banner.png"
            alt="武汉理工大学校园"
            width="1672"
            height="941"
          />
          <div className="campus-hero__shade" aria-hidden="true" />
          <div className="campus-hero__content">
            <p className="campus-hero__eyebrow">Corpus Research Workspace</p>
            <h1>智能语料检索与分析平台</h1>
            <p className="campus-hero__lead">探索语言 · 数据驱动 · 智慧研究</p>

            <section className="campus-hero-statement" aria-label="平台说明与登录">
              <div className="campus-hero-statement__top">
                <div className="campus-hero-statement__heading">
                  <span>Platform Statement</span>
                  <h2>平台说明</h2>
                </div>
                <span className="campus-hero-statement__tag">NSFC · 22BYY022</span>
              </div>

              <div className="campus-hero-statement__content">
                <p>
                  本平台依托国家社会科学基金项目“
                  <strong>马克思主义中国化经典文献汉英平行语料库建设及其综合研究</strong>
                  ”（22BYY022）开发，主要服务于相关汉英平行语料的建设、检索与研究。
                </p>
                <p>
                  同时，平台具备通用语料处理能力，支持多类型单语及双语平行语料的检索、对齐、统计与分析。
                </p>
              </div>

              <div className="campus-hero-statement__meta">
                <span className="campus-hero-statement__meta-card">
                  <small>项目负责人</small>
                  <strong>陈伟教授</strong>
                </span>
                <a className="campus-hero-statement__meta-card" href="mailto:chenweiwhut@126.com">
                  <small>联系邮箱</small>
                  <strong>chenweiwhut@126.com</strong>
                </a>
              </div>

              <div
                className={`campus-login ${loginAttention ? "campus-login--attention" : ""}`}
                id="home-login"
                ref={loginSectionRef}
              >
                {loginPrompt && !session?.is_authenticated && (
                  <div className="campus-login-prompt" role="alert" aria-live="assertive">
                    <LockKeyhole aria-hidden="true" size={20} strokeWidth={2.2} />
                    <div>
                      <strong>请先登录平台</strong>
                      <span>{loginPrompt}</span>
                    </div>
                    <button
                      aria-label="关闭登录提示"
                      className="campus-login-prompt__close"
                      onClick={() => setLoginPrompt("")}
                      type="button"
                    >
                      <X aria-hidden="true" size={17} />
                    </button>
                  </div>
                )}

                {session?.is_authenticated ? (
                  <div className="campus-login-authenticated">
                    <CheckCircle2 aria-hidden="true" size={25} strokeWidth={2.1} />
                    <div>
                      <strong>已登录</strong>
                      <span>
                        {session.user?.display_name || session.user?.username}，可以使用全部已授权功能。
                      </span>
                    </div>
                    <a href="/accounts/dashboard/">进入工作台</a>
                  </div>
                ) : (
                  <form onSubmit={handleLogin}>
                    <div className="campus-login__header">
                      <strong>快速登录</strong>
                      <span>
                        平台使用提示：测试账号为 <strong>test</strong>，密码为 <strong>test</strong>，可体验基础检索与分析功能。需要更高权限时，请提交账号申请，管理员会在后台审核。
                      </span>
                    </div>
                    <div className="campus-login__fields">
                      <label>
                        <span>账号</span>
                        <input
                          autoComplete="username"
                          onChange={(event) => setUsername(event.target.value)}
                          placeholder="请输入用户名"
                          ref={usernameInputRef}
                          required
                          value={username}
                        />
                      </label>
                      <label>
                        <span>密码</span>
                        <input
                          autoComplete="current-password"
                          onChange={(event) => setPassword(event.target.value)}
                          placeholder="请输入密码"
                          required
                          type="password"
                          value={password}
                        />
                      </label>
                      <button className="campus-login__submit" disabled={isSubmitting} type="submit">
                        <LogIn size={18} strokeWidth={2.2} />
                        {isSubmitting ? "登录中" : "登录平台"}
                      </button>
                    </div>
                    <div className="campus-login__foot">
                      <span>已审核账号可直接进入工作台</span>
                      <a href="/accounts/apply/">
                        <UserPlus size={15} strokeWidth={2.2} />
                        没有账号？申请账号
                      </a>
                    </div>
                    {loginMessage && <p className="campus-login__message">{loginMessage}</p>}
                  </form>
                )}
              </div>
            </section>
          </div>
        </section>

        <section className="campus-panel-grid" aria-label="平台概览">
          <article className="campus-card campus-card--resources">
            <header className="campus-card__header">
              <div>
                <span>Corpus Library</span>
                <h2>语料资源</h2>
              </div>
              <a
                className="campus-card__action"
                href="/corpora/"
                onClick={(event) => void handleProtectedNavigation(event, "语料资源", "/corpora/")}
              >
                查看全部
                <ArrowUpRight aria-hidden="true" size={15} />
              </a>
            </header>
            <dl className="campus-resource-grid">
              {resources.map((item) => (
                <div className="campus-resource-item" data-tone={item.tone} key={item.label}>
                  <dt><span>{item.icon}</span>{item.label}</dt>
                  <dd>{item.value}</dd>
                  <small>{item.meta}</small>
                </div>
              ))}
            </dl>
          </article>

          <article className="campus-card campus-card--tools">
            <header className="campus-card__header">
              <div>
                <span>Research Toolkit</span>
                <h2>平台功能</h2>
              </div>
              <em>6 项研究工具</em>
            </header>
            <div className="campus-tool-grid">
              {tools.map(({ Icon, title, desc, href, tone }) => (
                <a
                  data-tone={tone}
                  href={href}
                  key={title}
                  onClick={(event) => void handleProtectedNavigation(event, title, href)}
                >
                  <span className="campus-tool-icon">
                    <Icon size={22} strokeWidth={2} />
                  </span>
                  <span className="campus-tool-copy">
                    <b>{title}</b>
                    <small>{desc}</small>
                  </span>
                  <ArrowUpRight className="campus-tool-arrow" aria-hidden="true" size={16} />
                </a>
              ))}
            </div>
          </article>

          <article className="campus-card campus-card--news">
            <header className="campus-card__header">
              <div>
                <span>Latest Updates</span>
                <h2>平台动态</h2>
              </div>
              <a className="campus-card__action" href="#platform-guide">
                更多动态
                <ArrowUpRight aria-hidden="true" size={15} />
              </a>
            </header>
            <ul>
              {news.map((item) => (
                <li key={item.title}>
                  <span className="campus-news-marker" aria-hidden="true" />
                  <div>
                    <span className="campus-news-tag">{item.tag}</span>
                    <a href="/">{item.title}</a>
                  </div>
                  <time>{item.date}</time>
                </li>
              ))}
            </ul>
          </article>
        </section>

        <footer className="campus-footer" id="platform-guide">
          <div className="campus-footer__brand">
            <img
              className="campus-footer__wordmark"
              src="/static/img/translation-center-wordmark-black.png"
              alt="武汉理工大学与外国语学院联合标识"
            />
            <span className="campus-footer__divider" aria-hidden="true" />
            <div className="campus-footer__center">
              <strong>翻译跨学科研究中心</strong>
              <span>Translation Interdisciplinary Research Center</span>
            </div>
          </div>
          <div className="campus-footer__copy">
            <p>武汉理工大学外国语学院翻译跨学科研究中心</p>
            <p>技术支持：陈俊宏</p>
            <p className="campus-footer__contact">
              <GitHubMark />
              <span>GitHub：</span>
              <a href="https://github.com/WHUTcjh-2024" rel="noreferrer" target="_blank">
                https://github.com/WHUTcjh-2024
              </a>
            </p>
            <p className="campus-footer__contact">
              <Mail size={16} strokeWidth={2} />
              <span>邮箱：</span>
              <a href="mailto:570372819@qq.com">570372819@qq.com</a>
            </p>
            <p>© 2026 Translation Interdisciplinary Research Center, WHUT. All rights reserved.</p>
          </div>
        </footer>
      </main>
    </div>
  );
}

function GitHubMark() {
  return (
    <svg
      className="campus-footer__github-mark"
      aria-hidden="true"
      viewBox="0 0 24 24"
      focusable="false"
    >
      <path d="M12 2C6.48 2 2 6.58 2 12.22c0 4.52 2.86 8.35 6.84 9.7.5.09.68-.22.68-.49 0-.24-.01-1.04-.01-1.89-2.51.47-3.16-.63-3.36-1.2-.11-.29-.6-1.2-1.03-1.45-.35-.19-.85-.66-.01-.67.79-.01 1.35.74 1.54 1.05.9 1.55 2.34 1.11 2.91.85.09-.67.35-1.11.64-1.37-2.22-.26-4.55-1.14-4.55-5.04 0-1.11.39-2.03 1.03-2.75-.1-.26-.45-1.31.1-2.71 0 0 .84-.27 2.75 1.05A9.27 9.27 0 0 1 12 6.96c.85 0 1.71.12 2.51.35 1.91-1.32 2.75-1.05 2.75-1.05.55 1.4.2 2.45.1 2.71.64.72 1.03 1.63 1.03 2.75 0 3.91-2.34 4.78-4.57 5.04.36.32.68.93.68 1.89 0 1.37-.01 2.47-.01 2.8 0 .27.18.59.69.49A10.08 10.08 0 0 0 22 12.22C22 6.58 17.52 2 12 2Z" />
    </svg>
  );
}

export default App;
