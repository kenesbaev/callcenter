import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IncomingTransferCard } from "@/components/incoming-transfer-card";
import type { LiveTransfer } from "@/lib/types";

const offer: LiveTransfer = {
  id: "10000000-0000-4000-8000-000000000001",
  call_id: "10000000-0000-4000-8000-000000000002",
  project_id: "10000000-0000-4000-8000-000000000003",
  status: "offered",
  reason: "Клиент просит специалиста",
  summary: "Подтверждён вопрос по договору.",
  language_code: "ru",
  routing_strategy: "longest_idle",
  destination_type: "browser",
  claimed_membership_id: null,
  context: {
    customer_name: "Тестовый клиент",
    flow_node_id: "pricing-confirmation",
    transcript_excerpt: "Клиент: соедините со специалистом",
  },
  attempt_count: 1,
  max_attempts: 3,
  lock_version: 2,
  requested_at: new Date().toISOString(),
  offer_expires_at: new Date(Date.now() + 30_000).toISOString(),
  claimed_at: null,
  connected_at: null,
  resolved_at: null,
  last_error_code: null,
  attempts: [],
};

afterEach(() => vi.restoreAllMocks());

describe("live operator transfer", () => {
  it("shows safe AI context and atomically claims the offer", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (_input, init) => {
        if (init?.method === "POST") {
          return new Response(
            JSON.stringify({
              ...offer,
              status: "claimed",
              claimed_membership_id: "10000000-0000-4000-8000-000000000004",
              lock_version: 3,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        return new Response(JSON.stringify([offer]), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <IncomingTransferCard />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Тестовый клиент")).toBeInTheDocument();
    expect(screen.getByText("Клиент просит специалиста")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Принять/ }));
    await waitFor(() =>
      expect(screen.getByText(/Звонок закреплён за вами/)).toBeInTheDocument(),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/transfers/requests/${offer.id}/claim`),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("answers a claimed offer on an allowlisted SIP phone without browser credentials", async () => {
    const claimed = {
      ...offer,
      status: "claimed" as const,
      claimed_membership_id: "10000000-0000-4000-8000-000000000004",
      lock_version: 3,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.includes("/operator/endpoints")) {
          return new Response(
            JSON.stringify([
              {
                id: "10000000-0000-4000-8000-000000000005",
                endpoint_type: "sip",
                display_hint: "Внутренний 17017",
                is_verified: true,
                is_enabled: true,
                lock_version: 2,
              },
            ]),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        if (init?.method === "POST") {
          return new Response(
            JSON.stringify({ ...claimed, status: "connecting" }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        return new Response(JSON.stringify([claimed]), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <IncomingTransferCard />
      </QueryClientProvider>,
    );
    fireEvent.change(await screen.findByLabelText("Способ ответа"), {
      target: { value: "sip" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ответить" }));
    await waitFor(() =>
      expect(screen.getByText(/подтверждённое устройство/)).toBeInTheDocument(),
    );
    const answerCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith(`/transfers/requests/${offer.id}/answer`),
    );
    expect(answerCall?.[1]?.body).toContain('"endpoint_type":"sip"');
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/operator/webrtc-config"),
      ),
    ).toBe(false);
  });
});
