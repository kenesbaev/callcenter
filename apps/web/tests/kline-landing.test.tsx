import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KLineLanding } from "@/components/kline-landing";

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    clear: () => values.clear(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("K-Line landing", () => {
  it("shows K-Line branding and working account links", () => {
    const { container } = render(<KLineLanding />);

    expect(screen.getAllByText(/K-Line/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Войти" })[0]).toHaveAttribute(
      "href",
      "/login",
    );
    expect(
      screen.getAllByRole("link", { name: "Регистрация" })[0],
    ).toHaveAttribute("href", "/register");
    expect(container).not.toHaveTextContent("Teamora Voice");
  });

  it("opens WhatsApp with the entered request instead of claiming server success", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<KLineLanding />);

    fireEvent.change(screen.getByLabelText("Имя"), {
      target: { value: "Алина" },
    });
    fireEvent.change(screen.getByLabelText("Телефон"), {
      target: { value: "+998901234567" },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "alina@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Отправить заявку/ }));

    expect(open).toHaveBeenCalledWith(
      expect.stringContaining("https://wa.me/998905395939?text="),
      "_blank",
      "noopener,noreferrer",
    );
    expect(screen.queryByText(/заявка отправлена/i)).not.toBeInTheDocument();
  });
});
