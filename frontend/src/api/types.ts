export type AccessScope = "none" | "demo_only" | "standard" | "admin";

export type SessionPayload = {
  is_authenticated: boolean;
  access_scope: AccessScope;
  user: null | {
    id: number;
    username: string;
    email: string;
    is_staff: boolean;
    is_superuser: boolean;
    display_name: string;
  };
  profile: null | {
    full_name: string;
    organization: string;
    email: string;
    role: string;
    role_label: string;
    status: string;
    status_label: string;
  };
};

export type PublicCorpus = {
  id: string;
  name: string;
  corpus_type: string;
  corpus_type_label: string;
  language: string;
  language_label: string;
  access_level_label: string;
  status_label: string;
  description: string;
  documentation: null | {
    file_count: number;
    document_count: number;
    paragraph_count: number;
    sentence_count: number;
    token_count: number;
    type_count: number;
  };
};

export type PublicCorpusOverview = {
  metrics: {
    corpus_count: number;
    bilingual_corpus_count: number;
    document_count: number;
    sentence_count: number;
    token_count: number;
  };
  corpora: PublicCorpus[];
};
