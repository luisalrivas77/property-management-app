import type { Metadata } from "next";

import { LoginForm } from "./LoginForm";

export const metadata: Metadata = { title: "Anmelden" };

export default function LoginPage() {
  return <LoginForm />;
}
