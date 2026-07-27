import type { Metadata } from "next";
import { AuthLayout } from "@/components/auth-layout";
import { RegisterForm } from "@/components/register-form";

export const metadata: Metadata = { title: "Create workspace" };

export default function RegisterPage() {
  return (
    <AuthLayout>
      <RegisterForm />
    </AuthLayout>
  );
}
