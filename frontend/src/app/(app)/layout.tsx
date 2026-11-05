import type { ReactNode } from "react";

import { AuthGate } from "@/auth/AuthGate";

import { Sidebar } from "./Sidebar";

// Every signed-in screen (§9.2): the AuthGate resolves the session first, then the sidebar
// and the content column render side by side (stacked on a phone).
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <div className="shell">
        <Sidebar />
        <main className="content">
          <div className="content-inner">{children}</div>
        </main>
      </div>
    </AuthGate>
  );
}
