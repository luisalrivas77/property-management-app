import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@fontsource-variable/manrope";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Hausbuch", template: "%s · Hausbuch" },
  description: "Immobilien, Mietverhältnisse, Darlehen und Belege an einem Ort.",
  applicationName: "Hausbuch",
  appleWebApp: { capable: true, title: "Hausbuch", statusBarStyle: "default" },
  // Private workspace: nothing here is for search engines.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#1f6f54",
  // The page runs under the phone's notch and home indicator; the bottom bar and the content
  // keep clear of them with env(safe-area-inset-*) (phase 10 D266).
  viewportFit: "cover",
};

// Before the first paint (phase 10 D283, D284): the light or dark choice and the language
// stored on this device, so a page never flashes in the other palette. The keys and values are
// those of src/lib/theme.ts and src/i18n/locale.ts; storage may throw (a private window), and
// then the defaults stand (the device's palette, German).
const DEVICE_SETTINGS_SCRIPT = `try{var d=document.documentElement,t=localStorage.getItem("hausbuch.theme");if(t==="light"||t==="dark")d.dataset.theme=t;if(localStorage.getItem("hausbuch.locale")==="en")d.lang="en"}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The script changes data-theme and lang before React hydrates, hence suppressHydrationWarning
    // (it covers this element's own attributes only).
    <html lang="de" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: DEVICE_SETTINGS_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
