"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  CheckCircle2,
  Clock3,
  History,
  MailPlus,
  RefreshCw,
  Search,
  ShieldCheck,
  UserCog,
  Users,
  X,
} from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { apiRequest, idempotencyKey } from "@/lib/api";
import type {
  AuthResponse,
  Page,
  Project,
  TeamAudit,
  TeamInvitation,
  TeamMember,
} from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

const roleLabel: Record<string, string> = {
  tenant_owner: "Владелец",
  tenant_manager: "Менеджер",
  human_operator: "Оператор",
  analyst: "Аналитик",
};
const statusLabel: Record<string, string> = {
  offline: "Офлайн",
  available: "Доступен",
  away: "Отошёл",
  on_break: "Перерыв",
  busy: "Разговаривает",
  on_hold: "На удержании",
};
const statusTone: Record<string, "neutral" | "success" | "warning" | "danger"> =
  {
    offline: "neutral",
    available: "success",
    away: "warning",
    on_break: "warning",
    busy: "danger",
    on_hold: "warning",
  };
const invitationStatus: Record<string, string> = {
  pending: "Ожидает",
  accepted: "Принято",
  cancelled: "Отменено",
  expired: "Истекло",
};

function dateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Не удалось выполнить действие";
}

export function TeamView() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"members" | "invitations">("members");
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [projectId, setProjectId] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<TeamMember | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<
    "tenant_manager" | "human_operator" | "analyst"
  >("human_operator");
  const [inviteProjects, setInviteProjects] = useState<string[]>([]);
  const [inviteExpiresInDays, setInviteExpiresInDays] = useState(7);
  const [oneTimeLink, setOneTimeLink] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const ownProfile = useQuery({
    queryKey: ["team", "me"],
    queryFn: () => apiRequest<TeamMember>("/team/me"),
  });
  const canManage =
    me.data?.user.role === "tenant_owner" ||
    me.data?.user.role === "tenant_manager";
  const query = new URLSearchParams({ limit: "25", offset: String(offset) });
  if (search.trim()) query.set("search", search.trim());
  if (role) query.set("role", role);
  if (projectId) query.set("project_id", projectId);
  if (status) query.set("effective_status", status);
  const team = useQuery({
    queryKey: ["team", search, role, projectId, status, offset],
    queryFn: () => apiRequest<Page<TeamMember>>(`/team?${query}`),
  });
  const projects = useQuery({
    queryKey: ["projects", "team-options"],
    queryFn: () => apiRequest<Page<Project>>("/projects?limit=100"),
  });
  const invitations = useQuery({
    queryKey: ["team", "invitations"],
    queryFn: () =>
      apiRequest<Page<TeamInvitation>>("/team/invitations?limit=100"),
    enabled: Boolean(canManage),
  });
  const history = useQuery({
    queryKey: ["team", selected?.membership_id, "history"],
    queryFn: () =>
      apiRequest<Page<TeamAudit>>(
        `/team/${selected?.membership_id}/history?limit=20`,
      ),
    enabled: Boolean(selected && canManage),
  });

  const refreshTeam = async (updated?: TeamMember) => {
    if (updated) setSelected(updated);
    await queryClient.invalidateQueries({ queryKey: ["team"] });
  };
  const editMember = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      if (!selected) throw new Error("Сотрудник не выбран");
      const data = new FormData(form);
      return apiRequest<TeamMember>(`/team/${selected.membership_id}/profile`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: selected.state_version,
          display_name: String(data.get("display_name") ?? ""),
          phone: String(data.get("phone") ?? "") || null,
          job_title: String(data.get("job_title") ?? "") || null,
          extension: String(data.get("extension") ?? "") || null,
          interface_language: String(data.get("interface_language") ?? "ru"),
          timezone: String(data.get("timezone") ?? "") || null,
          is_transfer_available: data.get("is_transfer_available") === "on",
        }),
      });
    },
    onSuccess: async (value) => {
      setFeedback("Профиль сотрудника сохранён");
      await refreshTeam(value);
    },
  });
  const roleMutation = useMutation({
    mutationFn: ({
      member,
      nextRole,
    }: {
      member: TeamMember;
      nextRole: string;
    }) =>
      apiRequest<TeamMember>(`/team/${member.membership_id}/role`, {
        method: "PUT",
        body: JSON.stringify({
          role: nextRole,
          expected_version: member.state_version,
        }),
      }),
    onSuccess: async (value) => {
      setFeedback("Роль сотрудника обновлена");
      await refreshTeam(value);
    },
  });
  const ownershipMutation = useMutation({
    mutationFn: (member: TeamMember) => {
      const currentOwner = ownProfile.data;
      if (!currentOwner)
        throw new Error("Не удалось определить текущего владельца");
      return apiRequest<TeamMember>(
        `/team/${member.membership_id}/transfer-ownership`,
        {
          method: "POST",
          body: JSON.stringify({
            current_owner_expected_version: currentOwner.state_version,
            target_expected_version: member.state_version,
          }),
        },
      );
    },
    onSuccess: () => {
      setFeedback(
        "Роль владельца передана. Войдите снова, чтобы обновить права текущей сессии.",
      );
      window.setTimeout(() => window.location.assign("/login"), 1200);
    },
  });
  const projectsMutation = useMutation({
    mutationFn: ({ member, ids }: { member: TeamMember; ids: string[] }) =>
      apiRequest<TeamMember>(`/team/${member.membership_id}/projects`, {
        method: "PUT",
        body: JSON.stringify({
          project_ids: ids,
          expected_version: member.state_version,
        }),
      }),
    onSuccess: async (value) => {
      setFeedback("Назначения на проекты обновлены");
      await refreshTeam(value);
    },
  });
  const accountMutation = useMutation({
    mutationFn: ({
      member,
      action,
    }: {
      member: TeamMember;
      action: "block" | "restore";
    }) =>
      apiRequest<TeamMember>(`/team/${member.membership_id}/${action}`, {
        method: "POST",
        body: JSON.stringify(
          action === "block"
            ? {
                expected_version: member.state_version,
                reason: "Заблокирован администратором",
              }
            : { expected_version: member.state_version },
        ),
      }),
    onSuccess: async (value) => {
      setFeedback(
        value.is_active
          ? "Доступ сотрудника восстановлен"
          : "Сотрудник заблокирован",
      );
      await refreshTeam(value);
    },
  });
  const createInvite = useMutation({
    mutationFn: () =>
      apiRequest<TeamInvitation>("/team/invitations", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("team-invite") },
        body: JSON.stringify({
          email: inviteEmail,
          role: inviteRole,
          project_ids: inviteProjects,
          expires_in_days: inviteExpiresInDays,
        }),
      }),
    onSuccess: async (value) => {
      setInviteOpen(false);
      setInviteEmail("");
      setInviteProjects([]);
      setInviteExpiresInDays(7);
      setOneTimeLink(value.acceptance_url);
      setFeedback("Приглашение создано");
      await queryClient.invalidateQueries({
        queryKey: ["team", "invitations"],
      });
    },
  });
  const invitationAction = useMutation({
    mutationFn: ({
      invite,
      action,
    }: {
      invite: TeamInvitation;
      action: "reissue" | "cancel";
    }) =>
      apiRequest<TeamInvitation>(`/team/invitations/${invite.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ expected_version: invite.state_version }),
      }),
    onSuccess: async (value) => {
      if (value.acceptance_url) setOneTimeLink(value.acceptance_url);
      setFeedback(
        value.status === "cancelled"
          ? "Приглашение отменено"
          : "Приглашение перевыпущено",
      );
      await queryClient.invalidateQueries({
        queryKey: ["team", "invitations"],
      });
    },
  });

  const selectedProjectIds = useMemo(
    () => new Set(selected?.projects.map((item) => item.id) ?? []),
    [selected],
  );
  const canManageSelected = Boolean(
    selected &&
    (me.data?.user.role === "tenant_owner" ||
      (me.data?.user.role === "tenant_manager" &&
        ["human_operator", "analyst"].includes(selected.role))),
  );
  const canEditOwnProfile = Boolean(
    selected?.user_id === me.data?.user.id && me.data?.user.role !== "analyst",
  );
  const mutationError =
    editMember.error ??
    roleMutation.error ??
    projectsMutation.error ??
    accountMutation.error ??
    ownershipMutation.error ??
    createInvite.error ??
    invitationAction.error;

  if (team.isPending || me.isPending) return <SectionSkeleton />;
  if (team.isError)
    return (
      <QueryError
        error={team.error}
        retry={() => void team.refetch()}
        title="Не удалось загрузить команду"
      />
    );

  return (
    <>
      <div className="page-heading row-between">
        <div>
          <h1>Команда</h1>
          <p>Сотрудники, роли, проекты и присутствие операторов</p>
        </div>
        {canManage && (
          <Button onClick={() => setInviteOpen(true)}>
            <MailPlus size={17} /> Пригласить сотрудника
          </Button>
        )}
      </div>

      {(feedback || mutationError) && (
        <div
          className={`team-feedback ${mutationError ? "error" : "success"}`}
          role="status"
        >
          {mutationError ? message(mutationError) : feedback}
          <button
            aria-label="Закрыть сообщение"
            onClick={() => setFeedback(null)}
            type="button"
          >
            <X size={15} />
          </button>
        </div>
      )}
      {oneTimeLink && (
        <div className="team-invite-secret panel">
          <ShieldCheck size={20} />
          <div>
            <strong>Одноразовая тестовая ссылка</strong>
            <p>
              Скопируйте её сейчас. После закрытия она больше не будет показана.
            </p>
            <code>{oneTimeLink}</code>
          </div>
          <Button onClick={() => setOneTimeLink(null)} variant="secondary">
            Закрыть
          </Button>
        </div>
      )}

      <div className="team-tabs" role="tablist">
        <button
          aria-selected={tab === "members"}
          onClick={() => setTab("members")}
          role="tab"
          type="button"
        >
          <Users size={16} /> Сотрудники <span>{team.data.total}</span>
        </button>
        {canManage && (
          <button
            aria-selected={tab === "invitations"}
            onClick={() => setTab("invitations")}
            role="tab"
            type="button"
          >
            <MailPlus size={16} /> Приглашения{" "}
            <span>{invitations.data?.total ?? 0}</span>
          </button>
        )}
      </div>

      {tab === "members" ? (
        <section className="panel team-panel">
          <div className="team-filterbar">
            <label className="team-search">
              <Search size={16} />
              <input
                aria-label="Поиск сотрудников"
                onChange={(event) => {
                  setSearch(event.target.value);
                  setOffset(0);
                }}
                placeholder="Имя, e-mail или телефон"
                value={search}
              />
            </label>
            <select
              aria-label="Фильтр по роли"
              onChange={(event) => {
                setRole(event.target.value);
                setOffset(0);
              }}
              value={role}
            >
              <option value="">Все роли</option>
              <option value="tenant_owner">Владелец</option>
              <option value="tenant_manager">Менеджер</option>
              <option value="human_operator">Оператор</option>
              <option value="analyst">Аналитик</option>
            </select>
            <select
              aria-label="Фильтр по проекту"
              onChange={(event) => {
                setProjectId(event.target.value);
                setOffset(0);
              }}
              value={projectId}
            >
              <option value="">Все проекты</option>
              {projects.data?.items.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Фильтр по статусу"
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
              value={status}
            >
              <option value="">Все статусы</option>
              <option value="available">Доступен</option>
              <option value="busy">Разговаривает</option>
              <option value="away">Отошёл</option>
              <option value="on_break">Перерыв</option>
              <option value="offline">Офлайн</option>
            </select>
          </div>
          {team.data.items.length === 0 ? (
            <div className="empty-state">
              <div>
                <Users size={28} />
                <h3>Сотрудники не найдены</h3>
                <p>Измените фильтры или пригласите нового сотрудника.</p>
              </div>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data-table team-table">
                <thead>
                  <tr>
                    <th>Сотрудник</th>
                    <th>Роль</th>
                    <th>Рабочий статус</th>
                    <th>Проекты</th>
                    <th>Линия</th>
                    <th>Активность</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {team.data.items.map((member) => (
                    <tr key={member.membership_id}>
                      <td>
                        <strong>{member.display_name}</strong>
                        <div className="table-secondary">{member.email}</div>
                      </td>
                      <td>{roleLabel[member.role] ?? member.role}</td>
                      <td>
                        <StatusBadge tone={statusTone[member.effective_status]}>
                          {statusLabel[member.effective_status]}
                        </StatusBadge>
                      </td>
                      <td>
                        <div className="team-project-chips">
                          {member.projects.slice(0, 2).map((project) => (
                            <span key={project.id}>{project.name}</span>
                          ))}
                          {member.projects.length > 2 && (
                            <span>+{member.projects.length - 2}</span>
                          )}
                        </div>
                      </td>
                      <td>
                        {member.extension ?? "—"}
                        {member.current_call_id && (
                          <div className="table-secondary">Есть звонок</div>
                        )}
                      </td>
                      <td>
                        <StatusBadge
                          tone={member.is_active ? "success" : "danger"}
                        >
                          {member.is_active ? "Активен" : "Заблокирован"}
                        </StatusBadge>
                        <div className="table-secondary">
                          {dateTime(member.last_heartbeat_at)}
                        </div>
                      </td>
                      <td>
                        <button
                          className="icon-button"
                          aria-label={`Открыть ${member.display_name}`}
                          onClick={() => {
                            setSelected(member);
                            setFeedback(null);
                          }}
                          type="button"
                        >
                          <UserCog size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="team-pagination">
            <span>
              Показано {team.data.items.length} из {team.data.total}
            </span>
            <div>
              <Button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 25))}
                variant="secondary"
              >
                Назад
              </Button>
              <Button
                disabled={offset + 25 >= team.data.total}
                onClick={() => setOffset(offset + 25)}
                variant="secondary"
              >
                Далее
              </Button>
            </div>
          </div>
        </section>
      ) : (
        <section className="panel team-panel">
          {invitations.isPending ? (
            <SectionSkeleton />
          ) : invitations.data?.items.length ? (
            <div className="team-invitation-list">
              {invitations.data.items.map((invite) => (
                <article key={invite.id}>
                  <div>
                    <strong>{invite.email}</strong>
                    <p>
                      {roleLabel[invite.role]} · до{" "}
                      {dateTime(invite.expires_at)}
                    </p>
                  </div>
                  <StatusBadge
                    tone={
                      invite.status === "pending"
                        ? "warning"
                        : invite.status === "accepted"
                          ? "success"
                          : "neutral"
                    }
                  >
                    {invitationStatus[invite.status]}
                  </StatusBadge>
                  {invite.status !== "accepted" && (
                    <div className="inline-badges">
                      <Button
                        disabled={invitationAction.isPending}
                        onClick={() =>
                          invitationAction.mutate({ invite, action: "reissue" })
                        }
                        variant="secondary"
                      >
                        <RefreshCw size={15} /> Перевыпустить
                      </Button>
                      {invite.status === "pending" && (
                        <Button
                          disabled={invitationAction.isPending}
                          onClick={() =>
                            invitationAction.mutate({
                              invite,
                              action: "cancel",
                            })
                          }
                          variant="secondary"
                        >
                          <Ban size={15} /> Отменить
                        </Button>
                      )}
                    </div>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <div>
                <MailPlus size={28} />
                <h3>Нет приглашений</h3>
                <p>Создайте приглашение и назначьте проекты.</p>
              </div>
            </div>
          )}
        </section>
      )}

      {selected && (
        <div
          className="task-modal-backdrop"
          onMouseDown={() => setSelected(null)}
          role="presentation"
        >
          <div
            aria-label="Карточка сотрудника"
            aria-modal="true"
            className="team-member-drawer"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <header>
              <div>
                <span>Карточка сотрудника</span>
                <h2>{selected.display_name}</h2>
                <p>{selected.email}</p>
              </div>
              <button
                aria-label="Закрыть карточку"
                className="icon-button"
                onClick={() => setSelected(null)}
                type="button"
              >
                <X size={17} />
              </button>
            </header>
            <form
              onSubmit={(event: FormEvent<HTMLFormElement>) => {
                event.preventDefault();
                editMember.mutate(event.currentTarget);
              }}
            >
              <div className="team-member-grid">
                <label>
                  <span>Имя</span>
                  <input
                    defaultValue={selected.display_name}
                    disabled={!canManageSelected && !canEditOwnProfile}
                    name="display_name"
                  />
                </label>
                <label>
                  <span>Телефон</span>
                  <input
                    defaultValue={selected.phone ?? ""}
                    disabled={!canManageSelected && !canEditOwnProfile}
                    name="phone"
                  />
                </label>
                <label>
                  <span>Должность</span>
                  <input
                    defaultValue={selected.job_title ?? ""}
                    disabled={!canManageSelected && !canEditOwnProfile}
                    name="job_title"
                  />
                </label>
                <label>
                  <span>Внутренний номер</span>
                  <input
                    defaultValue={selected.extension ?? ""}
                    disabled={!canManageSelected}
                    name="extension"
                  />
                </label>
                <label>
                  <span>Язык интерфейса</span>
                  <input
                    defaultValue={selected.interface_language}
                    disabled={!canManageSelected && !canEditOwnProfile}
                    name="interface_language"
                  />
                </label>
                <label>
                  <span>Часовой пояс</span>
                  <input
                    defaultValue={selected.timezone ?? "Asia/Tashkent"}
                    disabled={!canManageSelected && !canEditOwnProfile}
                    name="timezone"
                  />
                </label>
              </div>
              <label className="team-transfer-toggle">
                <input
                  defaultChecked={selected.is_transfer_available}
                  disabled={!canManageSelected}
                  name="is_transfer_available"
                  type="checkbox"
                />{" "}
                Доступен для перевода звонков
              </label>
              {(canManageSelected || canEditOwnProfile) && (
                <Button disabled={editMember.isPending} type="submit">
                  Сохранить профиль
                </Button>
              )}
            </form>
            {canManageSelected && selected.role !== "tenant_owner" && (
              <section>
                <h3>Роль и доступ</h3>
                <div className="team-admin-row">
                  <select
                    aria-label="Роль сотрудника"
                    onChange={(event) =>
                      roleMutation.mutate({
                        member: selected,
                        nextRole: event.target.value,
                      })
                    }
                    value={selected.role}
                  >
                    <option value="tenant_manager">Менеджер</option>
                    <option value="human_operator">Оператор</option>
                    <option value="analyst">Аналитик</option>
                  </select>
                  <Button
                    disabled={accountMutation.isPending}
                    onClick={() =>
                      accountMutation.mutate({
                        member: selected,
                        action: selected.is_active ? "block" : "restore",
                      })
                    }
                    variant="secondary"
                  >
                    {selected.is_active ? "Заблокировать" : "Восстановить"}
                  </Button>
                </div>
              </section>
            )}
            {me.data?.user.role === "tenant_owner" &&
              selected.role !== "tenant_owner" &&
              selected.is_active && (
                <section className="team-danger-zone">
                  <h3>Передача владения компанией</h3>
                  <p className="panel-subtitle">
                    Новый владелец получит полный доступ, а ваша роль станет
                    ролью менеджера.
                  </p>
                  <Button
                    disabled={ownershipMutation.isPending}
                    onClick={() => {
                      if (
                        window.confirm(
                          `Передать роль владельца сотруднику «${selected.display_name}»?`,
                        )
                      )
                        ownershipMutation.mutate(selected);
                    }}
                    variant="secondary"
                  >
                    Передать роль владельца
                  </Button>
                </section>
              )}
            {canManageSelected && selected.role !== "tenant_owner" && (
              <section>
                <h3>Проекты</h3>
                <div className="team-project-editor">
                  {projects.data?.items.map((project) => (
                    <label key={project.id}>
                      <input
                        defaultChecked={selectedProjectIds.has(project.id)}
                        name="member_project"
                        type="checkbox"
                        value={project.id}
                      />{" "}
                      {project.name}
                    </label>
                  ))}
                </div>
                <Button
                  onClick={() => {
                    const checked = Array.from(
                      document.querySelectorAll<HTMLInputElement>(
                        'input[name="member_project"]:checked',
                      ),
                    ).map((item) => item.value);
                    projectsMutation.mutate({ member: selected, ids: checked });
                  }}
                  variant="secondary"
                >
                  Сохранить проекты
                </Button>
              </section>
            )}
            <section>
              <h3>
                <History size={16} /> История изменений
              </h3>
              {history.data?.items.length ? (
                <div className="team-history">
                  {history.data.items.map((event) => (
                    <div key={event.id}>
                      <Clock3 size={14} />
                      <span>
                        <strong>{event.action}</strong>
                        <small>
                          {dateTime(event.created_at)}
                          {event.reason ? ` · ${event.reason}` : ""}
                        </small>
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="panel-subtitle">
                  Административных изменений пока нет.
                </p>
              )}
            </section>
          </div>
        </div>
      )}

      {inviteOpen && (
        <div
          className="task-modal-backdrop"
          onMouseDown={() => setInviteOpen(false)}
          role="presentation"
        >
          <form
            aria-label="Приглашение сотрудника"
            aria-modal="true"
            className="team-invite-modal"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={(event) => {
              event.preventDefault();
              createInvite.mutate();
            }}
            role="dialog"
          >
            <header>
              <div>
                <span>Новый сотрудник</span>
                <h2>Приглашение в K-Line</h2>
              </div>
              <button
                aria-label="Закрыть приглашение"
                className="icon-button"
                onClick={() => setInviteOpen(false)}
                type="button"
              >
                <X size={17} />
              </button>
            </header>
            <label>
              <span>Рабочий e-mail</span>
              <input
                aria-label="E-mail приглашения"
                onChange={(event) => setInviteEmail(event.target.value)}
                required
                type="email"
                value={inviteEmail}
              />
            </label>
            <label>
              <span>Роль</span>
              <select
                aria-label="Роль приглашения"
                onChange={(event) =>
                  setInviteRole(event.target.value as typeof inviteRole)
                }
                value={inviteRole}
              >
                <option value="human_operator">Оператор</option>
                <option value="tenant_manager">Менеджер</option>
                <option value="analyst">Аналитик</option>
              </select>
            </label>
            <label>
              <span>Срок действия</span>
              <select
                aria-label="Срок действия приглашения"
                onChange={(event) =>
                  setInviteExpiresInDays(Number(event.target.value))
                }
                value={inviteExpiresInDays}
              >
                <option value={1}>1 день</option>
                <option value={3}>3 дня</option>
                <option value={7}>7 дней</option>
                <option value={14}>14 дней</option>
                <option value={30}>30 дней</option>
              </select>
            </label>
            <fieldset>
              <legend>Проекты</legend>
              {projects.data?.items.map((project) => (
                <label key={project.id}>
                  <input
                    checked={inviteProjects.includes(project.id)}
                    onChange={(event) =>
                      setInviteProjects((current) =>
                        event.target.checked
                          ? [...current, project.id]
                          : current.filter((id) => id !== project.id),
                      )
                    }
                    type="checkbox"
                  />{" "}
                  {project.name}
                </label>
              ))}
            </fieldset>
            {createInvite.error && (
              <p className="form-error">{message(createInvite.error)}</p>
            )}
            <div className="team-modal-actions">
              <Button
                onClick={() => setInviteOpen(false)}
                type="button"
                variant="secondary"
              >
                Отмена
              </Button>
              <Button disabled={createInvite.isPending} type="submit">
                <CheckCircle2 size={16} /> Создать приглашение
              </Button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
