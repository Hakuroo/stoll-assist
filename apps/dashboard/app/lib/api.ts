import type {
  ApiResult,
  AuthUser,
  ConversationDetail,
  ConversationState,
  ConversationSummary,
  KnowledgeItem,
  OperatorRole,
  OutboxReviewItem
} from "./types";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";
const DASHBOARD_ORIGIN = process.env.DASHBOARD_ORIGIN ?? "http://localhost:3000";
const CSRF_COOKIE = "stoll_assist_csrf";

export async function apiGet<T>(path: string): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: await authHeaders(),
      cache: "no-store"
    });
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "No se pudo conectar con la API"
    };
  }
  if (response.status === 401) {
    redirect("/login");
  }
  if (!response.ok) {
    return { ok: false, error: await errorText(response) };
  }
  return { ok: true, data: (await response.json()) as T };
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      ...(await authHeaders()),
      "content-type": "application/json",
      origin: DASHBOARD_ORIGIN,
      "x-csrf-token": await csrfToken()
    },
    body: JSON.stringify(body),
    cache: "no-store"
  });
  if (response.status === 401) {
    redirect("/login");
  }
  if (!response.ok) {
    throw new Error(await errorText(response));
  }
  return (await response.json()) as T;
}

export async function apiPostAndCopyCookies<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      ...(await authHeaders()),
      "content-type": "application/json",
      origin: DASHBOARD_ORIGIN,
      "x-csrf-token": await csrfToken()
    },
    body: JSON.stringify(body),
    cache: "no-store"
  });
  if (response.status === 401) {
    redirect("/login");
  }
  if (!response.ok) {
    throw new Error(await errorText(response));
  }
  await copySetCookies(response);
  return (await response.json()) as T;
}

export async function loginRequest(body: unknown): Promise<ApiResult<AuthUser>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        origin: DASHBOARD_ORIGIN
      },
      body: JSON.stringify(body),
      cache: "no-store"
    });
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "No se pudo conectar con la API"
    };
  }
  if (!response.ok) {
    return { ok: false, error: await errorText(response) };
  }
  await copySetCookies(response);
  return { ok: true, data: (await response.json()) as AuthUser };
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/auth/me`, {
      headers: await authHeaders(),
      cache: "no-store"
    });
  } catch {
    return null;
  }
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as AuthUser;
}

export async function copySetCookies(response: Response): Promise<void> {
  const store = await cookies();
  for (const cookie of getSetCookieHeaders(response)) {
    const parsed = parseSetCookie(cookie);
    if (!parsed) {
      continue;
    }
    store.set(parsed.name, parsed.value, parsed.options);
  }
}

export function canOperate(role: OperatorRole): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "OPERATOR";
}

export function canAdmin(role: OperatorRole): boolean {
  return role === "OWNER" || role === "ADMIN";
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
  AuthUser,
  ConversationDetail,
  ConversationState,
  ConversationSummary,
  KnowledgeItem,
  OperatorRole,
  OutboxReviewItem
};

async function authHeaders(): Promise<Record<string, string>> {
  const header = await cookieHeader();
  return header ? { cookie: header } : {};
}

async function cookieHeader(): Promise<string> {
  const store = await cookies();
  return store
    .getAll()
    .map((item) => `${item.name}=${encodeURIComponent(item.value)}`)
    .join("; ");
}

async function csrfToken(): Promise<string> {
  const store = await cookies();
  return store.get(CSRF_COOKIE)?.value ?? "";
}

function getSetCookieHeaders(response: Response): string[] {
  const headersWithList = response.headers as Headers & {
    getSetCookie?: () => string[];
  };
  if (headersWithList.getSetCookie) {
    return headersWithList.getSetCookie();
  }
  const combined = response.headers.get("set-cookie");
  return combined ? combined.split(/,(?=[^;,]+=)/) : [];
}

function parseSetCookie(header: string):
  | {
      name: string;
      value: string;
      options: {
        expires?: Date;
        httpOnly?: boolean;
        maxAge?: number;
        path?: string;
        sameSite?: "lax" | "strict" | "none";
        secure?: boolean;
      };
    }
  | null {
  const parts = header.split(";").map((part) => part.trim());
  const [nameValue, ...attributes] = parts;
  const separator = nameValue.indexOf("=");
  if (separator <= 0) {
    return null;
  }
  const name = nameValue.slice(0, separator);
  const value = decodeURIComponent(nameValue.slice(separator + 1));
  const options: {
    expires?: Date;
    httpOnly?: boolean;
    maxAge?: number;
    path?: string;
    sameSite?: "lax" | "strict" | "none";
    secure?: boolean;
  } = {};

  for (const attribute of attributes) {
    const [rawKey, ...rawValue] = attribute.split("=");
    const key = rawKey.toLowerCase();
    const attrValue = rawValue.join("=");
    if (key === "httponly") {
      options.httpOnly = true;
    } else if (key === "secure") {
      options.secure = true;
    } else if (key === "path") {
      options.path = attrValue || "/";
    } else if (key === "max-age") {
      options.maxAge = Number(attrValue);
    } else if (key === "expires") {
      options.expires = new Date(attrValue);
    } else if (key === "samesite") {
      const sameSite = attrValue.toLowerCase();
      if (sameSite === "lax" || sameSite === "strict" || sameSite === "none") {
        options.sameSite = sameSite;
      }
    }
  }
  return { name, value, options };
}

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
