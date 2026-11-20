"use client";

// The detail panel (phase 10 D267): a tapped row or card opens its details beside the list
// without leaving the page. From 1100 px the panel is a column on the right of the list; below
// that it slides in from the right over the dimmed page (a modal <dialog>). Its state is the URL
// hash `#d-<kind>-<id>` (lib/detailHash.ts), so the browser's back button closes it, a reload
// or a shared link opens it again, and „Zurück“, ✕, Escape and a swipe to the right close it.
// Focus moves to the panel's title when it opens and back to the row when it closes.
//
// Usage on a screen (one kind per list):
//
//   const detail = useDetail("tenancy");                    // hash #d-tenancy-<id>
//   const selected = rows.find((row) => row.id === detail.id);
//   <DetailLayout
//     panel={selected ? (
//       <DetailPanel title={selected.label} backLabel={t.title} onClose={detail.close}>
//         … the details, and a link to the full page …
//       </DetailPanel>
//     ) : null}
//   >
//     {rows.map((row) => <a key={row.id} {...detail.linkProps(row.id)} className="…">{row.label}</a>)}
//   </DetailLayout>
//
// A row is a real link (`href="#d-tenancy-<id>"`), so it opens in a new tab too; linkProps adds
// the click handler that opens the panel without a scroll jump and marks the open row
// (aria-current). Render the panel only when its record has loaded; the layout keeps the list
// full width until then.
import {
  type MouseEvent,
  type ReactNode,
  type TouchEvent,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useSyncExternalStore,
} from "react";

import { useT } from "@/i18n";
import { shell } from "@/i18n/shell";
import { closeDetail, detailHref, detailIdOf, openDetail, useDetailHash } from "@/lib/detailHash";
import { Icon } from "@/lib/icons";

import "./detailPanel.css";

/** Where the panel becomes a column beside the list (D267). */
const WIDE = "(min-width: 1100px)";

function subscribeWide(listener: () => void): () => void {
  const query = window.matchMedia(WIDE);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

function useWide(): boolean {
  return useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia(WIDE).matches,
    () => true,
  );
}

export interface Detail {
  /** The id of the open record of this kind, or null. */
  id: string | null;
  open(id: string): void;
  close(): void;
  href(id: string): string;
  /** Props for the row's <a>: the hash link, the click that opens the panel, aria-current. */
  linkProps(id: string): {
    href: string;
    onClick: (event: MouseEvent<HTMLAnchorElement>) => void;
    "aria-current": "true" | undefined;
  };
}

/** The panel's state for one kind of record; `kind` is lower-case letters and digits. */
export function useDetail(kind: string): Detail {
  const id = detailIdOf(useDetailHash(), kind);
  return {
    id,
    open: (next) => openDetail(kind, next),
    close: closeDetail,
    href: (next) => detailHref(kind, next),
    linkProps: (next) => ({
      href: detailHref(kind, next),
      onClick: (event) => {
        // A new tab or window (Ctrl, ⌘, Shift, middle click) keeps the browser's own handling.
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        openDetail(kind, next);
      },
      "aria-current": id === next ? "true" : undefined,
    }),
  };
}

/** The list and, when `panel` is given, the panel beside it (on the computer). */
export function DetailLayout({ panel, children }: { panel: ReactNode; children: ReactNode }) {
  return (
    <div className="detail-layout" data-open={panel ? "" : undefined}>
      <div className="detail-main">{children}</div>
      {panel}
    </div>
  );
}

export function DetailPanel({
  title,
  backLabel,
  onClose,
  children,
}: {
  title: ReactNode;
  /** The list's name for „‹ …“ on the phone, e.g. „Übersicht“; „Zurück“ without it. */
  backLabel?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const t = useT(shell);
  const wide = useWide();
  const titleId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const onCloseRef = useRef(onClose);
  const returnTo = useRef<HTMLElement | null>(null);
  const swipe = useRef<{ x: number; y: number } | null>(null);

  useLayoutEffect(() => {
    onCloseRef.current = onClose;
  });

  // The row that opened the panel gets the focus back when it closes.
  useLayoutEffect(() => {
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body) returnTo.current = active;
    return () => {
      const target = returnTo.current;
      // After the commit: while the modal sheet is still in the document the page under it is
      // inert and would refuse the focus.
      queueMicrotask(() => {
        if (target?.isConnected) target.focus({ preventScroll: true });
      });
    };
  }, []);

  // Open: the dialog on the phone, then the focus on the title.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!wide && dialog && !dialog.open) dialog.showModal();
    titleRef.current?.focus({ preventScroll: wide });
  }, [wide]);

  // The phone: the page under the sheet does not scroll.
  useEffect(() => {
    if (wide) return;
    const root = document.documentElement;
    const previous = root.style.overflow;
    root.style.overflow = "hidden";
    return () => {
      root.style.overflow = previous;
    };
  }, [wide]);

  // The computer: Escape closes the panel, unless something over it handled the key first (the
  // menu prevents it) or a dialog is open over it. A component with its own Escape over the
  // panel calls event.preventDefault().
  useEffect(() => {
    if (!wide) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (document.querySelector("dialog[open]")) return;
      onCloseRef.current();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [wide]);

  function onTouchStart(event: TouchEvent) {
    const touch = event.touches[0];
    swipe.current = event.touches.length === 1 && touch ? { x: touch.clientX, y: touch.clientY } : null;
  }

  function onTouchEnd(event: TouchEvent) {
    const start = swipe.current;
    const touch = event.changedTouches[0];
    swipe.current = null;
    if (!start || !touch) return;
    const dx = touch.clientX - start.x;
    const dy = Math.abs(touch.clientY - start.y);
    if (dx > 80 && dy < dx / 2) onCloseRef.current();
  }

  const body = (
    <>
      <div className="detail-head">
        {!wide ? (
          <button type="button" className="detail-back" onClick={onClose}>
            <Icon name="chevronLeft" size={18} />
            {backLabel ?? t.panelBack}
          </button>
        ) : null}
        <button type="button" className="detail-close" onClick={onClose} aria-label={t.panelClose}>
          <Icon name="close" size={18} />
        </button>
      </div>
      <h2 id={titleId} ref={titleRef} tabIndex={-1} className="detail-title">
        {title}
      </h2>
      <div className="detail-body">{children}</div>
    </>
  );

  if (wide) {
    return (
      <aside className="detail-panel" aria-labelledby={titleId}>
        {body}
      </aside>
    );
  }
  return (
    <dialog
      ref={dialogRef}
      className="detail-sheet"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={() => onCloseRef.current()}
      onClick={(event) => {
        // A tap on the dimmed page beside the sheet (the dialog's own box, outside its content).
        if (event.target === event.currentTarget) onClose();
      }}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <div className="detail-sheet-inner">{body}</div>
    </dialog>
  );
}
