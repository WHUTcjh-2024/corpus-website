import type { PublicCorpus, PublicCorpusOverview, SessionPayload } from "../api/types";

export const anonymousSession: SessionPayload = {
  is_authenticated: false,
  access_scope: "none",
  user: null,
  profile: null,
};

export const authenticatedSession: SessionPayload = {
  is_authenticated: true,
  access_scope: "standard",
  user: {
    id: 7,
    username: "researcher",
    email: "researcher@example.test",
    is_staff: false,
    is_superuser: false,
    display_name: "研究员",
  },
  profile: {
    full_name: "研究员",
    organization: "测试机构",
    email: "researcher@example.test",
    role: "middle",
    role_label: "中级用户",
    status: "approved",
    status_label: "已审核",
  },
};

export const rawChineseCorpus: PublicCorpus = {
  id: "raw-zh",
  name: "老师语料·单语中文·中国社会各阶级的分析",
  corpus_type: "raw_zh",
  corpus_type_label: "中文原文",
  language: "zh",
  language_label: "中文",
  access_level_label: "公开",
  status_label: "可用",
  description: "测试语料",
  documentation: {
    file_count: 1,
    document_count: 2,
    paragraph_count: 12,
    sentence_count: 3456,
    token_count: 12345,
    type_count: 789,
  },
};

export const pairedCorpus: PublicCorpus = {
  ...rawChineseCorpus,
  id: "paired",
  name: "老师语料·双语段对齐·项目样本",
  corpus_type: "paired_raw_zh_en",
  corpus_type_label: "中英原文配对",
  language: "zh_en",
  language_label: "中英双语",
  documentation: null,
};

export const publicOverview: PublicCorpusOverview = {
  metrics: {
    corpus_count: 2,
    bilingual_corpus_count: 1,
    document_count: 3,
    sentence_count: 3456,
    token_count: 12345,
  },
  corpora: [rawChineseCorpus, pairedCorpus],
};

export function jsonResponse(payload: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}
