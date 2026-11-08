import type { Metadata } from "next";
import type { ReactNode } from "react";

// The page is a client component, so its title is set here.
export const metadata: Metadata = { title: "Zu prüfen" };

export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
