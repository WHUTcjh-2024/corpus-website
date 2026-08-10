import type { PublicCorpus } from "../api/types";

export function publicCorpusTitle(corpus: PublicCorpus) {
  const parts = corpus.name.split("·").filter(Boolean);
  return parts[0] === "老师语料" ? parts.slice(2).join("·") : corpus.name;
}

export function publicCorpusMode(corpus: PublicCorpus) {
  const parts = corpus.name.split("·").filter(Boolean);
  return parts[0] === "老师语料"
    ? (parts[1] ?? corpus.corpus_type_label)
    : corpus.corpus_type_label;
}

export function formatCount(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function safeInternalDestination(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return null;
  }
  return value;
}
