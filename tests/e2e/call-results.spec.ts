import { expect, test } from "@playwright/test";

test("владелец создаёт и архивирует проектный результат звонка", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const registration = await page.request.post("/api/v1/auth/register", {
    data: {
      company_name: `K-Line Results ${suffix}`,
      company_slug: `results-${suffix}`,
      display_name: "Владелец каталога",
      email: `results+${suffix}@example.com`,
      password: "SecureResults123!",
    },
  });
  expect(registration.ok()).toBeTruthy();

  await page.goto("/app/projects");
  await page.locator(".project-list-row").first().click();
  await page.getByRole("link", { name: "Результаты" }).click();
  await expect(page.locator(".call-results-page")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Успешные", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Промежуточные" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Недозвон" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Неуспешные" })).toBeVisible();

  await page.getByRole("button", { name: "Новый результат" }).click();
  await page.getByLabel("Название", { exact: true }).fill("Клиент согласен");
  await page
    .getByLabel("Системный код")
    .fill(`approved_${suffix.replaceAll("-", "_")}`);
  await page.getByLabel("Название · UZ").fill("Mijoz rozi");
  await page.getByLabel("Название · EN").fill("Customer approved");
  await page.getByLabel("Название · KAA").fill("Klient razı");
  await page.getByLabel("Категория").selectOption("successful");
  await page.getByLabel("Учитывать как успешный").check();
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByText("Каталог результатов сохранён.")).toBeVisible();
  await expect(page.getByText("Клиент согласен")).toBeVisible();

  const item = page.locator(".call-result-item").filter({
    hasText: "Клиент согласен",
  });
  page.once("dialog", (dialog) => dialog.accept());
  await item.getByRole("button", { name: "В архив" }).click();
  await expect(page.getByText("Результат перемещён в архив.")).toBeVisible();
  await expect(item.getByText("Архив")).toBeVisible();
});
