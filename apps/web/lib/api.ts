export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly correlationId?: string,
  ) {
    super(message);
  }
}

export const API_REQUEST_TIMEOUT_MS = 10_000;

function cookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const entry = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith(`${name}=`));
  return entry ? decodeURIComponent(entry.slice(name.length + 1)) : undefined;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  const method = (init.method ?? "GET").toUpperCase();
  if (
    !["GET", "HEAD", "OPTIONS"].includes(method) &&
    !headers.has("X-CSRF-Token")
  ) {
    const csrf = cookie("tv_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort(init.signal?.reason);
  if (init.signal?.aborted) controller.abort(init.signal.reason);
  else init.signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), API_REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`/api/v1${path}`, {
      ...init,
      headers,
      signal: controller.signal,
      credentials: "include",
      cache: "no-store",
    });
    if (response.status === 204) return undefined as T;
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string; correlation_id?: string };
    } | null;
    if (!response.ok) {
      const unavailable = response.status >= 500;
      throw new ApiClientError(
        response.status,
        body?.error?.code ??
          (unavailable ? "service_unavailable" : "request_failed"),
        body?.error?.message ??
          (unavailable
            ? "Сервис временно недоступен. Попробуйте ещё раз."
            : "Не удалось выполнить запрос."),
        body?.error?.correlation_id,
      );
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiClientError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      throw new ApiClientError(
        0,
        "request_timeout",
        "Сервис не отвечает. Проверьте запуск Docker и повторите попытку.",
      );
    }
    throw new ApiClientError(
      0,
      "network_unavailable",
      "Не удалось связаться с сервером. Проверьте запуск проекта.",
    );
  } finally {
    clearTimeout(timeout);
    init.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export function idempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}
