import { expect, test } from "@playwright/test";

test.setTimeout(90_000);

test("realtime updates calls, team, analytics, tasks, and replays after reconnect", async ({
  page,
  browser,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const slug = `realtime-${suffix}`;
  const operatorEmail = `realtime-operator+${suffix}@example.com`;
  const operatorPassword = "SecureRealtime123!";
  const customerName = `Realtime Client ${suffix}`;
  const taskTitle = `Reconnect task ${suffix}`;

  const registration = await page.request.post("/api/v1/auth/register", {
    data: {
      company_name: `K-Line Realtime ${suffix}`,
      company_slug: slug,
      display_name: "Realtime Owner",
      email: `realtime-owner+${suffix}@example.com`,
      password: "SecureRealtimeOwner123!",
    },
  });
  expect(registration.ok()).toBeTruthy();
  const ownerAuth = (await registration.json()) as { csrf_token: string };
  const projects = await page.request.get("/api/v1/projects");
  const projectId = (
    (await projects.json()) as { items: Array<{ id: string }> }
  ).items[0].id;
  const customerResponse = await page.request.post("/api/v1/customers", {
    headers: { "X-CSRF-Token": ownerAuth.csrf_token },
    data: {
      project_id: projectId,
      display_name: customerName,
      phone: "+998901112233",
      preferred_language: "ru",
    },
  });
  expect(customerResponse.ok()).toBeTruthy();
  const customerId = ((await customerResponse.json()) as { id: string }).id;
  const invitationResponse = await page.request.post(
    "/api/v1/team/invitations",
    {
      headers: {
        "X-CSRF-Token": ownerAuth.csrf_token,
        "Idempotency-Key": `invite-${suffix}`,
      },
      data: {
        email: operatorEmail,
        role: "human_operator",
        project_ids: [projectId],
      },
    },
  );
  expect(invitationResponse.ok()).toBeTruthy();
  const invitation = (await invitationResponse.json()) as {
    acceptance_token: string;
  };

  const operatorContext = await browser.newContext();
  const operatorPage = await operatorContext.newPage();
  try {
    const accepted = await operatorContext.request.post(
      "/api/v1/team/invitations/accept",
      {
        data: {
          tenant_slug: slug,
          token: invitation.acceptance_token,
          display_name: "Realtime Operator",
          password: operatorPassword,
        },
      },
    );
    expect(accepted.ok()).toBeTruthy();
    const login = await operatorContext.request.post("/api/v1/auth/login", {
      data: {
        company_slug: slug,
        email: operatorEmail,
        password: operatorPassword,
      },
    });
    expect(login.ok()).toBeTruthy();
    const operatorAuth = (await login.json()) as {
      csrf_token: string;
      user: { id: string };
    };
    await operatorContext.request.post("/api/v1/team/presence/heartbeat", {
      headers: { "X-CSRF-Token": operatorAuth.csrf_token },
      data: { session_key: `realtime-${suffix}` },
    });
    await operatorContext.request.put("/api/v1/team/presence/status", {
      headers: { "X-CSRF-Token": operatorAuth.csrf_token },
      data: { status: "available", session_key: `realtime-${suffix}` },
    });

    await page.goto("/app/live");
    await expect(page.getByTestId("realtime-status")).toContainText(
      "В реальном времени",
      { timeout: 15_000 },
    );
    const teamPage = await page.context().newPage();
    await teamPage.goto("/app/team");
    await expect(teamPage.getByTestId("realtime-status")).toContainText(
      "В реальном времени",
      { timeout: 15_000 },
    );
    const operatorRow = teamPage.locator("tbody tr").filter({
      hasText: operatorEmail,
    });
    await expect(operatorRow).toContainText("Доступен");

    await operatorPage.goto("/app/dialer");
    await expect(operatorPage.getByTestId("realtime-status")).toContainText(
      "В реальном времени",
      { timeout: 15_000 },
    );
    await operatorPage
      .getByRole("button", { name: "Следующий клиент" })
      .click();
    await expect(
      operatorPage.getByRole("heading", { name: customerName }),
    ).toBeVisible();
    await operatorPage.getByRole("button", { name: "Позвонить" }).click();

    await expect(page.locator("tbody tr")).toHaveCount(1, { timeout: 15_000 });
    await operatorPage
      .getByRole("button", { name: "Имитировать ответ" })
      .click();
    await expect(page.locator("tbody tr")).toContainText("В разговоре", {
      timeout: 15_000,
    });
    await expect(operatorRow).toContainText("Разговаривает", {
      timeout: 15_000,
    });
    await operatorPage.getByRole("button", { name: "Удержать" }).click();
    await expect(
      operatorPage.getByRole("button", { name: "Продолжить" }),
    ).toBeVisible();
    await operatorPage.getByRole("button", { name: "Продолжить" }).click();
    await operatorPage
      .getByRole("button", { name: "Завершить звонок" })
      .click();
    await operatorPage.getByText("Успешно", { exact: true }).click();
    await operatorPage
      .getByRole("button", { name: /Сохранить и следующий/ })
      .click();
    await expect(page.locator("tbody tr")).toHaveCount(0, { timeout: 15_000 });
    await expect(operatorRow).toContainText("Доступен", { timeout: 15_000 });

    await page.goto("/app/overview");
    await expect(
      page
        .locator("article.analytics-kpi")
        .filter({ hasText: "Начатые попытки" })
        .getByText("1", { exact: true }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole("heading", { name: "Последние звонки" }),
    ).toBeVisible();

    await page.goto("/app/tasks");
    await expect(page.getByTestId("realtime-status")).toContainText(
      "В реальном времени",
      { timeout: 15_000 },
    );
    await page.context().setOffline(true);
    await expect(page.getByTestId("realtime-status")).toContainText(
      /Офлайн|Переподключение/,
      { timeout: 10_000 },
    );
    const createdTask = await operatorContext.request.post("/api/v1/tasks", {
      headers: {
        "X-CSRF-Token": operatorAuth.csrf_token,
        "Idempotency-Key": `task-${suffix}`,
      },
      data: {
        project_id: projectId,
        customer_id: customerId,
        task_type: "manual",
        title: taskTitle,
        description: "Created while the owner realtime connection is offline",
        priority: "normal",
        due_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      },
    });
    expect(createdTask.ok()).toBeTruthy();
    await page.context().setOffline(false);
    await expect(page.getByTestId("realtime-status")).toContainText(
      "В реальном времени",
      { timeout: 20_000 },
    );
    await expect(page.getByText(taskTitle)).toBeVisible({ timeout: 20_000 });
    await page.reload();
    await expect(page.getByText(taskTitle)).toBeVisible();
    await teamPage.close();
  } finally {
    await operatorContext.close();
  }
});
