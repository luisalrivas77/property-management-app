import type { ReactNode } from "react";

import { BrandMark } from "@/lib/BrandMark";

// /login and /enroll: no session needed, no navigation (§9.2); a centred card under the
// brand mark.
export default function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <main className="public">
      <p className="brand">
        <BrandMark />
        <span>Hausbuch</span>
      </p>
      {children}
    </main>
  );
}
