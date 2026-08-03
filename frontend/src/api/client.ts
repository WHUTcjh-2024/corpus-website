import type { PublicCorpusOverview, SessionPayload } from "./types";

async function requestJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }

  return (await response.json()) as T;
}

export function fetchSession() {
  return requestJson<SessionPayload>("/api/session/");
}

export function fetchPublicCorpusOverview() {
  return requestJson<PublicCorpusOverview>("/api/public-corpora/");
}
