import { Mail } from "lucide-react";

export function HomeFooter() {
  return (
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
        <p>© 2026 武汉理工大学外国语学院翻译跨学科研究中心</p>
      </div>
    </footer>
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
