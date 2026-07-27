import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConversationsView } from "@/components/conversations-view";

vi.mock("@/lib/api", () => ({
  apiRequest: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 }),
}));

describe("ConversationsView", () => {
  it("renders an honest empty state", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ConversationsView />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole("heading", { name: "Разговоров пока нет" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Завершите тестовую симуляцию."),
    ).toBeInTheDocument();
  });
});
