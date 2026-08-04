import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const DOCX_BASE64 =
  "UEsDBBQAAAAIABeVA115bjPX6AAAAK0BAAATAAAAW0NvbnRlbnRfVHlwZXNdLnhtbH1QyU7DMBD9FWuuKHHggBCK0wPLETiUDxjZk8SqN3nc0v49Tlt6QIXjzFv1+tXeO7GjzDYGBbdtB4KCjsaGScHn+rV5AMEFg0EXAyk4EMNq6NeHRCyqNrCCuZT0KCXrmTxyGxOFiowxeyz1zJNMqDc4kbzrunupYygUSlMWDxj6Zxpx64p42df3qUcmxyCeTsQlSwGm5KzGUnG5C+ZXSnNOaKvyyOHZJr6pBJBXExbk74Cz7r0Ok60h8YG5vKGvLPkVs5Em6q2vyvZ/mys94zhaTRf94pZy1MRcF/euvSAebfjpL49zD99QSwMEFAAAAAgAF5UDXZv9N+qtAAAAKQEAAAsAAABfcmVscy8ucmVsc43POw7CMAwG4KtE3mlaBoRQ0y4IqSsqB7ASN61oHkrCo7cnAwNFDIy2f3+W6/ZpZnanECdnBVRFCYysdGqyWsClP232wGJCq3B2lgQsFKFt6jPNmPJKHCcfWTZsFDCm5A+cRzmSwVg4TzZPBhcMplwGzT3KK2ri27Lc8fBpwNpknRIQOlUB6xdP/9huGCZJRydvhmz6ceIrkWUMmpKAhwuKq3e7yCzwpuarF5sXUEsDBBQAAAAIABeVA11GWhfX2QAAACoBAAARAAAAd29yZC9kb2N1bWVudC54bWxFj7FOxDAMhl/Fyn5NYUCoanvDIRaQYACJNZf62ojGjhz3St+e5hhYPsu29Nl/e/yJM1xRcmDqzF1VG0DyPAQaO/P58Xx4NJDV0eBmJuzMhtkc+3ZtBvZLRFLYBZSbtTOTamqszX7C6HLFCWnfXVii072V0a4sQxL2mPPuj7O9r+sHG10gU5RnHrZSU4EUaP9yeA2E8PR2+oLMi3hswDOpOK+Ql5RYFEIGd3VhducZAfc0G6yI34Pb4CIcgYpCGTCMkyJS1driLpQbbxczen0Xexv8vWL/Y/a/UEsBAhQAFAAAAAgAF5UDXXluM9foAAAArQEAABMAAAAAAAAAAAAAAIABAAAAAFtDb250ZW50X1R5cGVzXS54bWxQSwECFAAUAAAACAAXlQNdm/036q0AAAApAQAACwAAAAAAAAAAAAAAgAEZAQAAX3JlbHMvLnJlbHNQSwECFAAUAAAACAAXlQNdRloX19kAAAAqAQAAEQAAAAAAAAAAAAAAgAHvAQAAd29yZC9kb2N1bWVudC54bWxQSwUGAAAAAAMAAwC5AAAA9wIAAAAA";

function textPdf(text: string): Buffer {
  const escaped = text
    .replaceAll("\\", "\\\\")
    .replaceAll("(", "\\(")
    .replaceAll(")", "\\)");
  const stream = `BT /F1 12 Tf 72 720 Td (${escaped}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
  ];
  let body = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(body));
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("");
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, "ascii");
}

async function uploadFromUi(
  page: Page,
  title: string,
  file: { name: string; mimeType: string; buffer: Buffer },
): Promise<void> {
  const form = page
    .locator("form.form-card")
    .filter({ hasText: "Загрузить файл" });
  await form.getByLabel("Название").fill(title);
  await form.getByLabel("Язык").fill("en");
  await form.locator('input[type="file"]').setInputFiles(file);
  await form.getByRole("button", { name: "Загрузить" }).click();
  const row = page
    .locator("article.knowledge-document-row")
    .filter({ hasText: title });
  await expect(row).toBeVisible();
  await expect(row).toContainText("Готов", { timeout: 30_000 });
}

async function registerTenant(
  request: APIRequestContext,
  suffix: string,
): Promise<{ csrf: string; projectId: string; slug: string }> {
  const slug = `knowledge-${suffix}`;
  const registration = await request.post("/api/v1/auth/register", {
    data: {
      company_name: `K-Line Knowledge ${suffix}`,
      company_slug: slug,
      display_name: "Knowledge Owner",
      email: `knowledge-owner+${suffix}@example.com`,
      password: "SecureKnowledgeOwner123!",
    },
  });
  expect(registration.ok()).toBeTruthy();
  const auth = (await registration.json()) as { csrf_token: string };
  const projects = await request.get("/api/v1/projects");
  expect(projects.ok()).toBeTruthy();
  const projectId = (
    (await projects.json()) as { items: Array<{ id: string }> }
  ).items[0]!.id;
  return { csrf: auth.csrf_token, projectId, slug };
}

test.setTimeout(150_000);

test("versioned Knowledge RAG processes sources, publishes citations, and isolates tenants", async ({
  page,
  browser,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const { csrf, projectId, slug } = await registerTenant(page.request, suffix);

  await page.goto("/app/knowledge");
  await expect(page.getByTestId("realtime-status")).toContainText(
    "В реальном времени",
    { timeout: 15_000 },
  );
  await page.getByLabel("Название новой базы").fill(`Knowledge E2E ${suffix}`);
  await page.getByLabel("Описание базы").fill("Versioned multilingual sources");
  await page.getByRole("button", { name: "Создать" }).click();
  await expect(
    page.getByRole("heading", { name: `Knowledge E2E ${suffix}` }),
  ).toBeVisible();

  await uploadFromUi(page, "TXT support policy", {
    name: "support.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "K-Line TXT source: priority assistance is available for verified customers every weekday.",
      "utf8",
    ),
  });
  await uploadFromUi(page, "PDF photon policy", {
    name: "photon-policy.pdf",
    mimeType: "application/pdf",
    buffer: textPdf(
      "K-Line PDF source: the photon policy reference code is LUMEN-4821 for escalation.",
    ),
  });
  await uploadFromUi(page, "DOCX contract policy", {
    name: "contract-policy.docx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: Buffer.from(DOCX_BASE64, "base64"),
  });

  const basesResponse = await page.request.get(
    `/api/v1/knowledge/bases?project_id=${projectId}`,
  );
  expect(basesResponse.ok()).toBeTruthy();
  const base = (
    (await basesResponse.json()) as {
      items: Array<{
        id: string;
        draft_revision: { id: string; lock_version: number };
      }>;
    }
  ).items[0]!;

  await page.getByRole("button", { name: "Опубликовать" }).click();
  await expect(page.getByText(/Revision опубликована/)).toBeVisible();

  await page
    .getByLabel("Вопрос для базы знаний")
    .fill("What is the LUMEN-4821 reference code?");
  await page.getByRole("button", { name: "Найти" }).click();
  const pdfHit = page.locator("article.retrieval-hit").filter({
    hasText: "PDF photon policy",
  });
  await expect(pdfHit).toBeVisible();
  await pdfHit.getByRole("button", { name: "Источник" }).click();
  await expect(page.getByRole("dialog")).toContainText("Страница 1");
  await expect(page.getByRole("dialog")).toContainText("LUMEN-4821");
  await page.getByRole("button", { name: "Закрыть" }).click();

  const noMatch = await page.request.post("/api/v1/knowledge/retrieval/test", {
    headers: {
      "X-CSRF-Token": csrf,
      "Idempotency-Key": `no-match-${suffix}`,
    },
    data: {
      project_id: projectId,
      knowledge_base_id: base.id,
      query: "unrelated quantum zebra orchard value",
      language: "en",
      top_k: 5,
      threshold: 1,
    },
  });
  expect(noMatch.ok()).toBeTruthy();
  expect(((await noMatch.json()) as { no_match: boolean }).no_match).toBe(true);

  const aiOperator = await page.request.post("/api/v1/ai-operators", {
    headers: { "X-CSRF-Token": csrf },
    data: {
      project_id: projectId,
      name: `Knowledge Operator ${suffix}`,
      description: "Deterministic retrieval operator",
      system_instructions:
        "Use only published knowledge citations and request a human when no source matches.",
      allowed_languages: ["en"],
      greeting_by_language: { en: "Knowledge test ready" },
      allowed_tools: ["search_knowledge", "request_human_operator", "end_call"],
    },
  });
  expect(aiOperator.ok()).toBeTruthy();
  const operatorId = ((await aiOperator.json()) as { id: string }).id;
  const publishedOperator = await page.request.post(
    `/api/v1/ai-operators/${operatorId}/publish`,
    { headers: { "X-CSRF-Token": csrf } },
  );
  expect(publishedOperator.ok()).toBeTruthy();

  await page.goto("/app/dev-simulator");
  await page
    .getByLabel("Опубликованный оператор")
    .selectOption({ label: `Knowledge Operator ${suffix} · v1` });
  await page.getByLabel("Язык").selectOption("en");
  await page.getByLabel("Имя тестового клиента").fill("Knowledge E2E Customer");
  await page.getByRole("button", { name: "Начать" }).click();
  await expect(page.getByText("Knowledge revision")).toBeVisible();
  await expect(
    page.locator(".session-facts").filter({ hasText: "Knowledge revision" }),
  ).not.toContainText("Не зафиксирована");
  await page.getByLabel("Сообщение клиента").fill("What is LUMEN-4821?");
  await page.getByRole("button", { name: "Отправить" }).click();
  await expect(page.getByText("PDF photon policy")).toBeVisible();
  await expect(page.getByText(/Без LLM/)).toBeVisible();

  const invitationResponse = await page.request.post(
    "/api/v1/team/invitations",
    {
      headers: {
        "X-CSRF-Token": csrf,
        "Idempotency-Key": `knowledge-invite-${suffix}`,
      },
      data: {
        email: `knowledge-operator+${suffix}@example.com`,
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
    const accepted = await operatorContext.request.post(
      "/api/v1/team/invitations/accept",
      {
        data: {
          tenant_slug: slug,
          token: invitation.acceptance_token,
          display_name: "Knowledge Human Operator",
          password: "SecureKnowledgeOperator123!",
        },
      },
    );
    expect(accepted.ok()).toBeTruthy();
    const login = await operatorContext.request.post("/api/v1/auth/login", {
      data: {
        company_slug: slug,
        email: `knowledge-operator+${suffix}@example.com`,
        password: "SecureKnowledgeOperator123!",
      },
    });
    expect(login.ok()).toBeTruthy();
    const operatorAuth = (await login.json()) as { csrf_token: string };
    const forbidden = await operatorContext.request.post(
      `/api/v1/knowledge/bases/${base.id}/archive`,
      {
        headers: { "X-CSRF-Token": operatorAuth.csrf_token },
        data: { expected_version: 1 },
      },
    );
    expect(forbidden.status()).toBe(403);
    const operatorPage = await operatorContext.newPage();
    await operatorPage.goto("/app/knowledge");
    await expect(
      operatorPage.getByRole("heading", { name: `Knowledge E2E ${suffix}` }),
    ).toBeVisible();
    await expect(
      operatorPage.getByRole("button", { name: "Опубликовать" }),
    ).toHaveCount(0);
  } finally {
    await operatorContext.close();
  }

  const foreignContext = await browser.newContext();
  try {
    await registerTenant(foreignContext.request, `foreign-${suffix}`);
    const foreignRead = await foreignContext.request.get(
      `/api/v1/knowledge/bases/${base.id}`,
    );
    expect(foreignRead.status()).toBe(404);
  } finally {
    await foreignContext.close();
  }

  await page.goto("/app/knowledge");
  await expect(
    page.getByRole("heading", { name: `Knowledge E2E ${suffix}` }),
  ).toBeVisible();
  await expect(page.getByText("PDF photon policy")).toBeVisible();

  for (const width of [1440, 1100]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(
      page.getByRole("heading", { name: "База знаний" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `.test-artifacts/stage13-knowledge-${width}x900.png`,
      fullPage: true,
    });
  }

  await page.goto("/app/dev-simulator");
  await expect(
    page.getByRole("heading", { name: "Тестовый звонок" }),
  ).toBeVisible();
  for (const width of [1440, 1100]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `.test-artifacts/stage13-simulator-${width}x900.png`,
      fullPage: true,
    });
  }
  expect(consoleErrors).toEqual([]);
});
