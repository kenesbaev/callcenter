"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Button } from "@teamora/ui";
import { apiRequest, ApiClientError } from "@/lib/api";
import { loginSchema, type LoginValues } from "@/lib/schemas";
import type { AuthResponse } from "@/lib/types";

export function LoginForm() {
  const router = useRouter();
  const [error, setError] = useState("");
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { company_slug: "", email: "", password: "" },
  });

  async function submit(values: LoginValues) {
    setError("");
    try {
      await apiRequest<AuthResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify(values),
      });
      router.push("/app");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiClientError ? caught.message : "Не удалось войти",
      );
    }
  }

  return (
    <div className="auth-card">
      <h2>Вход в K-Line</h2>
      <p>Введите адрес компании и данные аккаунта.</p>
      <form
        className="form-stack"
        onSubmit={form.handleSubmit(submit)}
        noValidate
      >
        <div className="field">
          <label htmlFor="login-company-slug">Адрес компании</label>
          <input
            id="login-company-slug"
            autoCapitalize="none"
            autoComplete="organization"
            {...form.register("company_slug")}
          />
          {form.formState.errors.company_slug && (
            <span className="field-error">
              {form.formState.errors.company_slug.message}
            </span>
          )}
        </div>
        <div className="field">
          <label htmlFor="login-email">Email</label>
          <input
            id="login-email"
            autoCapitalize="none"
            autoComplete="email"
            type="email"
            {...form.register("email")}
          />
          {form.formState.errors.email && (
            <span className="field-error">
              {form.formState.errors.email.message}
            </span>
          )}
        </div>
        <div className="field">
          <label htmlFor="login-password">Пароль</label>
          <input
            id="login-password"
            autoComplete="current-password"
            type="password"
            {...form.register("password")}
          />
          {form.formState.errors.password && (
            <span className="field-error">
              {form.formState.errors.password.message}
            </span>
          )}
        </div>
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <Button disabled={form.formState.isSubmitting} type="submit">
          {form.formState.isSubmitting ? "Входим…" : "Войти"}
        </Button>
      </form>
      <p className="auth-footnote">
        <Link href="/forgot-password">Забыли пароль?</Link> ·{" "}
        <Link href="/register">Создать компанию</Link>
      </p>
    </div>
  );
}
