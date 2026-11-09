"use client";

// Steuern (phase-3.md §11): /tax opens the current year's draft, keeping a chosen property.
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
  const property = search.get("immobilie");
  useEffect(() => {
    const year = new Date().getFullYear();
    router.replace(property ? `/tax/${year}?immobilie=${encodeURIComponent(property)}` : `/tax/${year}`);
  }, [router, property]);
  return <p className="muted">Lädt …</p>;
}
