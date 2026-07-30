import { expect, test } from "@playwright/test";

test("владелец создаёт, начинает и завершает общую задачу", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const registration = await page.request.post("/api/v1/auth/register", {
    data: {
      company_name: `K-Line Tasks ${suffix}`,
      company_slug: `tasks-${suffix}`,
      display_name: "Владелец задач",
      email: `tasks+${suffix}@example.com`,
      password: "SecureTasks123!",
    },
  });
  expect(registration.ok()).toBeTruthy();
  const csrfToken = ((await registration.json()) as { csrf_token: string })
    .csrf_token;

  const projects = await page.request.get("/api/v1/projects");
  expect(projects.ok()).toBeTruthy();
  const projectId = (await projects.json()).items[0].id as string;
  const customer = await page.request.post("/api/v1/customers", {
    headers: { "X-CSRF-Token": csrfToken },
    data: {
      project_id: projectId,
      display_name: "Клиент задач",
      phone: "+998901230099",
      preferred_language: "ru",
    },
  });
  expect(customer.ok()).toBeTruthy();
  const customerId = ((await customer.json()) as { id: string }).id;

  await page.goto("/app/tasks");
  await expect(page.getByRole("heading", { name: "Задачи" })).toBeVisible();
  await page.getByRole("button", { name: "Создать задачу" }).click();
  const form = page.getByRole("dialog", { name: "Создание задачи" });
  await form.getByLabel("Клиент").selectOption(customerId);
  await form.getByLabel("Тип").selectOption("manual");
  await form.getByLabel("Приоритет").selectOption("high");
  await form.getByLabel("Название").fill("Проверить документы клиента");
  await form.getByLabel("Комментарий").fill("Создано через рабочий интерфейс");
  await form.getByRole("button", { name: "Сохранить", exact: true }).click();

  await expect(page.getByText("Задача сохранена")).toBeVisible();
  const row = page
    .locator(".task-row")
    .filter({ hasText: "Проверить документы клиента" });
  await expect(row).toBeVisible();
  const drawer = page.getByRole("dialog", { name: "Карточка задачи" });
  await drawer.getByRole("button", { name: "Начать" }).click();
  await expect(drawer.getByText("В работе")).toBeVisible();
  await drawer.getByRole("button", { name: "Завершить" }).click();
  await expect(drawer.getByText("Завершена", { exact: true })).toBeVisible();
});
