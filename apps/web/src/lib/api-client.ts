/**
 * Low-level HTTP client for the Quantelune backend.
 *
 * - Runtime sidecar URL + session token in Tauri
 * - NEXT_PUBLIC_API_URL fallback in regular web mode
 * - HttpOnly cookie auth: every request sends credentials
 * - 401 → single-flight token refresh, one retry, then redirect to /login
 * - Errors are normalized to ApiError { status, detail, errorCode }
 */

import { getApiRuntime, resolveDesktopApiPath } from "./tauri-adapter";
import { getDesktopFault, reportDesktopFault } from "./desktop-startup";
import {
  assertActorEpoch,
  getActorEpoch,
  serializeSessionRequest,
} from "./auth-session";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly errorCode: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Paths that must never trigger the refresh/retry/redirect dance. */
const AUTH_EXEMPT_PATHS = [
  "/api/v1/auth/login",
  "/api/v1/auth/register",
  "/api/v1/auth/refresh",
  "/api/v1/auth/logout",
];

const REDIRECT_SKIPLIST = ["/login", "/register"];

function redirectLogin(): void {
  if (typeof window === "undefined") return;
  if (REDIRECT_SKIPLIST.includes(window.location.pathname)) return;
  window.location.href = "/login";
}

/** Extract a human-readable detail from a FastAPI error body. */
function extractDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.length > 0) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object"
          ? String((item as { msg?: unknown }).msg ?? "")
          : "",
      )
      .filter(Boolean);
    if (messages.length > 0) return messages.join("; ");
  }
  return fallback;
}

function extractErrorCode(body: unknown): string {
  if (body && typeof body === "object") {
    const code = (body as { error_code?: unknown }).error_code;
    if (typeof code === "string" && code.length > 0) return code;
  }
  return "UNKNOWN";
}

async function throwResponseError(
  response: Response,
  epoch: number,
): Promise<never> {
  const body = await response.json().catch(() => undefined);
  assertActorEpoch(epoch);
  throw new ApiError(
    response.status,
    extractDetail(
      body,
      response.statusText || `Request failed (${response.status})`,
    ),
    extractErrorCode(body),
  );
}

/* ------------------------------------------------------------------ */
/* Single-flight session refresh                                       */
/* ------------------------------------------------------------------ */

let refreshPromise: { epoch: number; promise: Promise<boolean> } | null = null;

function refreshSession(epoch: number): Promise<boolean> {
  assertActorEpoch(epoch);
  if (!refreshPromise || refreshPromise.epoch !== epoch) {
    const pending = {
      epoch,
      promise: serializeSessionRequest(async () => {
        assertActorEpoch(epoch);
        const { apiUrl, sessionToken } = await getApiRuntime();
        assertActorEpoch(epoch);
        return fetch(`${apiUrl}/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: sessionToken
            ? { "X-Session-Token": sessionToken }
            : undefined,
        });
      })
        .then((response) => {
          assertActorEpoch(epoch);
          return response.ok;
        })
        .catch((error: unknown) => {
          assertActorEpoch(epoch);
          if (error instanceof DOMException && error.name === "AbortError")
            throw error;
          return false;
        })
        .finally(() => {
          // Allow future refresh attempts once this one settles.
          setTimeout(() => {
            if (refreshPromise === pending) refreshPromise = null;
          }, 0);
        }),
    };
    refreshPromise = pending;
  }
  return refreshPromise.promise;
}

/* ------------------------------------------------------------------ */
/* Core fetch wrapper                                                  */
/* ------------------------------------------------------------------ */

export type ApiRequestInit = RequestInit & {
  /** Skip the redirect-to-/login behavior on auth failure (used by auth pages). */
  skipAuthRedirect?: boolean;
};

function buildHeaders(init?: RequestInit): HeadersInit {
  const isFormData = init?.body instanceof FormData;
  return {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...init?.headers,
  };
}

function withSessionToken(
  headers: HeadersInit,
  sessionToken: string,
): HeadersInit {
  const merged = new Headers(headers);
  if (sessionToken) merged.set("X-Session-Token", sessionToken);
  return Object.fromEntries(merged.entries());
}

function isBodyEmpty(response: Response): boolean {
  return (
    response.status === 204 ||
    response.status === 205 ||
    response.headers.get("content-length") === "0"
  );
}

async function parseSuccess<T>(response: Response, epoch: number): Promise<T> {
  assertActorEpoch(epoch);
  if (isBodyEmpty(response)) return undefined as T;
  const result = await response.json();
  assertActorEpoch(epoch);
  return result as T;
}

async function checkDesktopConnection(apiUrl: string, signal?: AbortSignal | null) {
  if (signal?.aborted || getDesktopFault()) return;
  try {
    const health = await fetch(new URL("/health", apiUrl), {
      cache: "no-store", signal: AbortSignal.timeout(3000),
    });
    if (health.ok) return;
  } catch { /* A failed health check is handled by the desktop boundary. */ }
  if (!signal?.aborted) reportDesktopFault("LOCAL_SERVICE_UNAVAILABLE");
}

export async function apiFetch<T>(
  path: string,
  options?: ApiRequestInit,
): Promise<T> {
  const epoch = getActorEpoch();
  const { skipAuthRedirect, ...init } = options ?? {};
  const runtime = await getApiRuntime();
  assertActorEpoch(epoch);
  const resolvedPath = resolveDesktopApiPath(path);
  const isAuthExempt = AUTH_EXEMPT_PATHS.some((exempt) =>
    path.startsWith(exempt),
  );

  const transport = () => {
    assertActorEpoch(epoch);
    if (runtime.desktop && getDesktopFault()) throw getDesktopFault();
    return fetch(`${runtime.apiUrl}${resolvedPath.replace(/^\/api\/v1/, "")}`, {
      ...init,
      credentials: "include",
      headers: withSessionToken(buildHeaders(init), runtime.sessionToken),
    });
  };
  const execute = () =>
    isAuthExempt ? serializeSessionRequest(transport) : transport();

  let response: Response;
  try {
    response = await execute();
  } catch (error) {
    assertActorEpoch(epoch);
    if (init.signal?.aborted || (error instanceof DOMException && error.name === "AbortError"))
      throw error;
    if (runtime.desktop) await checkDesktopConnection(runtime.apiUrl, init.signal);
    throw new ApiError(
      0,
      "Cannot reach the Quantelune API. Is the backend running?",
      "NETWORK_ERROR",
    );
  }

  assertActorEpoch(epoch);

  if (response.status === 401 && !isAuthExempt) {
    if (runtime.desktop) {
      reportDesktopFault("LOCAL_SESSION_UNAVAILABLE");
      await throwResponseError(response, epoch);
    }
    const refreshed = await refreshSession(epoch);
    assertActorEpoch(epoch);
    if (refreshed) {
      const retry = await execute().catch(() => null);
      assertActorEpoch(epoch);
      if (retry?.ok) return parseSuccess<T>(retry, epoch);
      // Surface non-auth failures from the retry (e.g. 404, 422, 500).
      if (retry && retry.status !== 401) await throwResponseError(retry, epoch);
    }
    if (!skipAuthRedirect) redirectLogin();
    throw new ApiError(
      401,
      "Session expired. Please sign in again.",
      "AUTH_TOKEN_EXPIRED",
    );
  }

  if (!response.ok) await throwResponseError(response, epoch);
  return parseSuccess<T>(response, epoch);
}

/* ------------------------------------------------------------------ */
/* Multipart upload with progress (XHR so we get upload events)        */
/* ------------------------------------------------------------------ */

export interface UploadOptions {
  /** 0–100 progress callback. */
  onProgress?: (percent: number) => void;
  signal?: AbortSignal;
}

export async function apiUpload<T>(
  path: string,
  formData: FormData,
  options?: UploadOptions,
): Promise<T> {
  const epoch = getActorEpoch();
  const runtime = await getApiRuntime();
  assertActorEpoch(epoch);
  const resolvedPath = resolveDesktopApiPath(path);
  if (runtime.desktop && getDesktopFault()) throw getDesktopFault();
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(
      "POST",
      `${runtime.apiUrl}${resolvedPath.replace(/^\/api\/v1/, "")}`,
    );
    xhr.withCredentials = true;
    xhr.responseType = "text";
    if (runtime.sessionToken) {
      xhr.setRequestHeader("X-Session-Token", runtime.sessionToken);
    }

    xhr.upload.onprogress = (event) => {
      if (getActorEpoch() !== epoch) return;
      if (!options?.onProgress || !event.lengthComputable) return;
      options.onProgress(Math.round((event.loaded / event.total) * 100));
    };

    xhr.onload = () => {
      try {
        assertActorEpoch(epoch);
      } catch (error) {
        reject(error);
        return;
      }
      const body: unknown = xhr.responseText
        ? safeJsonParse(xhr.responseText)
        : undefined;
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve((body ?? undefined) as T);
        return;
      }
      if (runtime.desktop && xhr.status === 401)
        reportDesktopFault("LOCAL_SESSION_UNAVAILABLE");
      reject(
        new ApiError(
          xhr.status,
          extractDetail(
            body,
            xhr.statusText || `Upload failed (${xhr.status})`,
          ),
          extractErrorCode(body),
        ),
      );
    };

    xhr.onerror = () => {
      try {
        assertActorEpoch(epoch);
      } catch (error) {
        reject(error);
        return;
      }
      // A failed upload is a request failure, not evidence that desktop startup
      // failed. Keep the workspace available for the error and an explicit retry.
      reject(
        new ApiError(
          0,
          "Upload failed. Check your connection and try again.",
          "NETWORK_ERROR",
        ),
      );
    };

    xhr.onabort = () =>
      reject(new ApiError(0, "Upload cancelled.", "UPLOAD_CANCELLED"));

    if (options?.signal) {
      if (options.signal.aborted) {
        xhr.abort();
        return;
      }
      options.signal.addEventListener("abort", () => xhr.abort(), {
        once: true,
      });
    }

    xhr.send(formData);
  });
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** Bounded authenticated local material viewer; bytes never become AI context. */
export async function apiBlob(
  path: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const epoch = getActorEpoch();
  const runtime = await getApiRuntime();
  assertActorEpoch(epoch);
  if (runtime.desktop && getDesktopFault()) throw getDesktopFault();
  let response: Response;
  try {
    response = await fetch(
      `${runtime.apiUrl}${resolveDesktopApiPath(path).replace(/^\/api\/v1/, "")}`,
      {
        credentials: "include",
        signal,
        headers: withSessionToken({}, runtime.sessionToken),
      },
    );
  } catch (error) {
    assertActorEpoch(epoch);
    if (
      runtime.desktop &&
      !(error instanceof DOMException && error.name === "AbortError")
    )
      await checkDesktopConnection(runtime.apiUrl, signal);
    throw error;
  }
  assertActorEpoch(epoch);
  if (runtime.desktop && response.status === 401)
    reportDesktopFault("LOCAL_SESSION_UNAVAILABLE");
  if (!response.ok) await throwResponseError(response, epoch);
  const reader = response.body?.getReader();
  if (!reader)
    throw new ApiError(0, "Material unavailable.", "MATERIAL_UNAVAILABLE");
  const buffers: ArrayBuffer[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      assertActorEpoch(epoch);
      if (done) break;
      buffers.push(new Uint8Array(value).buffer);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    assertActorEpoch(epoch);
    if (
      runtime.desktop &&
      !(error instanceof ApiError) &&
      !(error instanceof DOMException && error.name === "AbortError")
    )
      await checkDesktopConnection(runtime.apiUrl, signal);
    throw error;
  } finally {
    reader.releaseLock();
  }
  return new Blob(buffers, {
    type: response.headers.get("content-type")?.split(";")[0] ?? "",
  });
}
