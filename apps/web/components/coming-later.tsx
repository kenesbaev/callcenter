import { Construction } from "lucide-react";
import { StatusBadge } from "@teamora/ui";

const labels: Record<string, string> = {
  live: "Активные звонки",
  "ai-operators/new": "Мастер AI-оператора",
  "call-flows": "Сценарии звонков",
  "phone-numbers": "Телефонные номера",
  "sip-trunks": "SIP-транки",
  operators: "Операторы",
  queues: "Очереди",
  customers: "Клиенты",
  integrations: "Интеграции",
  analytics: "Расширенная аналитика",
  usage: "Использование",
  billing: "Оплата",
  settings: "Настройки",
  audit: "Журнал аудита",
};

export function ComingLater({ path }: { path: string }) {
  const title =
    labels[path] ??
    (path.startsWith("ai-operators/") ? "Редактор AI-оператора" : "Функция");
  return (
    <div className="coming-later">
      <div>
        <StatusBadge tone="warning">В разработке</StatusBadge>
        <Construction aria-hidden="true" size={34} />
        <h1>{title}</h1>
        <p>Backend для этого раздела ещё не реализован.</p>
      </div>
    </div>
  );
}
