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

  it("accepts Karakalpak BCP 47 variants without rewriting Georgian", () => {
    const karakalpak = knowledgeSchema.safeParse({
      title: "FAQ",
      language: "KAA-Latn",
      content: "This text is long enough for validation.",
    });
    const georgian = knowledgeSchema.safeParse({
      title: "FAQ",
      language: "ka",
      content: "This text is long enough for validation.",
    });
    expect(karakalpak.success).toBe(true);
    expect(georgian.success).toBe(true);
    if (karakalpak.success && georgian.success) {
      expect(karakalpak.data.language).toBe("kaa-latn");
      expect(georgian.data.language).toBe("ka");
      expect(georgian.data.language).not.toBe("kaa");
    }
  });
});
