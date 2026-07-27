import { z } from "zod";

export const registerSchema = z.object({
  company_name: z.string().min(2).max(160),
  company_slug: z
    .string()
    .min(3)
    .max(80)
    .regex(
      /^[a-z0-9]+(?:-[a-z0-9]+)*$/,
      "Use lowercase letters, numbers, and hyphens",
    ),
  display_name: z.string().min(2).max(160),
  email: z.email(),
  password: z
    .string()
    .min(12)
    .max(128)
    .regex(/[a-z]/, "Add a lowercase letter")
    .regex(/[A-Z]/, "Add an uppercase letter")
    .regex(/[0-9]/, "Add a number"),
});

export const loginSchema = z.object({
  company_slug: z.string().min(3).max(80),
  email: z.email(),
  password: z.string().min(1).max(128),
});

export const operatorSchema = z.object({
  name: z.string().min(2).max(120),
  description: z.string().max(500),
  system_instructions: z.string().min(20).max(12_000),
});

export const knowledgeSchema = z.object({
  title: z.string().min(2).max(240),
  language: z.enum(["ru", "en", "uz"]),
  content: z.string().min(20).max(100_000),
});

export type RegisterValues = z.infer<typeof registerSchema>;
export type LoginValues = z.infer<typeof loginSchema>;
export type OperatorValues = z.infer<typeof operatorSchema>;
export type KnowledgeValues = z.infer<typeof knowledgeSchema>;
