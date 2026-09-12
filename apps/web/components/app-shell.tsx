"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  BookOpen,
  Bot,
  CalendarClock,
  ChevronDown,
  ContactRound,
  FolderKanban,
  Headphones,
  LayoutDashboard,
  ListTodo,
  LogOut,
  MessageSquareText,
  Mic2,
  PhoneCall,
  Radio,
  Settings,
  Sparkles,
  Users,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { TeamoraLogo } from "@teamora/ui";
import { apiRequest, ApiClientError } from "@/lib/api";
import type { AuthResponse, OperatorPresence, Role } from "@/lib/types";
import { RealtimeProvider, useRealtime } from "@/components/realtime-provider";
import { broadcastRealtimeLogout } from "@/lib/realtime";

const taskRoles: Role[] = [
  "tenant_owner",
  "tenant_manager",
  "human_operator",
  "analyst",
];
const dialerRoles: Role[] = [
  "tenant_owner",
  "tenant_manager",
  "human_operator",
];
const managementRoles: Role[] = ["tenant_owner", "tenant_manager"];
const settingsRoles: Role[] = ["tenant_owner", "tenant_manager", "analyst"];
const analyticsRoles: Role[] = [
  "tenant_owner",
  "tenant_manager",
  "human_operator",
  "analyst",
];

const primaryNav: Array<{
  href: string;
  label: string;
  icon: typeof FolderKanban;
  roles?: Role[];
}> = [
  { href: "/app/projects", label: "Проекты", icon: FolderKanban },
  { href: "/app/overview", label: "Обзор", icon: LayoutDashboard },
  {
    href: "/app/dialer",
    label: "Диалер",
    icon: PhoneCall,
    roles: dialerRoles,
  },
  {
    href: "/app/customers",
    label: "Клиенты",
    icon: ContactRound,
    roles: dialerRoles,
  },
  { href: "/app/tasks", label: "Задачи", icon: ListTodo, roles: taskRoles },
  {
    href: "/app/callbacks",
    label: "Перезвоны",
    icon: CalendarClock,
    roles: dialerRoles,
  },
  {
    href: "/app/ai-operators",
    label: "AI-операторы",
    icon: Bot,
    roles: managementRoles,
  },
  {
    href: "/app/ai-setup",
    label: "Настройка AI",
    icon: Sparkles,
    roles: managementRoles,
  },
  {
    href: "/app/knowledge",
    label: "База знаний",
    icon: BookOpen,
    roles: managementRoles,
  },
  { href: "/app/conversations", label: "Разговоры", icon: Headphones },
];

const secondaryNav: Array<{
  href: string;
  label: string;
  icon: typeof Radio;
  roles?: Role[];
}> = [
  {
    href: "/app/live",
    label: "Активные звонки",
    icon: Radio,
    roles: analyticsRoles,
  },
  { href: "/app/team", label: "Команда", icon: Users, roles: taskRoles },
  {
    href: "/app/integrations",
    label: "Интеграции",
    icon: Wrench,
    roles: managementRoles,
  },
  {
    href: "/app/language-lab",
    label: "Voice AI Lab",
    icon: Mic2,
    roles: managementRoles,
  },
  {
    href: "/app/analytics",
    label: "Аналитика",
    icon: BarChart3,
    roles: analyticsRoles,
  },
  {
    href: "/app/settings",
    label: "Настройки",
    icon: Settings,
    roles: settingsRoles,
  },
  {
    href: "/app/dev-simulator",
    label: "AI-симулятор",
    icon: MessageSquareText,
    roles: dialerRoles,
  },
];

const roleLabels: Record<string, string> = {
  platform_admin: "Super admin K-Line",
  tenant_owner: "Владелец компании",
  tenant_manager: "Администратор компании",
  human_operator: "Оператор",
  analyst: "Аналитик",
  billing_admin: "Биллинг",
};

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [presenceSessionKey, setPresenceSessionKey] = useState("");
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
    retry: false,
  });
  useEffect(() => {
    const existing = sessionStorage.getItem("kline_presence_session");
    const value = existing ?? `browser-${crypto.randomUUID()}`;
    if (!existing) sessionStorage.setItem("kline_presence_session", value);
    setPresenceSessionKey(value);
  }, []);
  const canUsePresence =
    me.data?.user.role === "tenant_owner" ||
    me.data?.user.role === "tenant_manager" ||
    me.data?.user.role === "human_operator";
  const presence = useQuery({
    queryKey: ["team", "presence", presenceSessionKey],
    queryFn: () =>
      apiRequest<OperatorPresence>("/team/presence/heartbeat", {
        method: "POST",
        body: JSON.stringify({ session_key: presenceSessionKey }),
      }),
    enabled: Boolean(canUsePresence && presenceSessionKey),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
    retry: 1,
  });
  const setStatus = useMutation({
    mutationFn: (status: OperatorPresence["manual_status"]) =>
      apiRequest<OperatorPresence>("/team/presence/status", {
        method: "PUT",
        body: JSON.stringify({ status, session_key: presenceSessionKey }),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData(["team", "presence", presenceSessionKey], value);
      void queryClient.invalidateQueries({ queryKey: ["team"] });
    },
  });
  const requiresLogin =
    me.isError && me.error instanceof ApiClientError && me.error.status === 401;
  useEffect(() => {
    if (requiresLogin) router.replace("/login");
  }, [requiresLogin, router]);

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
    if (requiresLogin) return null;
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

  const authenticated = me.data;

  async function logout() {
    await apiRequest<void>("/auth/logout", { method: "POST" });
    broadcastRealtimeLogout(
      `kline:realtime:${authenticated.tenant.id}:${authenticated.user.id}:cursor`,
    );
    router.replace("/login");
    router.refresh();
  }

  return (
    <RealtimeProvider tenantId={me.data.tenant.id} userId={me.data.user.id}>
      <div className="app-frame">
        <aside className="app-sidebar" aria-label="Навигация кабинета">
          <Link className="sidebar-logo" href="/app">
            <TeamoraLogo />
          </Link>
          <div className="sidebar-section-label">Кабинет</div>
          <nav className="sidebar-nav">
            {primaryNav
              .filter(
                (item) => !item.roles || item.roles.includes(me.data.user.role),
              )
              .map((item) => {
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
            {secondaryNav
              .filter(
                (item) => !item.roles || item.roles.includes(me.data.user.role),
              )
              .map((item) => {
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
              <RealtimeIndicator />
              {canUsePresence && (
                <label className="topbar-presence">
                  <span>
                    {presence.data?.effective_status === "busy"
                      ? "Занят"
                      : presence.data?.effective_status === "on_hold"
                        ? "На удержании"
                        : "Рабочий статус"}
                  </span>
                  <select
                    aria-label="Рабочий статус"
                    disabled={
                      setStatus.isPending ||
                      presence.data?.effective_status === "busy" ||
                      presence.data?.effective_status === "on_hold"
                    }
                    onChange={(event) =>
                      setStatus.mutate(
                        event.target.value as OperatorPresence["manual_status"],
                      )
                    }
                    value={presence.data?.manual_status ?? "offline"}
                  >
                    <option value="available">Доступен</option>
                    <option value="away">Отошёл</option>
                    <option value="on_break">Перерыв</option>
                    <option value="offline">Офлайн</option>
                  </select>
                </label>
              )}
              <div>
                <strong>{me.data.user.display_name}</strong>
                <span>
                  {roleLabels[me.data.user.role] ?? me.data.user.role}
                </span>
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
    </RealtimeProvider>
  );
}

function RealtimeIndicator() {
  const realtime = useRealtime();
  const label =
    realtime.status === "connected"
      ? "В реальном времени"
      : realtime.status === "reconnecting"
        ? "Переподключение"
        : "Офлайн — резервное обновление";
  return (
    <span
      className={`realtime-indicator realtime-${realtime.status}`}
      data-testid="realtime-status"
      title={label}
    >
      <span aria-hidden="true" className="realtime-dot" />
      {label}
    </span>
  );
}
