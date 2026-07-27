import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ComingLater } from "@/components/coming-later";

describe("ComingLater", () => {
  it("never presents an unavailable integration as working", () => {
    render(<ComingLater path="integrations" />);
    expect(screen.getByText("В разработке")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Интеграции" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
