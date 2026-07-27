"use client";

import { useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { TeamMember } from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

const roleLabel: Record<string, string> = {
  tenant_owner: "Владелец",
  tenant_manager: "Менеджер",
  human_operator: "Оператор",
  analyst: "Аналитик",
  billing_admin: "Биллинг",
  platform_admin: "Администратор платформы",
};

const operatorStatusLabel: Record<string, string> = {
  offline: "Не в сети",
  available: "Свободен",
  busy: "Занят",
  wrap_up: "Завершает работу",
};

export function TeamView() {
  const team = useQuery({
    queryKey: ["team"],
    queryFn: () => apiRequest<TeamMember[]>("/team"),
  });

  if (team.isPending) return <SectionSkeleton />;
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
        <h1>Команда</h1>
        <StatusBadge>{team.data.length}</StatusBadge>
      </div>
      <section className="panel">
        {team.data.length === 0 ? (
          <div className="empty-state">
            <div>
              <Users aria-hidden="true" size={28} />
              <h3>Команда пока пуста</h3>
            </div>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Сотрудник</th>
                  <th>Роль</th>
                  <th>Линия</th>
                  <th>Доступ</th>
                </tr>
              </thead>
              <tbody>
                {team.data.map((member) => (
                  <tr key={member.membership_id}>
                    <td>
                      <strong>{member.display_name}</strong>
                      <div className="table-secondary">{member.email}</div>
                    </td>
                    <td>{roleLabel[member.role] ?? member.role}</td>
                    <td>
                      {member.operator_status
                        ? operatorStatusLabel[member.operator_status]
                        : "—"}
                      {member.extension ? ` · ${member.extension}` : ""}
                    </td>
                    <td>
                      <StatusBadge
                        tone={member.is_active ? "success" : "danger"}
                      >
                        {member.is_active ? "Активен" : "Заблокирован"}
                      </StatusBadge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="compact-note">
          <StatusBadge tone="warning">В разработке</StatusBadge>
          <span>Приглашения и изменение ролей</span>
        </div>
      </section>
    </>
  );
}
