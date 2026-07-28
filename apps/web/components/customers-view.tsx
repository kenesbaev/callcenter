"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest } from "@/lib/api";
import type { Customer, Page } from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

const customerSchema = z.object({
  display_name: z.string().trim().min(2, "Введите имя клиента").max(160),
  phone: z
    .string()
    .trim()
    .regex(/^\+[1-9][0-9\s()-]{7,20}$/, "Используйте формат +998901234567"),
  email: z.union([z.literal(""), z.email("Некорректный e-mail")]),
  preferred_language: z.enum(["ru", "uz", "en"]),
  external_reference: z.string().trim().max(160),
});

type CustomerForm = z.infer<typeof customerSchema>;

const statusLabels: Record<string, string> = {
  new: "Новый",
  assigned: "Назначен",
  callback: "Перезвон",
  completed: "Обработан",
  do_not_call: "Не звонить",
};

function primaryPhone(customer: Customer) {
  return customer.contacts.find(
    (contact) => contact.kind === "phone" && contact.is_primary,
  )?.value;
}

export function CustomersView() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const form = useForm<CustomerForm>({
    resolver: zodResolver(customerSchema),
    defaultValues: {
      display_name: "",
      phone: "+998",
      email: "",
      preferred_language: "ru",
      external_reference: "",
    },
  });
  const customers = useQuery({
    queryKey: ["customers", search],
    queryFn: () =>
      apiRequest<Page<Customer>>(
        `/customers?search=${encodeURIComponent(search)}&limit=100`,
      ),
  });
  const createCustomer = useMutation({
    mutationFn: (value: CustomerForm) =>
      apiRequest<Customer>("/customers", {
        method: "POST",
        body: JSON.stringify({
          ...value,
          email: value.email || null,
          external_reference: value.external_reference || null,
        }),
      }),
    onSuccess: async () => {
      setSubmitError("");
      setFormOpen(false);
      form.reset();
      await queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (error) =>
      setSubmitError(
        error instanceof ApiClientError
          ? error.message
          : "Не удалось сохранить клиента",
      ),
  });

  return (
    <>
      <div className="page-heading row-between">
        <div>
          <h1>Клиенты</h1>
          <p>Единая очередь контактов для операторов</p>
        </div>
        <Button onClick={() => setFormOpen((value) => !value)}>
          <UserPlus size={16} />
          Новый клиент
        </Button>
      </div>
      {formOpen && (
        <section className="panel crm-create-panel">
          <div>
            <h2>Карточка клиента</h2>
            <p className="panel-subtitle">Телефон хранится в формате E.164</p>
          </div>
          <form
            className="crm-inline-form"
            onSubmit={form.handleSubmit((value) =>
              createCustomer.mutate(value),
            )}
          >
            <div className="field">
              <label htmlFor="customer-name">Имя</label>
              <input id="customer-name" {...form.register("display_name")} />
              {form.formState.errors.display_name && (
                <small className="field-error">
                  {form.formState.errors.display_name.message}
                </small>
              )}
            </div>
            <div className="field">
              <label htmlFor="customer-phone">Телефон</label>
              <input id="customer-phone" {...form.register("phone")} />
              {form.formState.errors.phone && (
                <small className="field-error">
                  {form.formState.errors.phone.message}
                </small>
              )}
            </div>
            <div className="field">
              <label htmlFor="customer-email">E-mail</label>
              <input id="customer-email" {...form.register("email")} />
            </div>
            <div className="field">
              <label htmlFor="customer-language">Язык</label>
              <select
                id="customer-language"
                {...form.register("preferred_language")}
              >
                <option value="ru">Русский</option>
                <option value="uz">O‘zbekcha</option>
                <option value="en">English</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="customer-reference">Внешний ID</label>
              <input
                id="customer-reference"
                {...form.register("external_reference")}
              />
            </div>
            <div className="crm-form-actions">
              {submitError && <span className="form-error">{submitError}</span>}
              <Button disabled={createCustomer.isPending} type="submit">
                {createCustomer.isPending ? "Сохраняем…" : "Сохранить"}
              </Button>
            </div>
          </form>
        </section>
      )}
      <section className="panel">
        <div className="table-toolbar">
          <label className="search-control">
            <Search aria-hidden="true" size={16} />
            <input
              aria-label="Поиск клиентов"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Имя, телефон или внешний ID"
              value={search}
            />
          </label>
          <StatusBadge>{customers.data?.total ?? 0} клиентов</StatusBadge>
        </div>
        {customers.isPending && <SectionSkeleton />}
        {customers.isError && (
          <QueryError
            error={customers.error}
            retry={() => void customers.refetch()}
            title="Не удалось загрузить клиентов"
          />
        )}
        {customers.data && customers.data.items.length === 0 && (
          <div className="empty-state">
            <div>
              <Users size={28} />
              <h3>Клиентов пока нет</h3>
              <p>Добавьте первую карточку, чтобы запустить очередь диалера.</p>
            </div>
          </div>
        )}
        {customers.data && customers.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Клиент</th>
                  <th>Телефон</th>
                  <th>Язык</th>
                  <th>Статус</th>
                  <th>Следующий контакт</th>
                </tr>
              </thead>
              <tbody>
                {customers.data.items.map((customer) => (
                  <tr key={customer.id}>
                    <td>
                      <strong>{customer.display_name ?? "Без имени"}</strong>
                      <div className="table-secondary">
                        {customer.external_reference ?? customer.id.slice(0, 8)}
                      </div>
                    </td>
                    <td>{primaryPhone(customer) ?? "—"}</td>
                    <td>{customer.preferred_language?.toUpperCase() ?? "—"}</td>
                    <td>
                      <StatusBadge
                        tone={
                          customer.status === "do_not_call"
                            ? "danger"
                            : customer.status === "callback"
                              ? "warning"
                              : customer.status === "completed"
                                ? "success"
                                : "primary"
                        }
                      >
                        {statusLabels[customer.status] ?? customer.status}
                      </StatusBadge>
                    </td>
                    <td>
                      {customer.next_call_at
                        ? new Date(customer.next_call_at).toLocaleString(
                            "ru-RU",
                          )
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
