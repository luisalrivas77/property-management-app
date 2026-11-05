import type { Metadata } from "next";
import type { ReactNode } from "react";

// The pages are client components, so their title is set here.
export const metadata: Metadata = { title: "Immobilie" };

export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
