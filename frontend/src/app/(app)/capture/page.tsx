"use client";

// Beleg fotografieren (docs/phases/phase-2.md §9, docs/mockups/06-mobile-capture.png): the
// phone capture page, reached from the review page (Zu prüfen) and from the home-screen shortcut
// (app/manifest.ts). The screen itself is components/capture.tsx.
import Link from "next/link";

import { CaptureScreen } from "@/components/capture";
import { useT } from "@/i18n";
import { capture } from "@/i18n/documentPage";
import { shell } from "@/i18n/shell";

export default function CapturePage() {
  const nav = useT(shell);
  const t = useT(capture);
  return (
    <section>
      <p className="eyebrow">
        <Link href="/inbox">{nav.inbox}</Link>
      </p>
      <div className="page-head">
        <h1>{t.takePhoto}</h1>
      </div>
      <CaptureScreen />
    </section>
  );
}
