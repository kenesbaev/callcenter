import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";

type BackgroundImport = {
  id: string;
  job_id: string | null;
  status:
    | "uploading"
    | "previewing"
    | "preview_ready"
    | "queued"
    | "staging"
    | "ready_to_finalize"
    | "finalizing"
    | "completed"
    | "failed"
    | "cancel_requested"
    | "cancelled";
  state_version: number;
  total_rows: number;
  created: number;
  updated: number;
  skipped: number;
  error_count: number;
  report_available: boolean;
};

type Registration = {
  csrf_token: string;
};

type PageResponse<T> = {
  items: T[];
  total: number;
};

async function registerTenant(
  request: APIRequestContext,
  suffix: string,
): Promise<{ csrf: string; projectId: string; slug: string }> {
  const slug = `operations-${suffix}`;
  const registration = await request.post("/api/v1/auth/register", {
    data: {
      company_name: `K-Line Operations ${suffix}`,
      company_slug: slug,
      display_name: "Operations Owner",
      email: `operations-owner+${suffix}@example.com`,
      password: "SecureOperationsOwner123!",
    },
  });
  expect(registration.ok()).toBeTruthy();
  const { csrf_token: csrf } = (await registration.json()) as Registration;
  const projects = await request.get("/api/v1/projects");
  expect(projects.ok()).toBeTruthy();
  const projectId = ((await projects.json()) as PageResponse<{ id: string }>)
    .items[0]!.id;
  return { csrf, projectId, slug };
}

function largeCsv(suffix: string, validRows = 650): Buffer {
  const rows = [
    "display_name,phone,email,external_reference,preferred_language",
  ];
  for (let index = 0; index < validRows; index += 1) {
    const serial = String(index).padStart(7, "0");
    rows.push(
      [
        `Stage14 ${suffix} Customer ${index}`,
        `+99890${serial}`,
        `stage14-${suffix}-${index}@example.com`,
        `stage14-${suffix}-${index}`,
        index % 2 === 0 ? "ru" : "kaa",
      ].join(","),
    );
  }
  // One deliberately malformed row proves that the private error report is
  // generated without exposing a partially-created customer.
  rows.push(`,+998991234567,,stage14-${suffix}-invalid,ru`);
  return Buffer.from(`${rows.join("\n")}\n`, "utf8");
}

async function customerCount(
  request: APIRequestContext,
  projectId: string,
  search: string,
): Promise<number> {
  const params = new URLSearchParams({
    project_id: projectId,
    search,
    limit: "1",
  });
  const response = await request.get(`/api/v1/customers?${params}`);
  expect(response.ok()).toBeTruthy();
  return ((await response.json()) as PageResponse<unknown>).total;
}

async function importStatus(
  request: APIRequestContext,
  importId: string,
): Promise<BackgroundImport> {
  const response = await request.get(
    `/api/v1/customers/import/background/${importId}/status`,
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as BackgroundImport;
}

async function acceptOperator(
  context: BrowserContext,
  options: {
    slug: string;
    email: string;
    password: string;
    acceptanceToken: string;
  },
): Promise<string> {
  const accepted = await context.request.post(
    "/api/v1/team/invitations/accept",
    {
      data: {
        tenant_slug: options.slug,
        token: options.acceptanceToken,
        display_name: "Stage 14 Operator",
        password: options.password,
      },
    },
  );
  expect(accepted.ok()).toBeTruthy();
  const login = await context.request.post("/api/v1/auth/login", {
    data: {
      company_slug: options.slug,
      email: options.email,
      password: options.password,
    },
  });
  expect(login.ok()).toBeTruthy();
  return ((await login.json()) as Registration).csrf_token;
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1,
    ),
  ).toBe(true);
}

test.setTimeout(180_000);

test("background import, jobs, storage, retention, and legal holds stay atomic and tenant-safe", async ({
  page,
  browser,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const { csrf, projectId, slug } = await registerTenant(page.request, suffix);
  const fileName = `stage14-large-${suffix}.csv`;
  const searchPrefix = `Stage14 ${suffix}`;

  await page.goto("/app/customers");
  await expect(page.getByTestId("realtime-status")).toContainText(
    "В реальном времени",
    { timeout: 15_000 },
  );
  await page.getByRole("button", { name: "Импорт" }).click();
  await page.getByRole("tab", { name: "Большой фоновый импорт" }).click();
  await page.getByLabel("Файл большого импорта").setInputFiles({
    name: fileName,
    mimeType: "text/csv",
    buffer: largeCsv(suffix),
  });
  const uploadResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes("/api/v1/customers/import/background/preview"),
  );
  await page
    .getByRole("button", { name: "Создать background preview" })
    .click();
  const uploaded = await uploadResponse;
  expect(uploaded.status()).toBe(202);
  const importRecord = (await uploaded.json()) as BackgroundImport;

  // Preview never mutates the customer database.
  expect(await customerCount(page.request, projectId, searchPrefix)).toBe(0);
  await expect
    .poll(
      async () => (await importStatus(page.request, importRecord.id)).status,
      {
        timeout: 45_000,
      },
    )
    .toBe("preview_ready");
  await expect(
    page.getByText("Preview готов", { exact: true }).first(),
  ).toBeVisible({ timeout: 15_000 });

  const mapped = await page.request.patch(
    `/api/v1/customers/import/${importRecord.id}`,
    {
      headers: { "X-CSRF-Token": csrf },
      data: {
        sheet_name: "CSV",
        update_rule: "skip",
        mapping: {
          display_name: "display_name",
          phone: "phone",
          email: "email",
          external_reference: "external_reference",
          preferred_language: "preferred_language",
        },
      },
    },
  );
  expect(mapped.ok()).toBeTruthy();

  // Reload recovery uses the durable import record, not component-local state.
  await page.reload();
  await page.getByRole("button", { name: "Импорт" }).click();
  await page.getByRole("tab", { name: "Большой фоновый импорт" }).click();
  await expect(page.getByText(fileName).first()).toBeVisible();
  await expect(
    page.getByText("Preview готов", { exact: true }).first(),
  ).toBeVisible();
  expect(await customerCount(page.request, projectId, searchPrefix)).toBe(0);

  const commitResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response
        .url()
        .includes(
          `/api/v1/customers/import/background/${importRecord.id}/background-commit`,
        ),
  );
  await page
    .getByRole("button", { name: "Запустить background commit" })
    .click();
  expect((await commitResponse).status()).toBe(202);

  // Realtime invalidation updates the UI; no manual reload is used here.
  await expect(page.getByText("Atomic finalization завершён")).toBeVisible({
    timeout: 60_000,
  });
  const completed = await importStatus(page.request, importRecord.id);
  expect(completed.status).toBe("completed");
  expect(completed.total_rows).toBe(651);
  expect(completed.created).toBe(650);
  expect(completed.updated).toBe(0);
  expect(completed.error_count).toBe(1);
  expect(completed.report_available).toBe(true);
  expect(completed.job_id).not.toBeNull();
  expect(await customerCount(page.request, projectId, searchPrefix)).toBe(650);

  const report = await page.request.get(
    `/api/v1/customers/import/background/${importRecord.id}/report`,
  );
  expect(report.ok()).toBeTruthy();
  expect((await report.json()) as { url: string }).toEqual({
    url: expect.stringMatching(/^https?:\/\//),
  });

  await page.goto("/app/settings");
  await page.getByRole("tab", { name: "Background Jobs" }).click();
  await expect(page.getByText("customer_import.process")).toBeVisible();
  await page.getByRole("tab", { name: "Storage" }).click();
  await expect(page.getByText("import_source", { exact: true })).toBeVisible();
  await expect(page.getByText("import_report", { exact: true })).toBeVisible();
  const scanResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes("/api/v1/storage/scan"),
  );
  await page.getByRole("button", { name: "Dry-run scan" }).click();
  expect((await scanResponse).status()).toBe(202);

  const holdReason = `Stage 14 legal hold ${suffix}`;
  await page.getByRole("tab", { name: "Legal Holds" }).click();
  await page.getByLabel("Причина").fill(holdReason);
  await page.getByRole("button", { name: "Установить hold" }).click();
  await expect(page.getByText(holdReason)).toBeVisible();

  await page.getByRole("tab", { name: "Retention" }).click();
  await expect(page.getByText("Auto purge выключен")).toBeVisible();
  await page.getByRole("button", { name: "Dry-run preview" }).click();
  await expect(page.getByText(/Legal hold исключил:/)).toBeVisible();
  const policyResponse = await page.request.get("/api/v1/retention/policy");
  expect(policyResponse.ok()).toBeTruthy();
  const policy = (await policyResponse.json()) as { state_version: number };
  const retentionRun = await page.request.post("/api/v1/retention/run", {
    headers: {
      "X-CSRF-Token": csrf,
      "Idempotency-Key": `disabled-retention-${suffix}`,
    },
    data: {
      preview_id: "00000000-0000-0000-0000-000000000001",
      policy_version: policy.state_version,
    },
  });
  expect(retentionRun.status()).toBe(409);

  for (const width of [1440, 1100]) {
    await page.setViewportSize({ width, height: 900 });
    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: `.test-artifacts/stage14-retention-${width}x900.png`,
      fullPage: true,
    });
  }

  const invitationResponse = await page.request.post(
    "/api/v1/team/invitations",
    {
      headers: {
        "X-CSRF-Token": csrf,
        "Idempotency-Key": `stage14-operator-${suffix}`,
      },
      data: {
        email: `stage14-operator+${suffix}@example.com`,
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
  try {
    const operatorCsrf = await acceptOperator(operatorContext, {
      slug,
      email: `stage14-operator+${suffix}@example.com`,
      password: "SecureStage14Operator123!",
      acceptanceToken: invitation.acceptance_token,
    });
    const operatorPage = await operatorContext.newPage();
    await operatorPage.goto("/app/settings");
    await expect(
      operatorPage.getByRole("tab", { name: "Background Jobs" }),
    ).toBeVisible();
    await expect(
      operatorPage.getByRole("tab", { name: "Retention" }),
    ).toHaveCount(0);
    await expect(
      operatorPage.getByRole("tab", { name: "Legal Holds" }),
    ).toHaveCount(0);
    const forbiddenRetention = await operatorContext.request.post(
      "/api/v1/retention/preview",
      {
        headers: {
          "X-CSRF-Token": operatorCsrf,
          "Idempotency-Key": `operator-retention-${suffix}`,
        },
        data: { policy_version: 1 },
      },
    );
    expect(forbiddenRetention.status()).toBe(403);
  } finally {
    await operatorContext.close();
  }

  const foreignContext = await browser.newContext();
  try {
    await registerTenant(foreignContext.request, `foreign-${suffix}`);
    const foreignImport = await foreignContext.request.get(
      `/api/v1/customers/import/background/${importRecord.id}/status`,
    );
    expect(foreignImport.status()).toBe(404);
    const foreignReport = await foreignContext.request.get(
      `/api/v1/customers/import/background/${importRecord.id}/report`,
    );
    expect(foreignReport.status()).toBe(404);
    const foreignJob = await foreignContext.request.get(
      `/api/v1/background-jobs/${completed.job_id!}`,
    );
    expect(foreignJob.status()).toBe(404);
  } finally {
    await foreignContext.close();
  }

  await page.goto("/app/customers");
  await page.getByRole("button", { name: "Импорт" }).click();
  await page.getByRole("tab", { name: "Большой фоновый импорт" }).click();
  for (const width of [1440, 1100]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByText("Atomic finalization завершён")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: `.test-artifacts/stage14-import-${width}x900.png`,
      fullPage: true,
    });
  }
  expect(consoleErrors).toEqual([]);
});
