import type { ButtonHTMLAttributes, ReactNode } from "react";

export function TeamoraLogo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="tv-logo" aria-label="K-Line">
      <svg
        className="tv-logo-mark"
        viewBox="0 0 74 58"
        role="img"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="kline-app-wave" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#B80A1F" />
            <stop offset="1" stopColor="#E41739" />
          </linearGradient>
        </defs>
        <rect x="2" y="20" width="7" height="18" rx="3.5" />
        <rect x="14" y="12" width="7" height="34" rx="3.5" />
        <rect x="26" y="2" width="7" height="54" rx="3.5" />
        <rect x="38" y="11" width="7" height="36" rx="3.5" />
        <rect x="50" y="19" width="7" height="20" rx="3.5" />
        <rect x="62" y="24" width="7" height="12" rx="3.5" />
      </svg>
      {!compact && (
        <span className="tv-logo-type">
          <strong>
            K<span>-</span>Line
          </strong>
          <small>CALL CENTER</small>
        </span>
      )}
    </span>
  );
}

export function StatusBadge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger" | "primary";
}) {
  return <span className={`tv-badge tv-badge-${tone}`}>{children}</span>;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "quiet";
};

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonProps) {
  return (
    <button
      className={`tv-button tv-button-${variant} ${className}`.trim()}
      {...props}
    />
  );
}
