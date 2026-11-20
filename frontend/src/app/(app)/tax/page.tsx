"use client";

// Steuern (phase-3.md §11): /tax opens the current year's draft, keeping a chosen property, tab
// and owner.
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

export default function TaxPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <ToCurrentYear />
    </Suspense>
  );
}

function ToCurrentYear() {
  const router = useRouter();
  const search = useSearchParams();
  // The chosen property, form tab (Eure Angaben, Anlage N) and owner travel along.
  const query = new URLSearchParams();
  for (const key of ["immobilie", "tab", "eigentuemer"]) {
    const value = search.get(key);
    if (value) query.set(key, value);
  }
  const forwarded = query.toString();
  useEffect(() => {
    const year = new Date().getFullYear();
    router.replace(forwarded ? `/tax/${year}?${forwarded}` : `/tax/${year}`);
  }, [router, forwarded]);
  return <p className="muted">Lädt …</p>;
}
