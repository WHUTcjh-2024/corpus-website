import { ArrowUpRight } from "lucide-react";

import type { PublicCorpusOverview } from "../api/types";
import { CORPUS_TYPE_ORDER } from "./constants";
import type { ProtectedNavigationHandler } from "./HomeHeader";
import { formatCount, publicCorpusMode, publicCorpusTitle } from "./utils";

type CorpusShowcaseProps = {
  hasError: boolean;
  isLoading: boolean;
  overview: PublicCorpusOverview;
  onProtectedNavigation: ProtectedNavigationHandler;
};

export function CorpusShowcase({
  hasError,
  isLoading,
  overview,
  onProtectedNavigation,
}: CorpusShowcaseProps) {
  const sortedCorpora = [...overview.corpora].sort(
    (left, right) =>
      (CORPUS_TYPE_ORDER[left.corpus_type] ?? 99) -
      (CORPUS_TYPE_ORDER[right.corpus_type] ?? 99),
  );

  return (
    <section className="campus-panel-grid" aria-label="平台概览">
      <div className="campus-corpus-showcase">
        <header className="campus-corpus-showcase__header">
          <div>
            <span className="campus-corpus-showcase__eyebrow">
              PROJECT CORPUS / RESEARCH COLLECTION
            </span>
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
              <span>经典文献</span>
              <span>汉英平行</span>
              <span>只读样本</span>
              <span>已完成加工</span>
            </div>
          </div>
          <div className="campus-corpus-showcase__snapshot">
            <div className="campus-corpus-showcase__label">COLLECTION SNAPSHOT</div>
            <div className="campus-corpus-showcase__stat-grid">
              <div>
                <strong>{isLoading ? "—" : formatCount(overview.metrics.corpus_count)}</strong>
                <span>公开样本</span>
              </div>
              <div>
                <strong>
                  {isLoading ? "—" : formatCount(overview.metrics.bilingual_corpus_count)}
                </strong>
                <span>汉英双语</span>
              </div>
              <div>
                <strong>{isLoading ? "—" : formatCount(overview.metrics.document_count)}</strong>
                <span>登记文档</span>
              </div>
              <div>
                <strong>{isLoading ? "—" : formatCount(overview.metrics.sentence_count)}</strong>
                <span>句级单元</span>
              </div>
              <div className="is-wide">
                <strong>{isLoading ? "—" : formatCount(overview.metrics.token_count)}</strong>
                <span>Tokens</span>
              </div>
            </div>
            <div className="campus-corpus-showcase__snapshot-note">
              统计基于当前公开样本目录，随语料加工状态更新。
            </div>
          </div>
        </div>

        <div className="campus-corpus-showcase__body">
          <div className="campus-corpus-catalogue">
            <div className="campus-corpus-catalogue__head">
              <span>公开语料目录</span>
              <span>{isLoading ? "读取中" : `${overview.corpora.length} 组样本`}</span>
            </div>
            {hasError ? (
              <div className="campus-corpus-showcase__empty">
                公开目录暂时无法读取，请刷新页面重试。
              </div>
            ) : isLoading ? (
              <div className="campus-corpus-showcase__empty">正在读取老师项目语料库…</div>
            ) : (
              sortedCorpora.map((corpus, index) => {
                const documentation = corpus.documentation;
                return (
                  <article className="campus-corpus-record" key={corpus.id}>
                    <span className="campus-corpus-record__index">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div className="campus-corpus-record__title">
                      <span>
                        {publicCorpusMode(corpus)} · {corpus.language_label}
                      </span>
                      <h3>{publicCorpusTitle(corpus)}</h3>
                      <small>{corpus.corpus_type_label} · 老师提供样本</small>
                    </div>
                    <div className="campus-corpus-record__metrics">
                      <span>
                        <strong>
                          {documentation ? formatCount(documentation.document_count) : "—"}
                        </strong>
                        <small>文档</small>
                      </span>
                      <span>
                        <strong>
                          {documentation ? formatCount(documentation.sentence_count) : "—"}
                        </strong>
                        <small>句</small>
                      </span>
                      <span>
                        <strong>
                          {documentation ? formatCount(documentation.token_count) : "—"}
                        </strong>
                        <small>tokens</small>
                      </span>
                      <span>
                        <strong>
                          {documentation ? formatCount(documentation.type_count) : "—"}
                        </strong>
                        <small>types</small>
                      </span>
                    </div>
                    <span className="campus-corpus-record__status">
                      <span />可用
                    </span>
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
              <div>
                <dt>数据来源</dt>
                <dd>老师提供 · 只读登记</dd>
              </div>
              <div>
                <dt>加工状态</dt>
                <dd>已完成文本处理</dd>
              </div>
              <div>
                <dt>开放边界</dt>
                <dd>公开样本 · 分级资源</dd>
              </div>
            </dl>
            <a
              href="/corpora/"
              onClick={(event) => onProtectedNavigation(event, "项目语料库", "/corpora/")}
            >
              进入研究库 <ArrowUpRight aria-hidden="true" size={15} />
            </a>
          </aside>
        </div>

        <footer className="campus-corpus-showcase__footer">
          <span>
            <span className="campus-corpus-showcase__footer-dot" />
            公开样本只读展示，不替代正式授权资源
          </span>
          <span>数据范围：当前已完成加工的老师项目样本</span>
        </footer>
      </div>
    </section>
  );
}
