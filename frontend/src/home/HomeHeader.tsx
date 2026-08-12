import { LockKeyhole } from "lucide-react";
import type { MouseEvent } from "react";

import { NAVIGATION_ITEMS } from "./constants";

export type ProtectedNavigationHandler = (
  event: MouseEvent<HTMLAnchorElement>,
  label: string,
  href: string,
) => void;

type HomeHeaderProps = {
  isAuthenticated: boolean;
  onProtectedNavigation: ProtectedNavigationHandler;
};

export function HomeHeader({ isAuthenticated, onProtectedNavigation }: HomeHeaderProps) {
  return (
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
          </span>
        </a>
        <nav className="public-nav" aria-label="公共导航">
          {NAVIGATION_ITEMS.map((item, index) => {
            const requiresLogin = item.requiresAuth && !isAuthenticated;
            return (
              <a
                className={`${index === 0 ? "active" : ""} ${
                  requiresLogin ? "requires-auth" : ""
                }`}
                href={item.href}
                key={item.label}
                onClick={
                  item.requiresAuth
                    ? (event) => onProtectedNavigation(event, item.label, item.href)
                    : undefined
                }
                title={requiresLogin ? "登录后可访问" : undefined}
              >
                {item.label}
                {requiresLogin && (
                  <LockKeyhole aria-hidden="true" size={12} strokeWidth={2.4} />
                )}
              </a>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
