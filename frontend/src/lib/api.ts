import { supabase } from "./supabase";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/**
 * Call the FastAPI backend with the current Supabase access token attached.
 * The backend re-verifies this token and derives tenant scope from it.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    throw new Error("not authenticated");
  }

  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
      ...(init.headers ?? {}),
    },
  });

  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${body}`);
  }
  return (await resp.json()) as T;
}

/**
 * Call a PUBLIC backend endpoint — no auth token. Used by the marketing site
 * (e.g. the Book a Demo form) which has no session.
 */
export async function publicPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  if (!resp.ok) {
    const detail =
      parsed && typeof parsed === "object" && "detail" in parsed
        ? (parsed as { detail: unknown }).detail
        : parsed;
    let msg = `Request failed (${resp.status})`;
    if (typeof detail === "string") {
      msg = detail;
    } else if (Array.isArray(detail) && detail[0] && typeof detail[0] === "object") {
      // FastAPI / pydantic validation error shape
      msg = String((detail[0] as { msg?: string }).msg ?? msg);
    }
    throw new Error(msg);
  }
  return parsed as T;
}
