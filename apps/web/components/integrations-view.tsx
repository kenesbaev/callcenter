"use client";

import { useQuery } from "@tanstack/react-query";
import { PlugZap } from "lucide-react";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { Integration } from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

const statusLabel = {
  unavailable: "Недоступно",
  development: "Разработка",
  configured: "Настроено",
  verified: "Проверено",
};

export function IntegrationsView() {
  const integrations = useQuery({
    queryKey: ["integrations"],
    queryFn: () => apiRequest<Integration[]>("/integrations"),
  });
  if (integrations.isPending) return <SectionSkeleton />;
  if (integrations.isError)
    return (
      <QueryError
        error={integrations.error}
        retry={() => void integrations.refetch()}
        title="Не удалось загрузить интеграции"
      />
    );
  return (
    <>
      <div className="page-heading">
        <h1>Интеграции</h1>
      </div>
      <div className="integration-grid">
        {integrations.data.map((integration) => (
          <article
            className="panel integration-card"
            key={integration.provider}
          >
            <div className="integration-mark">
              <PlugZap aria-hidden="true" size={20} />
            </div>
            <div className="row-between">
              <h2>{integration.display_name}</h2>
              <StatusBadge
                tone={integration.status === "verified" ? "success" : "warning"}
              >
                {statusLabel[integration.status]}
              </StatusBadge>
            </div>
            <div className="capability-list">
              {integration.capabilities.map((capability) => (
                <span key={capability}>{capability}</span>
              ))}
            </div>
            <p>{integration.note}</p>
          </article>
        ))}
      </div>
    </>
  );
}
