import { getSession, signOut } from "next-auth/react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";

let cachedToken: string | null = null;

export function setAccessToken(token: string | null) {
  cachedToken = token;
}

export function getAccessToken(): string | null {
  return cachedToken;
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit & { skipAuth?: boolean }
): Promise<T> {
  const { skipAuth, ...fetchOptions } = options || {};

  const headers = new Headers(fetchOptions.headers);
  if (!skipAuth) {
    const token = await resolveAccessToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }
  if (!headers.has("Content-Type") && !(fetchOptions.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let response = await fetch(`${API_URL}${path}`, {
    ...fetchOptions,
    headers,
    credentials: "include",
  });

  // If 401, try refreshing the backend token via refresh-token cookie
  if (response.status === 401 && !skipAuth) {
    cachedToken = null;
    const refreshed = await refreshBackendToken();
    if (refreshed) {
      headers.set("Authorization", `Bearer ${refreshed}`);
      response = await fetch(`${API_URL}${path}`, {
        ...fetchOptions,
        headers,
        credentials: "include",
      });
    }

    // Still 401 after refresh → force re-login
    if (response.status === 401) {
      cachedToken = null;
      await signOut({ callbackUrl: "/login" });
      throw new Error("Session expired. Please sign in again.");
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || body.msg || `API error: ${response.status}`);
  }

  return response.json();
}

/**
 * Ask the backend to mint a new access token using the httpOnly
 * refresh-token cookie.  Returns the fresh token or null on failure.
 */
async function refreshBackendToken(): Promise<string | null> {
  try {
    const res = await fetch(`${API_URL}/api/auth/refresh`, {
      method: "POST",
      credentials: "include", // sends the refresh_token cookie
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data.access_token) {
      cachedToken = data.access_token;
      return cachedToken;
    }
  } catch {
    console.error("Token refresh failed");
  }
  return null;
}

/**
 * Resolve the backend JWT from the NextAuth session.
 * The backend token was obtained via token-exchange during sign-in
 * and is stored in session.accessToken.
 */
async function resolveAccessToken(forceRefresh = false): Promise<string | null> {
  if (cachedToken && !forceRefresh) return cachedToken;

  // First try the NextAuth session (has the token from initial sign-in)
  try {
    const session = await getSession();
    if (session?.accessToken) {
      cachedToken = session.accessToken;
      return cachedToken;
    }
  } catch {
    console.error("Failed to get session for access token");
  }

  // Fallback: try refreshing via the httpOnly refresh-token cookie
  return refreshBackendToken();
}
