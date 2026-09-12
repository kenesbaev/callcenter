import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AI_SETUP_QUESTIONS,
  CompanyAiSetupWizard,
} from "@/components/company-ai-setup-wizard";

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  apiRequest: apiRequestMock,
}));

function renderWizard() {
  apiRequestMock.mockResolvedValue({
    user: {
      id: "owner",
      email: "owner@example.com",
      display_name: "Owner",
      role: "tenant_owner",
    },
    tenant: { id: "tenant-1", name: "K-Line", slug: "k-line" },
    csrf_token: "csrf",
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <CompanyAiSetupWizard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
  window.localStorage.clear();
});

describe("company AI setup wizard", () => {
  it("contains exactly twenty company questions", () => {
    expect(AI_SETUP_QUESTIONS).toHaveLength(20);
    expect(
      new Set(AI_SETUP_QUESTIONS.map((question) => question.id)).size,
    ).toBe(20);
  });

  it("prefills the company and saves a frontend-only draft", async () => {
    renderWizard();
    expect(await screen.findByDisplayValue("K-Line")).toBeInTheDocument();
    fireEvent.change(
      screen.getByLabelText("В какой сфере работает компания?"),
      {
        target: { value: "b2b_services" },
      },
    );
    fireEvent.change(screen.getByLabelText("Чем занимается компания?"), {
      target: { value: "Автоматизирует поддержку клиентов." },
    });
    fireEvent.change(
      screen.getByLabelText("Какие товары или услуги вы предлагаете?"),
      { target: { value: "AI-операторы для бизнеса." } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Следующий шаг/ }));
    expect(
      await screen.findByRole("heading", { name: "Задачи" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      const stored = window.localStorage.getItem(
        "kline:ai-company-setup:tenant-1",
      );
      expect(stored).toContain("Автоматизирует поддержку клиентов");
    });
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    expect(apiRequestMock).toHaveBeenCalledWith("/auth/me");
  });

  it("does not skip unanswered questions", async () => {
    renderWizard();
    expect(await screen.findByDisplayValue("K-Line")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Следующий шаг/ }));
    expect(
      await screen.findByText("Ответьте ещё на 3 вопроса этого шага."),
    ).toBeInTheDocument();
  });
});
