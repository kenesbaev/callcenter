import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamView } from "@/components/team-view";
import type { Role, TeamMember } from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  ApiClientError: class ApiClientError extends Error {},
  apiRequest: apiRequestMock,
  idempotencyKey: () => "team-invite-test-key",
}));

const member: TeamMember = {
  membership_id: "10000000-0000-4000-8000-000000000001",
  user_id: "10000000-0000-4000-8000-000000000002",
  display_name: "Алексей Оператор",
  email: "operator@example.com",
  phone: "+998901112233",
  job_title: "Оператор",
  role: "human_operator",
  is_active: true,
  extension: "101",
  interface_language: "ru",
  timezone: "Asia/Tashkent",
  invited_at: "2026-08-01T10:00:00Z",
  activated_at: "2026-08-01T10:05:00Z",
  last_login_at: "2026-08-01T10:10:00Z",
  last_heartbeat_at: "2026-08-01T10:12:00Z",
  blocked_at: null,
  blocked_reason: null,
  manual_status: "available",
  effective_status: "available",
  is_transfer_available: true,
  current_call_id: null,
  projects: [{ id: "20000000-0000-4000-8000-000000000001", name: "Продажи" }],
  state_version: 1,
};

function renderTeam(role: Role = "tenant_owner") {
  apiRequestMock.mockImplementation(
    (path: string | undefined, init?: RequestInit) => {
      // React Query may call a stale mock during test cleanup after its observer is
      // disposed. Production apiRequest is strongly typed and never accepts this.
      if (typeof path !== "string") return Promise.resolve(undefined);
      if (path === "/auth/me") {
        return Promise.resolve({
          user: {
            id: "owner",
            email: "owner@example.com",
            display_name: "Owner",
            role,
          },
          tenant: { id: "tenant", name: "K-Line", slug: "k-line" },
          csrf_token: "csrf",
        });
      }
      if (path === "/team/me") {
        return Promise.resolve({
          ...member,
          user_id: "owner",
          membership_id: "10000000-0000-4000-8000-000000000099",
          role,
        });
      }
      if (path.startsWith("/team?")) {
        return Promise.resolve({
          items: [member],
          total: 1,
          limit: 25,
          offset: 0,
        });
      }
      if (path === "/projects?limit=100") {
        return Promise.resolve({
          items: [{ id: member.projects[0].id, name: "Продажи" }],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      if (path === "/team/invitations?limit=100") {
        return Promise.resolve({ items: [], total: 0, limit: 100, offset: 0 });
      }
      if (path === "/team/invitations" && init?.method === "POST") {
        return Promise.resolve({
          id: "invite-id",
          email: "new@example.com",
          role: "human_operator",
          status: "pending",
          project_ids: [member.projects[0].id],
          expires_at: "2026-08-08T10:00:00Z",
          issued_at: "2026-08-01T10:00:00Z",
          accepted_at: null,
          cancelled_at: null,
          state_version: 1,
          created_at: "2026-08-01T10:00:00Z",
          acceptance_url: "http://localhost:3000/accept-invitation?token=once",
          acceptance_token: "once",
        });
      }
      if (path.includes("/history")) {
        return Promise.resolve({ items: [], total: 0, limit: 20, offset: 0 });
      }
      if (path.endsWith("/role") && init?.method === "PUT") {
        return Promise.resolve({
          ...member,
          role: "analyst",
          state_version: 2,
        });
      }
      throw new Error(`Unexpected API path: ${path}`);
    },
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <TeamView />
    </QueryClientProvider>,
  );
}

beforeEach(() => apiRequestMock.mockReset());

describe("team workspace", () => {
  it("shows team members, filters and live status", async () => {
    renderTeam();
    expect(await screen.findByText("Алексей Оператор")).toBeInTheDocument();
    expect(screen.getAllByText("Доступен").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Продажи").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("Фильтр по роли"), {
      target: { value: "human_operator" },
    });
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        expect.stringContaining("role=human_operator"),
      ),
    );
  });

  it("lets owner create invitation and exposes one-time link", async () => {
    renderTeam();
    fireEvent.click(
      await screen.findByRole("button", { name: /Пригласить сотрудника/ }),
    );
    fireEvent.change(screen.getByLabelText("E-mail приглашения"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByText("Продажи", { selector: "label" }));
    fireEvent.click(
      screen.getByRole("button", { name: /Создать приглашение/ }),
    );
    expect(
      await screen.findByText("Одноразовая тестовая ссылка"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/accept-invitation\?token=once/),
    ).toBeInTheDocument();
  });

  it("shows role-based controls to manager but protects owner policy in backend", async () => {
    renderTeam("tenant_manager");
    fireEvent.click(await screen.findByLabelText("Открыть Алексей Оператор"));
    expect(await screen.findByLabelText("Роль сотрудника")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Заблокировать" }),
    ).toBeInTheDocument();
  });

  it("keeps analyst workspace read-only", async () => {
    renderTeam("analyst");
    expect(await screen.findByText("Алексей Оператор")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Пригласить сотрудника/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /Приглашения/ }),
    ).not.toBeInTheDocument();
  });
});
