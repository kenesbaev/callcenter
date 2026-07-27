"use client";

import {
  ArrowRight,
  BookOpen,
  Cable,
  Check,
  Languages,
  LockKeyhole,
  Network,
  Radio,
  ShieldCheck,
  Sparkles,
  UserRoundCheck,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { StatusBadge, TeamoraLogo } from "@teamora/ui";

const copy = {
  en: {
    eyebrow: "Multilingual AI call operations",
    title: "AI Call Center that speaks your customer’s language",
    description:
      "Build controlled voice agents for Russian, English, and Uzbek conversations — with verified knowledge, typed tools, and a clean human handoff.",
    primary: "Start test call",
    secondary: "View how it works",
  },
  ru: {
    eyebrow: "Многоязычные AI-операции",
    title: "AI-колл-центр, который говорит на языке вашего клиента",
    description:
      "Создавайте управляемых голосовых операторов для русского, английского и узбекского языков — с проверенной базой знаний и переводом человеку.",
    primary: "Начать тестовый звонок",
    secondary: "Как это работает",
  },
};

const features = [
  {
    icon: Network,
    title: "Connect your number",
    body: "Keep your SIP provider and DID while every inbound call is resolved to the correct tenant.",
  },
  {
    icon: BookOpen,
    title: "Ground every answer",
    body: "Use tenant knowledge and server-authorized tools. Unknown answers trigger handoff, not guessing.",
  },
  {
    icon: UserRoundCheck,
    title: "Handoff with context",
    body: "Human operators receive language, reason, summary, transcript, and safe tool history.",
  },
];

const pricing = [
  ["Start", "$99", "$999/year", "250 AI minutes"],
  ["Business", "$149", "$1,500/year", "500 AI minutes"],
  ["Pro", "$299", "$2,990/year", "2,000 AI minutes"],
  ["Enterprise", "Custom", "Contract", "Custom capacity"],
];

export function LandingPage() {
  const [locale, setLocale] = useState<"en" | "ru">("en");
  const text = copy[locale];
  return (
    <div className="landing-shell">
      <header className="landing-header">
        <div className="landing-container landing-header-inner">
          <Link href="/" aria-label="Teamora Voice home">
            <TeamoraLogo />
          </Link>
          <nav className="landing-nav" aria-label="Landing navigation">
            <a href="#how">How it works</a>
            <a href="#languages">Languages</a>
            <a href="#security">Security</a>
            <a href="#pricing">Pricing</a>
          </nav>
          <div className="landing-actions">
            <div className="locale-switch" aria-label="Language">
              <button
                className={locale === "en" ? "active" : ""}
                onClick={() => setLocale("en")}
                type="button"
              >
                EN
              </button>
              <button
                className={locale === "ru" ? "active" : ""}
                onClick={() => setLocale("ru")}
                type="button"
              >
                RU
              </button>
            </div>
            <Link className="header-login" href="/login">
              Log in
            </Link>
            <Link className="tv-button tv-button-primary" href="/register">
              Get started <ArrowRight size={16} />
            </Link>
          </div>
        </div>
      </header>
      <main id="main-content">
        <section className="landing-container hero">
          <div className="hero-copy">
            <p className="eyebrow">{text.eyebrow}</p>
            <h1>{text.title}</h1>
            <p>{text.description}</p>
            <div className="hero-actions">
              <Link className="tv-button tv-button-primary" href="/register">
                {text.primary} <ArrowRight size={17} />
              </Link>
              <a className="tv-button tv-button-secondary" href="#how">
                {text.secondary}
              </a>
            </div>
            <div className="hero-proof">
              <span>
                <i /> Tenant-isolated by design
              </span>
              <span>
                <i /> Human handoff built in
              </span>
              <span>
                <i /> No fake integrations
              </span>
            </div>
          </div>
          <div
            className="live-visual"
            aria-label="Product preview of an active call"
          >
            <div className="visual-top">
              <span>Product preview</span>
              <span className="live-indicator">Active call</span>
            </div>
            <div className="visual-call-row">
              <div className="caller-avatar">+998</div>
              <div className="visual-caller">
                <strong>Incoming customer</strong>
                <span>Russian · 00:42 · AI operator Mira</span>
              </div>
              <StatusBadge tone="success">Knowledge grounded</StatusBadge>
            </div>
            <div className="waveform" aria-hidden="true">
              {Array.from({ length: 24 }, (_, index) => (
                <i key={index} />
              ))}
            </div>
            <div className="visual-transcript">
              <div className="visual-line ai">
                Virtual assistant disclosure and recording notice.
              </div>
              <div className="visual-line">
                Customer asks about service hours.
              </div>
              <div className="visual-line ai">
                Verified answer returned from tenant knowledge.
              </div>
            </div>
            <div className="visual-tool">
              <Check size={15} color="var(--tv-success)" /> search_knowledge ·
              completed
            </div>
          </div>
        </section>
        <section className="section section-lined" id="how">
          <div className="landing-container">
            <div className="section-heading">
              <h2>From the company number to a resolved conversation</h2>
              <p>
                Asterisk keeps telephony under your control while Teamora Voice
                handles tenant policy, realtime AI, tools, and outcomes.
              </p>
            </div>
            <div className="feature-grid">
              {features.map(({ icon: Icon, title, body }) => (
                <article className="feature-card" key={title}>
                  <span className="feature-icon">
                    <Icon size={20} />
                  </span>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>
        <section className="section section-lined" id="languages">
          <div className="landing-container">
            <div className="section-heading">
              <h2>Four language tracks with honest readiness</h2>
              <p>
                Uzbek stays beta until real STT/TTS review; Karakalpak is always
                experimental.
              </p>
            </div>
            <div className="language-grid">
              <article className="language-card">
                <StatusBadge tone="success">Production target</StatusBadge>
                <h3>Русский</h3>
                <p>Russian call flows and transcripts.</p>
              </article>
              <article className="language-card">
                <StatusBadge tone="success">Production target</StatusBadge>
                <h3>English</h3>
                <p>English operations with the same policy boundary.</p>
              </article>
              <article className="language-card">
                <StatusBadge tone="warning">Beta</StatusBadge>
                <h3>O‘zbekcha</h3>
                <p>Provider quality validation required.</p>
              </article>
              <article className="language-card">
                <StatusBadge tone="danger">Experimental</StatusBadge>
                <h3>Qaraqalpaqsha</h3>
                <p>Feature-flagged; never presented as production-ready.</p>
              </article>
            </div>
          </div>
        </section>
        <section className="section section-lined">
          <div className="landing-container handoff-layout">
            <div className="section-heading" style={{ display: "block" }}>
              <p className="eyebrow">AI + human</p>
              <h2>Escalation is a product path</h2>
              <p style={{ marginTop: 18 }}>
                Human requests, low confidence, knowledge gaps, prohibited
                actions, serious complaints, and critical tool errors create a
                controlled transfer.
              </p>
            </div>
            <div className="handoff-flow">
              {[
                ["01", "Detect policy", "Capture reason and language."],
                [
                  "02",
                  "Prepare context",
                  "Package summary, transcript, customer and tools.",
                ],
                [
                  "03",
                  "Transfer or callback",
                  "Use an approved queue or close gracefully.",
                ],
              ].map(([number, title, body]) => (
                <div className="flow-step" key={number}>
                  <span>{number}</span>
                  <div>
                    <strong>{title}</strong>
                    <small>{body}</small>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
        <section className="section section-lined">
          <div className="landing-container analytics-layout">
            <div className="analytics-preview">
              <div className="card-title">
                <strong>Conversation volume</strong>
                <StatusBadge>Live data only</StatusBadge>
              </div>
              <div
                className="bar-chart"
                aria-label="Illustrative chart preview"
              >
                {[34, 48, 40, 68, 56, 82, 71, 88, 64, 78].map(
                  (height, index) => (
                    <i key={index} style={{ height: `${height}%` }} />
                  ),
                )}
              </div>
            </div>
            <div className="section-heading" style={{ display: "block" }}>
              <p className="eyebrow">Analytics</p>
              <h2>Metrics without invented production numbers</h2>
              <p style={{ marginTop: 18 }}>
                Track outcomes, languages, transfers, queue load, AI minutes,
                and estimated cost. Empty tenants see zeros and setup guidance.
              </p>
            </div>
          </div>
        </section>
        <section className="section section-lined">
          <div className="landing-container">
            <div className="section-heading">
              <h2>Provider-independent foundations</h2>
              <p>
                Only implemented and credentialed adapters can move beyond
                Unavailable or Development.
              </p>
            </div>
            <div className="feature-grid">
              <article className="feature-card">
                <span className="feature-icon">
                  <Radio size={20} />
                </span>
                <h3>Realtime voice</h3>
                <p>OpenAI Realtime first, behind a replaceable interface.</p>
              </article>
              <article className="feature-card">
                <span className="feature-icon">
                  <Cable size={20} />
                </span>
                <h3>CRM tools</h3>
                <p>
                  Generic signed webhook first; named CRMs remain unavailable.
                </p>
              </article>
              <article className="feature-card">
                <span className="feature-icon">
                  <Languages size={20} />
                </span>
                <h3>Speech providers</h3>
                <p>
                  Azure and Yandex are inactive quality-comparison adapters.
                </p>
              </article>
            </div>
          </div>
        </section>
        <section className="section section-lined" id="security">
          <div className="landing-container">
            <div className="section-heading">
              <h2>Security boundaries in the call path</h2>
              <p>
                Identity, tenant scope, tools, recordings, and provider
                credentials are separate trust zones.
              </p>
            </div>
            <div className="security-grid">
              <article className="feature-card">
                <span className="feature-icon">
                  <ShieldCheck size={20} />
                </span>
                <h3>Tenant isolation</h3>
                <p>
                  Application filters plus PostgreSQL RLS and negative tests.
                </p>
              </article>
              <article className="feature-card">
                <span className="feature-icon">
                  <LockKeyhole size={20} />
                </span>
                <h3>Private recordings</h3>
                <p>
                  Tenant prefixes, encryption, signed URLs, retention, and
                  access audit.
                </p>
              </article>
              <article className="feature-card">
                <span className="feature-icon">
                  <Sparkles size={20} />
                </span>
                <h3>Typed tools only</h3>
                <p>
                  No arbitrary SQL, URLs, shell, secrets, prices, discounts, or
                  payment claims.
                </p>
              </article>
            </div>
          </div>
        </section>
        <section className="section section-lined" id="pricing">
          <div className="landing-container">
            <div className="section-heading">
              <h2>Plans that map to AI minutes</h2>
              <p>
                SIP numbers and carrier minutes are separate. Additional AI
                usage is $0.08 per minute.
              </p>
            </div>
            <div className="pricing-grid">
              {pricing.map(([name, monthly, annual, minutes]) => (
                <article
                  className={`pricing-card ${name === "Pro" ? "featured" : ""}`}
                  key={name}
                >
                  <StatusBadge tone={name === "Pro" ? "primary" : "neutral"}>
                    {name === "Pro" ? "Recommended" : "Plan"}
                  </StatusBadge>
                  <h3>{name}</h3>
                  <strong className="price">{monthly}</strong>
                  <p>per month</p>
                  <ul>
                    <li>{annual}</li>
                    <li>{minutes}</li>
                    <li>$0.08 additional AI minute</li>
                  </ul>
                </article>
              ))}
            </div>
          </div>
        </section>
        <section className="section section-lined">
          <div className="landing-container">
            <div className="section-heading">
              <h2>Questions before your first test</h2>
            </div>
            <div className="faq-list">
              <details>
                <summary>Is the test call a real phone call?</summary>
                <p>
                  No. The first slice uses a labeled development simulator. SIP
                  requires a carrier and two-way audio evidence.
                </p>
              </details>
              <details>
                <summary>Can the AI call any API?</summary>
                <p>
                  No. Only server-registered typed tools with tenant
                  authorization, timeout, idempotency, and audit.
                </p>
              </details>
              <details>
                <summary>Is Karakalpak production-ready?</summary>
                <p>
                  No. It remains Experimental behind
                  `KARAKALPAK_EXPERIMENTAL=true` until real quality tests exist.
                </p>
              </details>
              <details>
                <summary>Is payment connected?</summary>
                <p>No payment provider is connected in this stage.</p>
              </details>
            </div>
          </div>
        </section>
      </main>
      <footer className="landing-footer">
        <div className="landing-container footer-inner">
          <TeamoraLogo />
          <span>
            © 2026 Teamora Voice · Built for controlled call operations
          </span>
        </div>
      </footer>
    </div>
  );
}
