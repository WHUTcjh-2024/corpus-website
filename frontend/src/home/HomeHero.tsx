import { LoginPanel, type LoginPanelProps } from "./LoginPanel";

type HomeHeroProps = {
  loginPanel: LoginPanelProps;
};

export function HomeHero({ loginPanel }: HomeHeroProps) {
  return (
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

          <LoginPanel {...loginPanel} />
        </section>
      </div>
    </section>
  );
}
