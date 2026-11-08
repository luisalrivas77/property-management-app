"use client";

// Beleg fotografieren (docs/phases/phase-2.md §9, docs/mockups/06-mobile-capture.png): the
// phone capture page, reached from the review page (Zu prüfen) and from the home-screen shortcut
// (app/manifest.ts). The screen itself is components/capture.tsx.
import Link from "next/link";

import { CaptureScreen } from "@/components/capture";

export default function CapturePage() {
  return (
    <section>
      <p className="eyebrow">
        <Link href="/inbox">Zu prüfen</Link>
      </p>
      <div className="page-head">
        <h1>Beleg fotografieren</h1>
      </div>
      <CaptureScreen />
    </section>
  );
}
