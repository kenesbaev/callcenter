import Link from "next/link";
import { TeamoraLogo } from "@teamora/ui";

export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="auth-shell" id="main-content">
      <aside className="auth-aside">
        <Link href="/">
          <TeamoraLogo />
        </Link>
        <div>
          <h1>Все разговоры под контролем.</h1>
          <p>AI-операторы, база знаний и история звонков в одном кабинете.</p>
        </div>
        <span className="tv-badge tv-badge-warning">Тестовая версия</span>
      </aside>
      <section className="auth-main">{children}</section>
    </main>
  );
}
