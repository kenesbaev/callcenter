import type { BackgroundImportStatus } from "@/lib/types";

export const customerImportFields = [
  ["display_name", "ФИО"],
  ["phone", "Основной телефон"],
  ["alternate_phone", "Дополнительный телефон"],
  ["email", "Основной e-mail"],
  ["external_reference", "Внешний ID"],
  ["preferred_language", "Язык"],
  ["city", "Город"],
  ["region", "Регион"],
  ["address", "Адрес"],
  ["job_title", "Должность"],
  ["organization", "Организация"],
  ["tags", "Теги"],
  ["description", "Описание"],
  ["source", "Источник"],
  ["next_contact_at", "Следующий контакт"],
] as const;

export const terminalImportStatuses = new Set<BackgroundImportStatus>([
  "completed",
  "failed",
  "expired",
  "cancelled",
]);
