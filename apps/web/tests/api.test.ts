import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest } from "@/lib/api";
import type { ApiClientError } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiRequest", () => {
  it("uses the structured database unavailable message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "database_unavailable",
              message: "Сервис временно недоступен. Попробуйте ещё раз.",
              correlation_id: "request-1",
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      apiRequest("/auth/register", { method: "POST" }),
    ).rejects.toMatchObject({
      status: 503,
      code: "database_unavailable",
      correlationId: "request-1",
      message: "Сервис временно недоступен. Попробуйте ещё раз.",
    } satisfies Partial<ApiClientError>);
  });

  it("turns a proxy failure into a Russian service message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 502 })),
    );

    await expect(
      apiRequest("/auth/register", { method: "POST" }),
    ).rejects.toMatchObject({
      status: 502,
      code: "service_unavailable",
      message: "Сервис временно недоступен. Попробуйте ещё раз.",
    } satisfies Partial<ApiClientError>);
  });

  it("reports a request timeout without exposing the underlying error", async () => {
    const abortError = new Error("internal timeout details");
    abortError.name = "AbortError";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    await expect(
      apiRequest("/auth/register", { method: "POST" }),
    ).rejects.toMatchObject({
      status: 0,
      code: "request_timeout",
      message:
        "Сервис не отвечает. Проверьте запуск Docker и повторите попытку.",
    } satisfies Partial<ApiClientError>);
  });
});
