import { describe, expect, it } from "vitest";
import { knowledgeSchema, registerSchema } from "@/lib/schemas";

describe("form schemas", () => {
  it("rejects a weak registration password", () => {
    const result = registerSchema.safeParse({
      company_name: "Acme",
      company_slug: "acme",
      display_name: "Owner",
      email: "owner@example.com",
      password: "weak-password",
    });
    expect(result.success).toBe(false);
  });

  it("does not expose Karakalpak as a normal knowledge option", () => {
    const result = knowledgeSchema.safeParse({
      title: "FAQ",
      language: "kaa",
      content: "This text is long enough for validation.",
    });
    expect(result.success).toBe(false);
  });
});
