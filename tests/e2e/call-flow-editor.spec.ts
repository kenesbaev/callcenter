import { expect, test } from "@playwright/test";

test("владелец создаёт, проверяет, просматривает и публикует сценарий", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const registration = await page.request.post("/api/v1/auth/register", {
    data: {
      company_name: `K-Line Flow ${suffix}`,
      company_slug: `flow-${suffix}`,
      display_name: "Владелец сценария",
      email: `flow+${suffix}@example.com`,
      password: "SecureFlow123!",
    },
  });
  expect(registration.ok()).toBeTruthy();

  await page.goto("/app/projects");
  await expect(page.locator(".page-heading h1")).toBeVisible();
  await page.locator(".project-list-row").first().click();
  await page.getByRole("link", { name: "Сценарий" }).click();
  await expect(page.locator(".call-flow-page")).toBeVisible();

  await page.getByRole("button", { name: "Создать сценарий" }).click();
  await page.getByLabel("Название").fill("Продажа банковской карты");
  await page.getByLabel("Языковые коды").fill("ru");
  await page.getByRole("button", { name: "Создать", exact: true }).click();
  await expect(page.getByText("Сценарий создан")).toBeVisible();

  await page.getByRole("button", { name: "Проверить" }).click();
  await expect(page.getByText("Граф готов к публикации")).toBeVisible();
  await page.getByRole("button", { name: "Запустить preview" }).click();
  await page.getByRole("button", { name: /Продолжить/ }).click();
  await expect(page.getByText("Сценарий завершён")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Опубликовать" }).click();
  await expect(page.getByText("Версия 1 опубликована")).toBeVisible();
  await expect(page.getByText(/Опубликован · v1/)).toBeVisible();
});
