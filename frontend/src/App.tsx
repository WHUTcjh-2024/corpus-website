import {
  ArrowUpRight,
  CheckCircle2,
  LockKeyhole,
  LogIn,
  Mail,
  UserPlus,
  X,
} from "lucide-react";
import { FormEvent, MouseEvent, useEffect, useRef, useState } from "react";

import { fetchPublicCorpusOverview, fetchSession } from "./api/client";
import type { PublicCorpus, PublicCorpusOverview, SessionPayload } from "./api/types";

const navItems = [
  { label: "首页", href: "/", requiresAuth: false },
  { label: "语料资源", href: "/corpora/", requiresAuth: true },
  { label: "检索中心", href: "/corpora/?tool=kwic", requiresAuth: true },
  { label: "用户语料", href: "/corpora/mine/", requiresAuth: true },
  { label: "对齐工具", href: "/corpora/?tool=parallel", requiresAuth: true },
  { label: "统计分析", href: "/corpora/?tool=statistics", requiresAuth: true },
  { label: "帮助中心", href: "#platform-guide", requiresAuth: false },
];

const EMPTY_PUBLIC_CORPUS_OVERVIEW: PublicCorpusOverview = {
  metrics: {
    corpus_count: 0,
    bilingual_corpus_count: 0,
    document_count: 0,
    sentence_count: 0,
    token_count: 0,
  },
  corpora: [],
};

const CORPUS_TYPE_ORDER: Record<string, number> = {
  paired_raw_zh_en: 0,
  paired_tagged_zh_en: 1,
  raw_zh: 2,
  raw_en: 3,
};

function publicCorpusTitle(corpus: PublicCorpus) {
  const parts = corpus.name.split("·").filter(Boolean);
  return parts[0] === "老师语料" ? parts.slice(2).join("·") : corpus.name;
}

function publicCorpusMode(corpus: PublicCorpus) {
  const parts = corpus.name.split("·").filter(Boolean);
  return parts[0] === "老师语料" ? parts[1] ?? corpus.corpus_type_label : corpus.corpus_type_label;
}

function formatCount(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function safeInternalDestination(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return null;
  }
  return value;
}

function App() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [publicCorpusOverview, setPublicCorpusOverview] = useState(EMPTY_PUBLIC_CORPUS_OVERVIEW);
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
          <div className="campus-corpus-showcase">
            <header className="campus-corpus-showcase__header">
              <div>
                <span className="campus-corpus-showcase__eyebrow">PROJECT CORPUS / RESEARCH COLLECTION</span>
                <h2>老师项目语料库</h2>
                <p>马克思主义中国化经典文献汉英平行语料库建设及其综合研究</p>
              </div>
              <div className="campus-corpus-showcase__header-meta">
                <strong>22BYY022</strong>
                <span>公开目录</span>
              </div>
            </header>

            <div className="campus-corpus-showcase__overview">
              <div className="campus-corpus-showcase__statement">
                <span className="campus-corpus-showcase__index">01</span>
                <span className="campus-corpus-showcase__label">COLLECTION NOTE</span>
                <h3>把经典文献的汉英表达，转化为可观察、可比较、可复核的研究材料。</h3>
                <p>本区域向专家与公众展示已登记、已加工的公开样本，完整教师资源继续按访问等级保护。</p>
                <div className="campus-corpus-showcase__tags">
                  <span>经典文献</span><span>汉英平行</span><span>只读样本</span><span>已完成加工</span>
                </div>
              </div>
              <div className="campus-corpus-showcase__snapshot">
                <div className="campus-corpus-showcase__label">COLLECTION SNAPSHOT</div>
                <div className="campus-corpus-showcase__stat-grid">
                  <div><strong>{publicCorpusLoading ? "—" : formatCount(publicCorpusOverview.metrics.corpus_count)}</strong><span>公开样本</span></div>
                  <div><strong>{publicCorpusLoading ? "—" : formatCount(publicCorpusOverview.metrics.bilingual_corpus_count)}</strong><span>汉英双语</span></div>
                  <div><strong>{publicCorpusLoading ? "—" : formatCount(publicCorpusOverview.metrics.document_count)}</strong><span>登记文档</span></div>
                  <div><strong>{publicCorpusLoading ? "—" : formatCount(publicCorpusOverview.metrics.sentence_count)}</strong><span>句级单元</span></div>
                  <div className="is-wide"><strong>{publicCorpusLoading ? "—" : formatCount(publicCorpusOverview.metrics.token_count)}</strong><span>Tokens</span></div>
                </div>
                <div className="campus-corpus-showcase__snapshot-note">统计基于当前公开样本目录，随语料加工状态更新。</div>
              </div>
            </div>

            <div className="campus-corpus-showcase__body">
              <div className="campus-corpus-catalogue">
                <div className="campus-corpus-catalogue__head">
                  <span>公开语料目录</span>
                  <span>{publicCorpusLoading ? "读取中" : `${publicCorpusOverview.corpora.length} 组样本`}</span>
                </div>
                {publicCorpusError ? (
                  <div className="campus-corpus-showcase__empty">公开目录暂时无法读取，请刷新页面重试。</div>
                ) : publicCorpusLoading ? (
                  <div className="campus-corpus-showcase__empty">正在读取老师项目语料库…</div>
                ) : (
                  [...publicCorpusOverview.corpora]
                    .sort((left, right) => (CORPUS_TYPE_ORDER[left.corpus_type] ?? 99) - (CORPUS_TYPE_ORDER[right.corpus_type] ?? 99))
                    .map((corpus, index) => {
                      const documentation = corpus.documentation;
                      return (
                        <article className="campus-corpus-record" key={corpus.id}>
                          <span className="campus-corpus-record__index">{String(index + 1).padStart(2, "0")}</span>
                          <div className="campus-corpus-record__title">
                            <span>{publicCorpusMode(corpus)} · {corpus.language_label}</span>
                            <h3>{publicCorpusTitle(corpus)}</h3>
                            <small>{corpus.corpus_type_label} · 老师提供样本</small>
                          </div>
                          <div className="campus-corpus-record__metrics">
                            <span><strong>{documentation ? formatCount(documentation.document_count) : "—"}</strong><small>文档</small></span>
                            <span><strong>{documentation ? formatCount(documentation.sentence_count) : "—"}</strong><small>句</small></span>
                            <span><strong>{documentation ? formatCount(documentation.token_count) : "—"}</strong><small>tokens</small></span>
                            <span><strong>{documentation ? formatCount(documentation.type_count) : "—"}</strong><small>types</small></span>
                          </div>
                          <span className="campus-corpus-record__status"><span />可用</span>
                        </article>
                      );
                    })
                )}
              </div>

              <aside className="campus-corpus-showcase__aside">
                <span className="campus-corpus-showcase__label">PROJECT NOTES</span>
                <h3>语料库使用说明</h3>
                <p>公开页面呈现项目对象、样本构成和加工规模；登录后可进入与账号等级对应的检索和分析空间。</p>
                <dl>
                  <div><dt>数据来源</dt><dd>老师提供 · 只读登记</dd></div>
                  <div><dt>加工状态</dt><dd>已完成文本处理</dd></div>
                  <div><dt>开放边界</dt><dd>公开样本 · 分级资源</dd></div>
                </dl>
                <a href="/corpora/" onClick={(event) => void handleProtectedNavigation(event, "项目语料库", "/corpora/")}>
                  进入研究库 <ArrowUpRight aria-hidden="true" size={15} />
                </a>
              </aside>
            </div>

            <footer className="campus-corpus-showcase__footer">
              <span><span className="campus-corpus-showcase__footer-dot" />公开样本只读展示，不替代正式授权资源</span>
              <span>数据范围：当前已完成加工的老师项目样本</span>
            </footer>
          </div>
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
