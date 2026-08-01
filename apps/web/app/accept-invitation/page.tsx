"use client";

import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Button, TeamoraLogo } from "@teamora/ui";
import { apiRequest } from "@/lib/api";

type AcceptedInvitation = {
  membership_id: string;
  tenant_id: string;
  tenant_slug: string;
  email: string;
  role: string;
  already_accepted: boolean;
};

function AcceptInvitationForm() {
  const search = useSearchParams();
  const tenant = search.get("tenant") ?? "";
  const token = search.get("token") ?? "";
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const accept = useMutation({
    mutationFn: () =>
      apiRequest<AcceptedInvitation>("/team/invitations/accept", {
        method: "POST",
        body: JSON.stringify({
          tenant_slug: tenant,
          token,
          display_name: displayName || null,
          password,
        }),
      }),
  });

  return (
    <main className="invitation-page">
      <section className="invitation-card">
        <TeamoraLogo />
        {accept.data ? (
          <div className="invitation-success">
            <CheckCircle2 size={34} />
            <h1>Приглашение принято</h1>
            <p>
              Доступ к компании активирован. Войдите с e-mail{" "}
              {accept.data.email}.
            </p>
            <Link
              className="tv-button tv-button-primary"
              href={`/login?company=${accept.data.tenant_slug}`}
            >
              Перейти ко входу
            </Link>
          </div>
        ) : (
          <form
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              accept.mutate();
            }}
          >
            <ShieldCheck size={28} />
            <h1>Присоединиться к K-Line</h1>
            <p>
              Создайте профиль или подтвердите пароль существующего аккаунта.
            </p>
            <label>
              <span>Имя сотрудника</span>
              <input
                aria-label="Имя сотрудника"
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Для нового аккаунта"
                value={displayName}
              />
            </label>
            <label>
              <span>Пароль</span>
              <input
                aria-label="Пароль"
                minLength={12}
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </label>
            {(!tenant || !token) && (
              <p className="form-error">Ссылка приглашения неполная.</p>
            )}
            {accept.error && (
              <p className="form-error">{accept.error.message}</p>
            )}
            <Button
              disabled={!tenant || !token || accept.isPending}
              type="submit"
            >
              Принять приглашение
            </Button>
          </form>
        )}
      </section>
    </main>
  );
}

export default function AcceptInvitationPage() {
  return (
    <Suspense
      fallback={
        <main className="invitation-page">
          <section className="invitation-card skeleton">
            Загрузка приглашения…
          </section>
        </main>
      }
    >
      <AcceptInvitationForm />
    </Suspense>
  );
}
