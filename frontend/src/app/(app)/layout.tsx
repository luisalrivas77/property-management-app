import type { ReactNode } from "react";

import { AuthGate } from "@/auth/AuthGate";

import { TopBar } from "./TopBar";

// Every signed-in screen (§9.2): the AuthGate resolves the session first, then the top bar
// and the content column render.
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <TopBar />
      <main className="content">{children}</main>
    </AuthGate>
  );
}
