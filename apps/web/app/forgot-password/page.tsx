import { AuthLayout } from "@/components/auth-layout";
import { StatusBadge } from "@teamora/ui";

export default function ForgotPasswordPage() {
  return (
    <AuthLayout>
      <div className="auth-card">
        <StatusBadge tone="warning">В разработке</StatusBadge>
        <h2 style={{ marginTop: 18 }}>Восстановление пароля пока недоступно</h2>
        <p>Обратитесь к владельцу компании.</p>
      </div>
    </AuthLayout>
  );
}
