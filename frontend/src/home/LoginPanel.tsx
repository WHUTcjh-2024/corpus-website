import { CheckCircle2, LockKeyhole, LogIn, UserPlus, X } from "lucide-react";
import type { FormEventHandler, RefObject } from "react";

import type { SessionPayload } from "../api/types";

export type LoginPanelProps = {
  isAttentionVisible: boolean;
  isSubmitting: boolean;
  loginMessage: string;
  loginPrompt: string;
  password: string;
  session: SessionPayload | null;
  username: string;
  containerRef: RefObject<HTMLDivElement | null>;
  usernameInputRef: RefObject<HTMLInputElement | null>;
  onDismissPrompt: () => void;
  onPasswordChange: (value: string) => void;
  onSubmit: FormEventHandler<HTMLFormElement>;
  onUsernameChange: (value: string) => void;
};

export function LoginPanel({
  isAttentionVisible,
  isSubmitting,
  loginMessage,
  loginPrompt,
  password,
  session,
  username,
  containerRef,
  usernameInputRef,
  onDismissPrompt,
  onPasswordChange,
  onSubmit,
  onUsernameChange,
}: LoginPanelProps) {
  return (
    <div
      className={`campus-login ${isAttentionVisible ? "campus-login--attention" : ""}`}
      id="home-login"
      ref={containerRef}
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
            onClick={onDismissPrompt}
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
        <form onSubmit={onSubmit}>
          <div className="campus-login__header">
            <strong>快速登录</strong>
            <span>
              平台使用提示：测试账号为 <strong>test</strong>，密码为 <strong>test</strong>
              ，可体验基础检索与分析功能。需要更高权限时，请提交账号申请，管理员会在后台审核。
            </span>
          </div>
          <div className="campus-login__fields">
            <label>
              <span>账号</span>
              <input
                autoComplete="username"
                onChange={(event) => onUsernameChange(event.target.value)}
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
                onChange={(event) => onPasswordChange(event.target.value)}
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
  );
}
