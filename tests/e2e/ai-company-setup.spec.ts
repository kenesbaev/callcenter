import { expect, test } from "@playwright/test";

test.setTimeout(60_000);

test("владелец настраивает компанию для AI и быстро выбирает роль сотрудника", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const companyName = `K-Line AI Setup ${suffix}`;

  await page.goto("/register");
  await page.getByLabel("Название компании").fill(companyName);
  await page.getByLabel("Адрес компании").fill(`ai-setup-${suffix}`);
  await page.getByLabel("Ваше имя").fill("Владелец AI Setup");
  await page
    .getByLabel("Рабочая почта")
    .fill(`ai-setup-owner+${suffix}@example.com`);
  await page.getByLabel("Пароль").fill("SecureAiSetup123!");
  await page.getByRole("button", { name: "Создать компанию" }).click();
  await expect(page).toHaveURL(/\/app$/, { timeout: 20_000 });

  await page.goto("/app/ai-setup");
  await expect(
    page.getByRole("heading", { name: "Настройте AI под компанию" }),
  ).toBeVisible();
  await expect(page.getByLabel("Как называется компания?")).toHaveValue(
    companyName,
  );
  await expect(page.getByText("1 из 20")).toBeVisible();

  await page
    .getByLabel("В какой сфере работает компания?")
    .selectOption("b2b_services");
  await page
    .getByLabel("Чем занимается компания?")
    .fill("Автоматизирует обработку входящих обращений для B2B-компаний.");
  await page
    .getByLabel("Какие товары или услуги вы предлагаете?")
    .fill("AI-оператор, аналитика звонков и интеграция с CRM.");
  await page.getByRole("button", { name: /Следующий шаг/ }).click();

  await expect(
    page.getByRole("heading", { name: "Задачи", level: 2 }),
  ).toBeVisible();
  await expect(page.getByText("4/20 ответов")).toBeVisible();

  await page.goto("/app/team");
  await expect(
    page.getByText("Super admin K-Line", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Владелец и администратор", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Оператор", { exact: true }).first(),
  ).toBeVisible();

  await page.getByRole("button", { name: "Добавить администратора" }).click();
  const dialog = page.getByRole("dialog", { name: "Приглашение сотрудника" });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("radio", { name: /^Администратор / }),
  ).toHaveAttribute("aria-checked", "true");

  await dialog.getByRole("button", { name: "Закрыть приглашение" }).click();
  await page.getByRole("button", { name: "Добавить оператора" }).click();
  await expect(
    page
      .getByRole("dialog", { name: "Приглашение сотрудника" })
      .getByRole("radio", { name: /^Оператор/ }),
  ).toHaveAttribute("aria-checked", "true");
});
