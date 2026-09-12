"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Building2,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Headphones,
  Languages,
  Route,
  Save,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { AuthResponse } from "@/lib/types";

type QuestionType = "text" | "textarea" | "select" | "multi";

type QuestionOption = {
  label: string;
  value: string;
};

export type AiSetupQuestion = {
  id: string;
  step: number;
  label: string;
  hint: string;
  placeholder?: string;
  type: QuestionType;
  options?: QuestionOption[];
};

type AnswerValue = string | string[];
type AiSetupAnswers = Record<string, AnswerValue>;

export const AI_SETUP_QUESTIONS: AiSetupQuestion[] = [
  {
    id: "company_name",
    step: 0,
    label: "Как называется компания?",
    hint: "Это имя AI будет использовать в приветствии и ответах.",
    placeholder: "Например, K-Line",
    type: "text",
  },
  {
    id: "industry",
    step: 0,
    label: "В какой сфере работает компания?",
    hint: "Выберите ближайшую категорию. Детали можно добавить следующим ответом.",
    type: "select",
    options: [
      { label: "Услуги для бизнеса", value: "b2b_services" },
      { label: "Розничная торговля", value: "retail" },
      { label: "Медицина и клиники", value: "healthcare" },
      { label: "Образование", value: "education" },
      { label: "Недвижимость", value: "real_estate" },
      { label: "Доставка и логистика", value: "logistics" },
      { label: "Финансы", value: "finance" },
      { label: "Другая сфера", value: "other" },
    ],
  },
  {
    id: "company_description",
    step: 0,
    label: "Чем занимается компания?",
    hint: "Опишите работу компании двумя-тремя простыми предложениями.",
    placeholder: "Мы помогаем клиентам…",
    type: "textarea",
  },
  {
    id: "products_services",
    step: 0,
    label: "Какие товары или услуги вы предлагаете?",
    hint: "Перечислите основные направления, которые должен понимать AI.",
    placeholder: "Услуга 1, услуга 2, тарифы…",
    type: "textarea",
  },
  {
    id: "primary_goal",
    step: 1,
    label: "Главная цель AI-оператора",
    hint: "Это определит приоритет разговора.",
    type: "select",
    options: [
      { label: "Отвечать на вопросы", value: "support" },
      { label: "Принимать заявки", value: "lead_capture" },
      { label: "Записывать клиентов", value: "booking" },
      { label: "Продавать", value: "sales" },
      { label: "Совмещать несколько задач", value: "mixed" },
    ],
  },
  {
    id: "call_scenarios",
    step: 1,
    label: "Какие звонки должен обрабатывать AI?",
    hint: "Можно выбрать несколько сценариев.",
    type: "multi",
    options: [
      { label: "Входящие вопросы", value: "inbound_support" },
      { label: "Новые заявки", value: "new_leads" },
      { label: "Запись и бронирование", value: "booking" },
      { label: "Исходящие звонки", value: "outbound" },
      { label: "Напоминания", value: "reminders" },
    ],
  },
  {
    id: "target_audience",
    step: 1,
    label: "Кто ваши клиенты?",
    hint: "Укажите тип клиента, его потребности и частые причины обращения.",
    placeholder: "Малый бизнес, постоянные клиенты, новые покупатели…",
    type: "textarea",
  },
  {
    id: "service_regions",
    step: 1,
    label: "В каких городах или странах вы работаете?",
    hint: "Это поможет не обещать услугу там, где её нет.",
    placeholder: "Ташкент, весь Узбекистан…",
    type: "text",
  },
  {
    id: "languages",
    step: 2,
    label: "На каких языках должен разговаривать AI?",
    hint: "На следующем этапе мы отдельно проверим качество каждого языка.",
    type: "multi",
    options: [
      { label: "Русский", value: "ru" },
      { label: "Узбекский", value: "uz" },
      { label: "Каракалпакский", value: "kaa" },
      { label: "Английский", value: "en" },
    ],
  },
  {
    id: "work_schedule",
    step: 2,
    label: "Когда AI должен принимать звонки?",
    hint: "Расписание можно будет уточнить для каждого проекта.",
    type: "select",
    options: [
      { label: "Круглосуточно, 24/7", value: "always" },
      { label: "Только в рабочее время", value: "business_hours" },
      { label: "После закрытия офиса", value: "after_hours" },
      { label: "По отдельному расписанию", value: "custom" },
    ],
  },
  {
    id: "assistant_name",
    step: 2,
    label: "Как AI должен представляться?",
    hint: "Можно использовать имя или нейтральное название роли.",
    placeholder: "Например, виртуальный помощник Алия",
    type: "text",
  },
  {
    id: "greeting",
    step: 2,
    label: "Как должно звучать приветствие?",
    hint: "Укажите обязательные слова, бренд и предупреждение о записи.",
    placeholder: "Здравствуйте! Вы позвонили в…",
    type: "textarea",
  },
  {
    id: "voice_tone",
    step: 3,
    label: "Какой стиль общения нужен?",
    hint: "AI будет придерживаться этого тона во всём разговоре.",
    type: "select",
    options: [
      { label: "Деловой и краткий", value: "business" },
      { label: "Дружелюбный и спокойный", value: "friendly" },
      { label: "Заботливый и подробный", value: "caring" },
      { label: "Энергичный продавец", value: "sales" },
    ],
  },
  {
    id: "customer_questions",
    step: 3,
    label: "Какие вопросы AI обязательно задаёт клиенту?",
    hint: "Например: город, цель звонка, удобное время или номер заказа.",
    placeholder: "1. Как вас зовут?\n2. Чем можем помочь?",
    type: "textarea",
  },
  {
    id: "customer_data",
    step: 3,
    label: "Какие данные AI должен собирать?",
    hint: "Собирайте только действительно необходимые данные.",
    type: "multi",
    options: [
      { label: "Имя", value: "name" },
      { label: "Телефон", value: "phone" },
      { label: "Город", value: "city" },
      { label: "E-mail", value: "email" },
      { label: "Номер заказа", value: "order_id" },
      { label: "Комментарий", value: "comment" },
    ],
  },
  {
    id: "allowed_actions",
    step: 3,
    label: "Что AI разрешено делать во время звонка?",
    hint: "Интеграции появятся позже; сейчас задаём только будущие правила.",
    type: "multi",
    options: [
      { label: "Искать ответ в базе знаний", value: "knowledge" },
      { label: "Создавать заявку", value: "create_lead" },
      { label: "Записывать на время", value: "booking" },
      { label: "Назначать обратный звонок", value: "callback" },
      { label: "Переводить на человека", value: "transfer" },
    ],
  },
  {
    id: "knowledge_sources",
    step: 4,
    label: "Где находятся правильные ответы компании?",
    hint: "Укажите документы, сайт, прайс-лист, CRM или внутренние инструкции.",
    placeholder: "Сайт, PDF-каталог, Google Sheets, инструкции…",
    type: "textarea",
  },
  {
    id: "transfer_rules",
    step: 4,
    label: "Когда обязательно переводить звонок человеку?",
    hint: "Например: жалоба, сложный вопрос, просьба клиента или две ошибки подряд.",
    placeholder: "Переводить, если…",
    type: "textarea",
  },
  {
    id: "transfer_destination",
    step: 4,
    label: "Куда направлять перевод?",
    hint: "Укажите отдел или очередь. SIP-номер подключим позже.",
    placeholder: "Отдел продаж, поддержка, операторская очередь…",
    type: "text",
  },
  {
    id: "forbidden_actions",
    step: 4,
    label: "Что AI категорически нельзя говорить или делать?",
    hint: "Это основа безопасных ограничений будущего оператора.",
    placeholder: "Не обещать скидки, не придумывать цены, не раскрывать…",
    type: "textarea",
  },
];

const SETUP_STEPS = [
  {
    title: "О компании",
    subtitle: "Бренд и услуги",
    icon: Building2,
  },
  {
    title: "Задачи",
    subtitle: "Клиенты и звонки",
    icon: Headphones,
  },
  {
    title: "Голос",
    subtitle: "Языки и приветствие",
    icon: Languages,
  },
  {
    title: "Сценарий",
    subtitle: "Вопросы и действия",
    icon: Route,
  },
  {
    title: "Безопасность",
    subtitle: "Знания и перевод",
    icon: ShieldCheck,
  },
] as const;

function isAnswered(value: AnswerValue | undefined): boolean {
  return Array.isArray(value)
    ? value.length > 0
    : typeof value === "string" && value.trim().length > 0;
}

function optionLabel(questionId: string, value: string): string {
  const question = AI_SETUP_QUESTIONS.find((item) => item.id === questionId);
  return (
    question?.options?.find((item) => item.value === value)?.label ?? value
  );
}

export function CompanyAiSetupWizard() {
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const [answers, setAnswers] = useState<AiSetupAnswers>({});
  const [currentStep, setCurrentStep] = useState(0);
  const [activeDraftKey, setActiveDraftKey] = useState("");
  const [completed, setCompleted] = useState(false);
  const [validationMessage, setValidationMessage] = useState("");
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  const tenantId = me.data?.tenant.id;
  useEffect(() => {
    if (!tenantId) return;
    const draftKey = `kline:ai-company-setup:${tenantId}`;
    let nextAnswers: AiSetupAnswers = {};
    let nextCompleted = false;
    try {
      const stored = window.localStorage.getItem(draftKey);
      if (stored) {
        const parsed = JSON.parse(stored) as {
          answers?: AiSetupAnswers;
          completed?: boolean;
        };
        nextAnswers = parsed.answers ?? {};
        nextCompleted = Boolean(parsed.completed);
      }
    } catch {
      nextAnswers = {};
    }
    if (!isAnswered(nextAnswers.company_name)) {
      nextAnswers.company_name = me.data?.tenant.name ?? "";
    }
    setAnswers(nextAnswers);
    setCompleted(nextCompleted);
    setActiveDraftKey(draftKey);
  }, [me.data?.tenant.name, tenantId]);

  useEffect(() => {
    if (!activeDraftKey) return;
    window.localStorage.setItem(
      activeDraftKey,
      JSON.stringify({
        answers,
        completed,
        updated_at: new Date().toISOString(),
      }),
    );
    setSavedAt(new Date());
  }, [activeDraftKey, answers, completed]);

  const answeredCount = useMemo(
    () =>
      AI_SETUP_QUESTIONS.reduce(
        (total, question) => total + Number(isAnswered(answers[question.id])),
        0,
      ),
    [answers],
  );
  const currentQuestions = AI_SETUP_QUESTIONS.filter(
    (question) => question.step === currentStep,
  );
  const stepAnswered = currentQuestions.filter((question) =>
    isAnswered(answers[question.id]),
  ).length;
  const StepIcon = SETUP_STEPS[currentStep].icon;

  function setAnswer(id: string, value: AnswerValue) {
    setAnswers((current) => ({ ...current, [id]: value }));
    setCompleted(false);
    setValidationMessage("");
  }

  function toggleOption(id: string, value: string) {
    const current = Array.isArray(answers[id]) ? (answers[id] as string[]) : [];
    setAnswer(
      id,
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );
  }

  function goForward() {
    const missing = currentQuestions.filter(
      (question) => !isAnswered(answers[question.id]),
    );
    if (missing.length > 0) {
      setValidationMessage(
        `Ответьте ещё на ${missing.length} ${missing.length === 1 ? "вопрос" : "вопроса"} этого шага.`,
      );
      return;
    }
    if (currentStep === SETUP_STEPS.length - 1) {
      setCompleted(true);
      setValidationMessage("");
      return;
    }
    setCurrentStep((step) => Math.min(step + 1, SETUP_STEPS.length - 1));
    setValidationMessage("");
  }

  function resetDraft() {
    if (!window.confirm("Очистить все 20 ответов этой компании?")) return;
    const companyName = me.data?.tenant.name ?? "";
    setAnswers({ company_name: companyName });
    setCurrentStep(0);
    setCompleted(false);
    setValidationMessage("");
  }

  if (me.isPending) {
    return <div className="panel ai-setup-loading skeleton">Загрузка</div>;
  }

  if (me.isError) {
    return (
      <div className="panel error-state">
        <div>
          <h1>Не удалось открыть настройку</h1>
          <p>{me.error.message}</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="page-heading row-between ai-setup-heading">
        <div>
          <div className="ai-setup-kicker">
            <Sparkles size={15} /> Первичная настройка
          </div>
          <h1>Настройте AI под компанию</h1>
          <p>
            20 ответов станут основой голоса, сценария и правил будущего
            AI-оператора.
          </p>
        </div>
        <StatusBadge tone="warning">
          Только интерфейс · без генерации
        </StatusBadge>
      </div>

      <section className="ai-setup-overview panel">
        <div>
          <Bot aria-hidden="true" size={24} />
          <span>
            <strong>{answeredCount} из 20</strong>
            <small>вопросов заполнено</small>
          </span>
        </div>
        <div
          className="ai-setup-progress"
          role="progressbar"
          aria-valuemax={20}
          aria-valuemin={0}
          aria-valuenow={answeredCount}
        >
          <span style={{ width: `${(answeredCount / 20) * 100}%` }} />
        </div>
        <div className="ai-setup-save-state">
          <Save size={15} />
          {savedAt
            ? `Черновик сохранён в ${savedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
            : "Готовим черновик"}
        </div>
      </section>

      <div className="ai-setup-layout">
        <aside className="ai-setup-steps panel" aria-label="Шаги настройки AI">
          <div className="ai-setup-company">
            <span>Компания</span>
            <strong>{me.data.tenant.name}</strong>
            <small>Ответы хранятся отдельно для этой компании</small>
          </div>
          <nav>
            {SETUP_STEPS.map((step, index) => {
              const Icon = step.icon;
              const questions = AI_SETUP_QUESTIONS.filter(
                (question) => question.step === index,
              );
              const count = questions.filter((question) =>
                isAnswered(answers[question.id]),
              ).length;
              return (
                <button
                  aria-current={currentStep === index ? "step" : undefined}
                  className={currentStep === index ? "active" : ""}
                  key={step.title}
                  onClick={() => {
                    setCurrentStep(index);
                    setCompleted(false);
                    setValidationMessage("");
                  }}
                  type="button"
                >
                  <span className="ai-setup-step-icon">
                    {count === questions.length ? (
                      <Check size={16} />
                    ) : (
                      <Icon size={16} />
                    )}
                  </span>
                  <span>
                    <strong>{step.title}</strong>
                    <small>{step.subtitle}</small>
                  </span>
                  <em>{count}/4</em>
                </button>
              );
            })}
          </nav>
          <button className="ai-setup-reset" onClick={resetDraft} type="button">
            Очистить черновик
          </button>
        </aside>

        <main className="ai-setup-card panel">
          {completed ? (
            <div className="ai-setup-complete">
              <div className="ai-setup-complete-icon">
                <CheckCircle2 size={34} />
              </div>
              <StatusBadge tone="success">20 ответов готовы</StatusBadge>
              <h2>Черновик настройки компании собран</h2>
              <p>
                На следующем этапе эти ответы можно будет отправить внутреннему
                AI, получить системные инструкции, приветствие и сценарий
                разговора.
              </p>
              <div className="ai-setup-summary-grid">
                <article>
                  <Building2 size={17} />
                  <span>Компания</span>
                  <strong>{String(answers.company_name ?? "—")}</strong>
                </article>
                <article>
                  <Headphones size={17} />
                  <span>Главная задача</span>
                  <strong>
                    {optionLabel(
                      "primary_goal",
                      String(answers.primary_goal ?? "—"),
                    )}
                  </strong>
                </article>
                <article>
                  <Languages size={17} />
                  <span>Языки</span>
                  <strong>
                    {Array.isArray(answers.languages)
                      ? answers.languages
                          .map((value) => optionLabel("languages", value))
                          .join(", ")
                      : "—"}
                  </strong>
                </article>
                <article>
                  <Clock3 size={17} />
                  <span>График</span>
                  <strong>
                    {optionLabel(
                      "work_schedule",
                      String(answers.work_schedule ?? "—"),
                    )}
                  </strong>
                </article>
              </div>
              <div className="ai-setup-next-stage">
                <Sparkles size={18} />
                <div>
                  <strong>Генерация пока не подключена</strong>
                  <span>
                    Ответы сохранены только в этом браузере. Backend и OpenAI не
                    вызывались.
                  </span>
                </div>
              </div>
              <Button onClick={() => setCompleted(false)} variant="secondary">
                Изменить ответы
              </Button>
            </div>
          ) : (
            <>
              <header className="ai-setup-card-header">
                <div className="ai-setup-card-icon">
                  <StepIcon size={21} />
                </div>
                <div>
                  <span>
                    Шаг {currentStep + 1} из {SETUP_STEPS.length}
                  </span>
                  <h2>{SETUP_STEPS[currentStep].title}</h2>
                  <p>
                    {SETUP_STEPS[currentStep].subtitle} · заполнено{" "}
                    {stepAnswered} из 4
                  </p>
                </div>
              </header>

              <div className="ai-setup-questions">
                {currentQuestions.map((question) => {
                  const number =
                    AI_SETUP_QUESTIONS.findIndex(
                      (item) => item.id === question.id,
                    ) + 1;
                  const describedBy = `${question.id}-hint`;
                  return (
                    <section className="ai-setup-question" key={question.id}>
                      <div className="ai-setup-question-number">{number}</div>
                      <div className="ai-setup-question-content">
                        <label htmlFor={question.id}>{question.label}</label>
                        <p id={describedBy}>{question.hint}</p>
                        {question.type === "text" && (
                          <input
                            aria-describedby={describedBy}
                            id={question.id}
                            onChange={(event) =>
                              setAnswer(question.id, event.target.value)
                            }
                            placeholder={question.placeholder}
                            value={String(answers[question.id] ?? "")}
                          />
                        )}
                        {question.type === "textarea" && (
                          <textarea
                            aria-describedby={describedBy}
                            id={question.id}
                            onChange={(event) =>
                              setAnswer(question.id, event.target.value)
                            }
                            placeholder={question.placeholder}
                            rows={3}
                            value={String(answers[question.id] ?? "")}
                          />
                        )}
                        {question.type === "select" && (
                          <select
                            aria-describedby={describedBy}
                            id={question.id}
                            onChange={(event) =>
                              setAnswer(question.id, event.target.value)
                            }
                            value={String(answers[question.id] ?? "")}
                          >
                            <option value="">Выберите вариант</option>
                            {question.options?.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        )}
                        {question.type === "multi" && (
                          <div
                            className="ai-setup-options"
                            role="group"
                            aria-label={question.label}
                          >
                            {question.options?.map((option) => {
                              const values = Array.isArray(answers[question.id])
                                ? (answers[question.id] as string[])
                                : [];
                              const selected = values.includes(option.value);
                              return (
                                <button
                                  aria-pressed={selected}
                                  className={selected ? "selected" : ""}
                                  key={option.value}
                                  onClick={() =>
                                    toggleOption(question.id, option.value)
                                  }
                                  type="button"
                                >
                                  <span>{selected && <Check size={13} />}</span>
                                  {option.label}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>

              {validationMessage && (
                <div className="ai-setup-validation" role="alert">
                  {validationMessage}
                </div>
              )}
              <footer className="ai-setup-actions">
                <Button
                  disabled={currentStep === 0}
                  onClick={() => {
                    setCurrentStep((step) => Math.max(step - 1, 0));
                    setValidationMessage("");
                  }}
                  variant="secondary"
                >
                  <ChevronLeft size={16} /> Назад
                </Button>
                <div>
                  <span>{answeredCount}/20 ответов</span>
                  <Button onClick={goForward}>
                    {currentStep === SETUP_STEPS.length - 1 ? (
                      <>
                        <CheckCircle2 size={16} /> Завершить анкету
                      </>
                    ) : (
                      <>
                        Следующий шаг <ChevronRight size={16} />
                      </>
                    )}
                  </Button>
                </div>
              </footer>
            </>
          )}
        </main>
      </div>
    </>
  );
}
