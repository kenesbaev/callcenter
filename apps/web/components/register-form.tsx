"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Button } from "@teamora/ui";
import { apiRequest, ApiClientError } from "@/lib/api";
import { registerSchema, type RegisterValues } from "@/lib/schemas";
import type { AuthResponse } from "@/lib/types";

export function RegisterForm() {
  const router = useRouter();
  const [error, setError] = useState("");
  const form = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: {
      company_name: "",
      company_slug: "",
      display_name: "",
      email: "",
      password: "",
    },
  });

  async function submit(values: RegisterValues) {
    setError("");
    try {
      await apiRequest<AuthResponse>("/auth/register", {
        method: "POST",
        body: JSON.stringify(values),
      });
      router.push("/app");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiClientError
          ? caught.message
          : "Registration could not be completed",
      );
    }
  }

  return (
    <div className="auth-card">
      <h2>Создать компанию</h2>
      <p>Первый аккаунт получит права владельца.</p>
      <form
        className="form-stack"
        onSubmit={form.handleSubmit(submit)}
        noValidate
      >
        <Field
          name="company_name"
          label="Название компании"
          error={form.formState.errors.company_name?.message}
        >
          <input
            id="company_name"
            autoComplete="organization"
            {...form.register("company_name")}
          />
        </Field>
        <Field
          name="company_slug"
          label="Адрес компании"
          error={form.formState.errors.company_slug?.message}
        >
          <input
            id="company_slug"
            autoCapitalize="none"
            autoComplete="off"
            placeholder="kline-support"
            {...form.register("company_slug")}
          />
        </Field>
        <Field
          name="display_name"
          label="Ваше имя"
          error={form.formState.errors.display_name?.message}
        >
          <input
            id="display_name"
            autoComplete="name"
            {...form.register("display_name")}
          />
        </Field>
        <Field
          name="email"
          label="Рабочая почта"
          error={form.formState.errors.email?.message}
        >
          <input
            id="email"
            autoCapitalize="none"
            autoComplete="email"
            type="email"
            {...form.register("email")}
          />
        </Field>
        <Field
          name="password"
          label="Пароль"
          error={form.formState.errors.password?.message}
        >
          <input
            id="password"
            autoComplete="new-password"
            type="password"
            {...form.register("password")}
          />
        </Field>
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <Button disabled={form.formState.isSubmitting} type="submit">
          {form.formState.isSubmitting ? "Создаём…" : "Создать компанию"}{" "}
          <ArrowRight size={16} />
        </Button>
      </form>
      <p className="auth-footnote">
        Уже зарегистрированы? <Link href="/login">Войти</Link>
      </p>
    </div>
  );
}

function Field({
  name,
  label,
  error,
  children,
}: {
  name: string;
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={name}>{label}</label>
      {children}
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}
