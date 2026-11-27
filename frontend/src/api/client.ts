/**
 * API client supporting both local development and public ngrok access.
 *
 * Local browser URL (localhost / 127.0.0.1): call FastAPI directly.
 * Public/LAN browser URL: use the same-origin /api path through Vite's proxy.
 */
import { getSessionId, setSessionId } from "../store/sessionStore";

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").trim();
const hostname = typeof window !== "undefined"
  ? window.location.hostname.toLowerCase()
  : "localhost";

const isLocalFrontend = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
const isLoopbackApiUrl = /^https?:\/\/(localhost|127\.0\.0\.1|\[?::1\]?)(:\d+)?(\/api)?\/?$/i.test(
  configuredBaseUrl,
);

// Do not let a configured localhost API URL leak into a public ngrok session.
// For ngrok and LAN access, /api is proxied by Vite to the local FastAPI server.
const BASE_URL = isLocalFrontend
  ? (configuredBaseUrl || "http://localhost:8000/api").replace(/\/$/, "")
  : (configuredBaseUrl && !isLoopbackApiUrl
      ? configuredBaseUrl.replace(/\/$/, "")
      : "/api");

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const sessionId = getSessionId();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(sessionId ? { "X-Session-Id": sessionId } : {}),
    ...((options.headers as Record<string, string>) || {}),
  };

  const res = await fetch(`${BASE_URL}${normalizedPath}`, {
    ...options,
    headers,
    credentials: "include",
  });

  const returnedSessionId = res.headers.get("X-Session-Id");
  if (returnedSessionId) setSessionId(returnedSessionId);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.message || body.detail || `Request failed: ${res.status}`,
    );
  }

  return res.json() as Promise<T>;
}

export { BASE_URL };
