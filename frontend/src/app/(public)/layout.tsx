import type { ReactNode } from "react";

// /login and /enroll: no session needed, no navigation (§9.2).
export default function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <main className="narrow">
      <p>
        <strong>Hausbuch</strong>
      </p>
      {children}
    </main>
  );
}
