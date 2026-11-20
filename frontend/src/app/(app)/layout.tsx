import type { ReactNode } from "react";

import { AuthGate } from "@/auth/AuthGate";

import { AppNav } from "./AppNav";

// Every signed-in screen (§9.2, phase 10 D266): the AuthGate resolves the session first, then
// the menu (a bar at the top on the computer, at the bottom on the phone) and the content.
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <div className="shell">
        <AppNav />
        <main className="content">
          <div className="content-inner">{children}</div>
        </main>
      </div>
    </AuthGate>
  );
}
