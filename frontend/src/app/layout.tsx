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
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  );
}
