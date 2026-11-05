"use client";

// Small layout pieces the domain screens share: loading/error states, breadcrumbs, tabs and a
// dialog. Plain elements and CSS only (§9); everything is reachable by keyboard.
import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { ErrorNotice } from "@/lib/ErrorNotice";

import type { Loaded } from "./useLoad";

import "./screens.css";

/** Renders `children(data)` once loaded; the loading line or the error (with retry) before. */
export function Loading<T>({ state, children }: { state: Loaded<T>; children: (data: T) => ReactNode }) {
  if (state.data !== undefined) {
    return (
      <>
        {state.error != null ? <ErrorNotice error={state.error} /> : null}
        {children(state.data)}
      </>
    );
  }
  if (state.error != null) {
    return (
      <div>
        <ErrorNotice error={state.error} />
        <button type="button" className="secondary" onClick={state.reload}>
          Erneut versuchen
        </button>
      </div>
    );
  }
  return <p className="muted">Lädt …</p>;
}

export interface Crumb {
  href?: string;
  label: string;
}

/** Immobilie › Einheit › Mietverhältnis (§9.2). The last crumb is the current page. */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Pfad" className="breadcrumbs">
      <ol>
        {items.map((item, index) => (
          <li key={index}>
            {item.href && index < items.length - 1 ? (
              <Link href={item.href}>{item.label}</Link>
            ) : (
              <span aria-current={index === items.length - 1 ? "page" : undefined}>{item.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export interface Tab<K extends string> {
  key: K;
  label: string;
}

/** Tabs as links (?tab=…), so each tab has a URL, back works, and Tab/Enter reach them. */
export function TabBar<K extends string>({ tabs, current, href }: { tabs: Tab<K>[]; current: K; href: (key: K) => string }) {
  return (
    <nav aria-label="Bereiche" className="tabs">
      {tabs.map((tab) => (
        <Link key={tab.key} href={href(tab.key)} aria-current={tab.key === current ? "page" : undefined} scroll={false}>
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}

/**
 * A button that opens a modal <dialog> (native: focus moves in, Escape closes, focus returns).
 * `children(close)` renders only while open, so a form inside starts fresh each time.
 */
export function ActionDialog({
  label,
  title,
  buttonClass = "secondary",
  disabled,
  children,
}: {
  label: string;
  title: string;
  buttonClass?: string;
  disabled?: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDialogElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (open && dialog && !dialog.open) dialog.showModal();
    // The dialog is unmounted rather than closed, so hand focus back to its button.
    if (!open && wasOpen.current) button.current?.focus();
    wasOpen.current = open;
  }, [open]);

  // Mounted while open only, and portalled to <body>: a form inside the dialog must not be
  // nested in a form around the button in the DOM. React events still bubble along the
  // component tree, so a form inside a dialog stops its submit event (see TenantForm).
  const dialog = open
    ? createPortal(
        <dialog ref={ref} aria-labelledby={titleId} onClose={() => setOpen(false)} className="dialog">
          <div className="stack">
            <div className="row dialog-head">
              <h2 id={titleId}>{title}</h2>
              <span className="spacer" />
              <button type="button" className="link" onClick={() => setOpen(false)}>
                Schließen
              </button>
            </div>
            {children(() => setOpen(false))}
          </div>
        </dialog>,
        document.body,
      )
    : null;

  return (
    <>
      <button ref={button} type="button" className={buttonClass} onClick={() => setOpen(true)} disabled={disabled}>
        {label}
      </button>
      {dialog}
    </>
  );
}

/** A definition list of label/value rows (Stammdaten). */
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** "korrigiert, ursprünglich 15.07.2024" next to a corrected milestone date (§9.4). */
export function CorrectedNote({ original }: { original: string }) {
  return <span className="corrected">korrigiert, ursprünglich {original}</span>;
}

/** A positive outcome line (e.g. "Gespeichert."). */
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice notice-ok" role="status">
      <p>{children}</p>
    </div>
  );
}
