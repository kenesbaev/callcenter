"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BookOpen,
  Bot,
  ChevronDown,
  Headphones,
  LayoutDashboard,
  LogOut,
  MessageSquareText,
  Radio,
  Settings,
  Users,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { TeamoraLogo } from "@teamora/ui";
import { apiRequest, ApiClientError } from "@/lib/api";
import type { AuthResponse } from "@/lib/types";

const primaryNav = [
  { href: "/app", label: "Обзор", icon: LayoutDashboard },
  { href: "/app/ai-operators", label: "AI-операторы", icon: Bot },
  { href: "/app/knowledge", label: "База знаний", icon: BookOpen },
  {
    href: "/app/dev-simulator",
    label: "Симулятор",
    icon: MessageSquareText,
  },
  { href: "/app/conversations", label: "Разговоры", icon: Headphones },
];

const secondaryNav = [
  { href: "/app/live", label: "Активные звонки", icon: Radio },
  { href: "/app/team", label: "Команда", icon: Users },
  { href: "/app/integrations", label: "Интеграции", icon: Wrench },
  { href: "/app/analytics", label: "Аналитика", icon: BarChart3 },
  { href: "/app/settings", label: "Настройки", icon: Settings },
];

const roleLabels: Record<string, string> = {
  platform_admin: "Администратор платформы",
  tenant_owner: "Владелец",
  tenant_manager: "Менеджер",
  human_operator: "Оператор",
  analyst: "Аналитик",
  billing_admin: "Биллинг",
};

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
    retry: false,
  });

  if (me.isPending) {
    return (
      <main className="app-gate" id="main-content">
        <div aria-label="Загрузка кабинета" className="app-gate-card skeleton">
          Загрузка
        </div>
      </main>
    );
  }

  if (me.isError) {
    if (me.error instanceof ApiClientError && me.error.status === 401) {
      router.replace("/login");
      return null;
    }
    return (
      <main className="app-gate" id="main-content">
        <div className="error-state panel">
          <div>
            <h1>Кабинет недоступен</h1>
            <p>
              {me.error instanceof Error
                ? me.error.message
                : "Не удалось загрузить кабинет."}
            </p>
            <button
              className="tv-button tv-button-secondary"
              onClick={() => void me.refetch()}
              type="button"
            >
              Повторить
            </button>
          </div>
        </div>
      </main>
    );
  }

  async function logout() {
    await apiRequest<void>("/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="app-frame">
      <aside className="app-sidebar" aria-label="Навигация кабинета">
        <Link className="sidebar-logo" href="/app">
          <TeamoraLogo />
        </Link>
        <div className="sidebar-section-label">Кабинет</div>
        <nav className="sidebar-nav">
          {primaryNav.map((item) => {
            const active =
              item.href === "/app"
                ? pathname === item.href
                : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                aria-current={active ? "page" : undefined}
                className={`sidebar-link${active ? " active" : ""}`}
                href={item.href}
                key={item.href}
              >
                <Icon aria-hidden="true" size={17} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-section-label">Далее</div>
        <div className="sidebar-nav">
          {secondaryNav.map((item) => {
            const active = pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                aria-current={active ? "page" : undefined}
                className={`sidebar-link${active ? " active" : ""}`}
                href={item.href}
                key={item.href}
              >
                <Icon aria-hidden="true" size={17} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
        <div className="sidebar-footer">DEV · Телефония отключена</div>
      </aside>
      <div className="app-main">
        <header className="app-topbar row-between">
          <div className="workspace-name">
            <strong>{me.data.tenant.name}</strong>
            <span>{me.data.tenant.slug}</span>
          </div>
          <div className="topbar-user">
            <div>
              <strong>{me.data.user.display_name}</strong>
              <span>{roleLabels[me.data.user.role] ?? me.data.user.role}</span>
            </div>
            <ChevronDown aria-hidden="true" size={15} />
            <button
              aria-label="Выйти"
              className="icon-button"
              onClick={() => void logout()}
              title="Выйти"
              type="button"
            >
              <LogOut aria-hidden="true" size={17} />
            </button>
          </div>
        </header>
        <main className="app-content" id="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}
