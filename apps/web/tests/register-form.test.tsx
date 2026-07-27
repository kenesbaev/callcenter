import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RegisterForm } from "@/components/register-form";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RegisterForm", () => {
  it("keeps user input when the database is temporarily unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "database_unavailable",
              message: "Сервис временно недоступен. Попробуйте ещё раз.",
              correlation_id: "request-2",
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    render(<RegisterForm />);

    fireEvent.change(screen.getByLabelText("Название компании"), {
      target: { value: "K-Line Test" },
    });
    fireEvent.change(screen.getByLabelText("Адрес компании"), {
      target: { value: "kline-test" },
    });
    fireEvent.change(screen.getByLabelText("Ваше имя"), {
      target: { value: "Алина" },
    });
    fireEvent.change(screen.getByLabelText("Рабочая почта"), {
      target: { value: "alina@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Пароль"), {
      target: { value: "SecurePass123!" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Создать компанию/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Сервис временно недоступен. Попробуйте ещё раз.",
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Название компании")).toHaveValue(
        "K-Line Test",
      ),
    );
    expect(screen.getByLabelText("Рабочая почта")).toHaveValue(
      "alina@example.com",
    );
  });
});
