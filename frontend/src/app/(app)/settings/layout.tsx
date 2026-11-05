import type { Metadata } from "next";
import type { ReactNode } from "react";

// The page is a client component, so its title is set here.
export const metadata: Metadata = { title: "Einstellungen" };

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return children;
}
