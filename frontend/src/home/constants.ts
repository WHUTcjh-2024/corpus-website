import type { PublicCorpusOverview } from "../api/types";

export type NavigationItem = {
  label: string;
  href: string;
  requiresAuth: boolean;
};

export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  { label: "首页", href: "/", requiresAuth: false },
  { label: "语料资源", href: "/corpora/", requiresAuth: true },
  { label: "检索中心", href: "/corpora/?tool=kwic", requiresAuth: true },
  { label: "用户语料", href: "/corpora/mine/", requiresAuth: true },
  { label: "对齐工具", href: "/corpora/?tool=parallel", requiresAuth: true },
  { label: "统计分析", href: "/corpora/?tool=statistics", requiresAuth: true },
  { label: "帮助中心", href: "#platform-guide", requiresAuth: false },
];

export const EMPTY_PUBLIC_CORPUS_OVERVIEW: PublicCorpusOverview = {
  metrics: {
    corpus_count: 0,
    bilingual_corpus_count: 0,
    document_count: 0,
    sentence_count: 0,
    token_count: 0,
  },
  corpora: [],
};

export const CORPUS_TYPE_ORDER: Readonly<Record<string, number>> = {
  paired_raw_zh_en: 0,
  paired_tagged_zh_en: 1,
  raw_zh: 2,
  raw_en: 3,
};
