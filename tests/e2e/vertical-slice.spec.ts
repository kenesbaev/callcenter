import { expect, test } from "@playwright/test";

test("регистрация → AI-оператор → база знаний → симуляция → итог", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const slug = `e2e-${suffix}`;
  const operatorName = `Поддержка E2E ${suffix}`;

  await page.goto("/register");
  await page.getByLabel("Название компании").fill(`K-Line E2E ${suffix}`);
  await page.getByLabel("Адрес компании").fill(slug);
  await page.getByLabel("Ваше имя").fill("Владелец E2E");
  await page.getByLabel("Рабочая почта").fill(`owner+${suffix}@example.com`);
  await page.getByLabel("Пароль").fill("SecureE2ePass123!");
  await page.getByRole("button", { name: "Создать компанию" }).click();
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible();

  await page.getByRole("link", { name: "AI-операторы" }).click();
  await page.getByLabel("Имя").fill(operatorName);
  await page.getByLabel("Роль").fill("Отвечает по проверенной базе знаний");
  await page.getByRole("button", { name: "Создать черновик" }).click();
  const operator = page.locator("article").filter({ hasText: operatorName });
  await expect(operator).toBeVisible();
  await operator.getByRole("button", { name: "Опубликовать" }).click();
  await expect(operator.getByText("Готов к симулятору")).toBeVisible();

  await page.getByRole("link", { name: "База знаний" }).click();
  await page.getByLabel("Название").fill("График поддержки E2E");
  await page
    .getByLabel("Проверенная информация")
    .fill(
      "Служба поддержки работает с понедельника по пятницу с девяти до восемнадцати.",
    );
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByText("График поддержки E2E")).toBeVisible();

  await page.getByRole("link", { name: "Симулятор" }).click();
  await page
    .getByLabel("Опубликованный оператор")
    .selectOption({ label: `${operatorName} · v1` });
  await page.getByLabel("Имя тестового клиента").fill("Тестовый клиент E2E");
  await page.getByRole("button", { name: "Начать" }).click();
  await expect(page.getByText(/я виртуальный помощник/i)).toBeVisible();
  await page
    .getByLabel("Сообщение клиента")
    .fill("Когда работает служба поддержки?");
  await page.getByRole("button", { name: "Отправить" }).click();
  await expect(page.getByText(/понедельника по пятницу/i)).toBeVisible();
  await page.getByRole("button", { name: "Завершить и создать итог" }).click();
  await expect(page.getByText("Итог разговора")).toBeVisible();

  await page.getByRole("link", { name: "Разговоры" }).click();
  const callLink = page.locator("tbody a").first();
  await expect(callLink).toBeVisible();
  await callLink.click();
  await expect(page.getByRole("heading", { name: "Итог" })).toBeVisible();
  await expect(page.getByText("development_deterministic")).toBeVisible();
  await expect(page.getByText(/Когда работает служба поддержки/)).toBeVisible();
});

test("пять разделов ДАЛЕЕ открываются как реальные страницы", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  await page.goto("/register");
  await page
    .getByLabel("Название компании")
    .fill(`K-Line Navigation ${suffix}`);
  await page.getByLabel("Адрес компании").fill(`nav-${suffix}`);
  await page.getByLabel("Ваше имя").fill("Владелец навигации");
  await page
    .getByLabel("Рабочая почта")
    .fill(`navigation+${suffix}@example.com`);
  await page.getByLabel("Пароль").fill("SecureNavigation123!");
  await page.getByRole("button", { name: "Создать компанию" }).click();

  for (const [link, heading] of [
    ["Активные звонки", "Активные звонки"],
    ["Команда", "Команда"],
    ["Интеграции", "Интеграции"],
    ["Аналитика", "Аналитика"],
    ["Настройки", "Настройки"],
  ] as const) {
    await page.getByRole("link", { name: link }).click();
    await expect(
      page.getByRole("heading", { name: heading, level: 1 }),
    ).toBeVisible();
  }
  await expect(page.getByText("Скоро")).toHaveCount(0);
});

test("владелец создаёт, редактирует и архивирует проект", async ({ page }) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  await page.goto("/register");
  await page.getByLabel("Название компании").fill(`K-Line Projects ${suffix}`);
  await page.getByLabel("Адрес компании").fill(`projects-${suffix}`);
  await page.getByLabel("Ваше имя").fill("Владелец проектов");
  await page.getByLabel("Рабочая почта").fill(`projects+${suffix}@example.com`);
  await page.getByLabel("Пароль").fill("SecureProjects123!");
  await page.getByRole("button", { name: "Создать компанию" }).click();
  await expect(page).toHaveURL(/\/app$/);

  await page.getByRole("link", { name: "Проекты" }).click();
  await expect(
    page.getByRole("heading", { name: "Проекты", level: 1, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Новый проект" }).click();
  await page.getByLabel("Название").fill(`Retention ${suffix}`);
  await page
    .getByLabel("Описание")
    .fill("Проект для проверки полной конфигурации");
  await page.getByRole("tab", { name: "Лимиты и правила" }).click();
  await page.getByLabel("Одновременных звонков").fill("3");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByText("Сохранено")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: `Retention ${suffix}`, exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "В архив", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "В архив", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("Архив").first()).toBeVisible();
});

test("владелец создаёт клиента с телефоном и e-mail, архивирует и восстанавливает", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const customerName = `Клиент E2E ${suffix}`;

  await page.goto("/register");
  await page.getByLabel("Название компании").fill(`K-Line Customers ${suffix}`);
  await page.getByLabel("Адрес компании").fill(`customers-${suffix}`);
  await page.getByLabel("Ваше имя").fill("Владелец клиентской базы");
  await page
    .getByLabel("Рабочая почта")
    .fill(`customers+${suffix}@example.com`);
  await page.getByLabel("Пароль").fill("SecureCustomers123!");
  await page.getByRole("button", { name: "Создать компанию" }).click();
  await expect(page).toHaveURL(/\/app$/);

  await page.getByRole("link", { name: "Клиенты" }).click();
  await expect(
    page.getByRole("heading", { name: "Клиенты", level: 1 }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Новый клиент" }).click();
  await page.getByLabel("ФИО").fill(customerName);
  await page.getByLabel("Телефон 1").fill("+998 90 123 45 67");
  await page.getByRole("button", { name: "E-mail", exact: true }).click();
  await page.getByLabel("E-mail 2").fill(`client+${suffix}@example.com`);
  await page.getByLabel("Теги через запятую").fill("VIP, E2E");
  await page.getByRole("button", { name: "Сохранить клиента" }).click();

  await expect(page.getByText("Клиент создан")).toBeVisible();
  await expect(page.getByText(customerName).first()).toBeVisible();
  await expect(page.getByText("+998901234567").first()).toBeVisible();

  await page.getByRole("button", { name: "Архивировать" }).click();
  await expect(page.getByText("Клиент архивирован")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Восстановить" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Восстановить" }).click();
  await expect(page.getByText("Клиент восстановлен")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Архивировать" }),
  ).toBeVisible();
});

test("оператор проходит полный Mock Dialer и сохраняет результат", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const customerName = `Dialer E2E ${suffix}`;

  await page.goto("/register");
  await page.getByLabel("Название компании").fill(`K-Line Dialer ${suffix}`);
  await page.getByLabel("Адрес компании").fill(`dialer-${suffix}`);
  await page.getByLabel("Ваше имя").fill("Оператор Dialer");
  await page.getByLabel("Рабочая почта").fill(`dialer+${suffix}@example.com`);
  await page.getByLabel("Пароль").fill("SecureDialer123!");
  await page.getByRole("button", { name: "Создать компанию" }).click();

  await page.getByRole("link", { name: "Клиенты" }).click();
  await page.getByRole("button", { name: "Новый клиент" }).click();
  await page.getByLabel("ФИО").fill(customerName);
  await page.getByLabel("Телефон 1").fill("+998 93 765 43 21");
  await page.getByLabel("Город").fill("Ташкент");
  await page.getByRole("button", { name: "Сохранить клиента" }).click();
  await expect(page.getByText("Клиент создан")).toBeVisible();

  await page.getByRole("link", { name: "Диалер" }).click();
  await page.getByRole("button", { name: "Следующий клиент" }).click();
  await expect(page.getByRole("heading", { name: customerName })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Сценарий" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "История" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Задачи" })).toBeVisible();

  await page.getByRole("button", { name: "Позвонить" }).click();
  await page.getByRole("button", { name: "Имитировать ответ" }).click();
  await expect(page.getByRole("button", { name: "Удержать" })).toBeVisible();
  await page.getByRole("button", { name: "Удержать" }).click();
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByRole("button", { name: "Завершить звонок" }).click();

  await page.getByText("Успешно", { exact: true }).click();
  await page.getByRole("button", { name: /Сохранить и следующий/ }).click();
  await expect(
    page.getByText("Результат сохранён. Очередь завершена."),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Ожидание клиента" }),
  ).toBeVisible();
});
