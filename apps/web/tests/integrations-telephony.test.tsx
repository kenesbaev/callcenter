import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IntegrationsView } from "@/components/integrations-view";

const apiRequestMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiRequest: apiRequestMock }));

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <IntegrationsView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
  apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/auth/me")
      return Promise.resolve({
        user: {
          id: "owner-id",
          email: "owner@example.com",
          display_name: "Owner",
          role: "tenant_owner",
        },
        tenant: { id: "tenant-id", name: "K-Line", slug: "k-line" },
        csrf_token: "csrf",
      });
    if (path === "/integrations") return Promise.resolve([]);
    if (path === "/projects?limit=100")
      return Promise.resolve({
        items: [
          {
            id: "project-id",
            name: "Local SIP project",
            status: "active",
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      });
    if (path === "/telephony/status")
      return Promise.resolve({
        status: "configured",
        asterisk: "configured_live_verification_required",
        ari: "configured_live_verification_required",
        sip_trunk: "configured",
        registration: "not_configured",
        reachability: "not_configured",
        external_media: "not_verified",
        recording: "configured_not_live_verified",
        dids: ["+99******111"],
        transport: "udp",
        codecs: ["ulaw"],
        channel_usage: [
          {
            trunk_id: "trunk-id",
            name: "Local trunk",
            pool_mode: "shared",
            limit: 4,
            inbound_limit: null,
            outbound_limit: null,
            occupied: 1,
            inbound_occupied: 1,
            outbound_occupied: 0,
            available: 3,
          },
        ],
        last_checked_at: null,
        last_safe_error: null,
        live_signaling_verified: false,
        live_audio_verified: false,
      });
    if (path === "/telephony/diagnostics/local" && init?.method === "POST")
      return Promise.resolve({
        id: "diagnostic-id",
        mode: "local",
        status: "local_test_passed",
        destination_masked: null,
        signaling_verified: false,
        inbound_audio_verified: true,
        outbound_audio_verified: true,
        dtmf_verified: false,
        codec: "ulaw",
        media_statistics: { packetsReceived: 50, packetsSent: 50 },
        safe_error_code: null,
        started_at: "2026-08-05T09:00:00Z",
        completed_at: "2026-08-05T09:00:01Z",
      });
    throw new Error(`Unexpected API path: ${path}`);
  });
});

describe("Integrations telephony status", () => {
  it("shows only safe status and runs the local bidirectional media test", async () => {
    renderView();
    expect(
      await screen.findByRole("heading", { name: /SIP \/ Asterisk/ }),
    ).toBeInTheDocument();
    expect((await screen.findAllByText("configured")).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("1/4")).toBeInTheDocument();
    expect(screen.queryByText(/password|secret/i)).not.toBeInTheDocument();

    const localButton = await screen.findByRole("button", {
      name: /media test/i,
    });
    await waitFor(() => expect(localButton).toBeEnabled());
    fireEvent.click(localButton);
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "/telephony/diagnostics/local",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });
});
