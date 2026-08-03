import type { Metadata } from "next";
import { QueryProvider } from "@/components/query-provider";
import "./globals.css";
import "./kline-landing.css";

export const metadata: Metadata = {
  title: {
    default: "K-Line — контакт-центр для бизнеса",
    template: "%s | K-Line",
  },
  description:
    "K-Line — профессиональный контакт-центр для поддержки клиентов, обработки заявок и продаж.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html data-scroll-behavior="smooth" lang="ru">
      <body>
        <a className="skip-link" href="#main-content">
          Перейти к содержимому
        </a>
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
