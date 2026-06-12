import type {
  ApiResult,
  ConversationDetail,
  ConversationState,
  ConversationSummary,
  KnowledgeItem,
  OutboxReviewItem
} from "./types";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

export async function apiGet<T>(path: string): Promise<ApiResult<T>> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store"
    });
    if (!response.ok) {
      return { ok: false, error: await errorText(response) };
    }
    return { ok: true, data: (await response.json()) as T };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "No se pudo conectar con la API"
    };
  }
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error(await errorText(response));
  }
  return (await response.json()) as T;
}

export function dashboardConversationsPath(state?: ConversationState): string {
  const params = new URLSearchParams({ limit: "200" });
  if (state) {
    params.set("state", state);
  }
  return `/operator/dashboard/conversations?${params.toString()}`;
}

export function conversationDetailPath(id: string): string {
  return `/operator/dashboard/conversations/${id}`;
}

export function dashboardOutboxPath(): string {
  return "/operator/dashboard/outbox?limit=200";
}

export function knowledgePath(): string {
  return "/operator/knowledge";
}

export type {
  ConversationDetail,
  ConversationState,
  ConversationSummary,
  KnowledgeItem,
  OutboxReviewItem
};

function errorText(response: Response): Promise<string> {
  return response
    .json()
    .then((payload) => {
      if (typeof payload?.detail === "string") {
        return payload.detail;
      }
      return `API ${response.status}`;
    })
    .catch(() => `API ${response.status}`);
}
