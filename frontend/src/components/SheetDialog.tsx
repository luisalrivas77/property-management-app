"use client";

// An edit form that slides in from the right (phase 10, mockup 25): the same API as ActionDialog
// (a button that opens a modal <dialog>, `children(close)` rendered only while open, so a form
// starts fresh each time), but drawn as a sheet the full height of the screen on the right, as the
// detail panel's sheet is on the phone. It stays a modal dialog at every width, so its role and
// name (the title) are the same as ActionDialog's, and it never takes the detail panel's hash.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useT } from "@/i18n";
import { common } from "@/i18n/common";

import "./sheetDialog.css";

export function SheetDialog({
  label,
  title,
  buttonClass = "secondary",
  disabled,
  initialOpen = false,
  children,
}: {
  label: string;
  title: string;
  buttonClass?: string;
  disabled?: boolean;
  /** Open on mount (a link to a pre-filled form); mount it only once its data has loaded. */
  initialOpen?: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const t = useT(common);
  const [open, setOpen] = useState(initialOpen);
  const ref = useRef<HTMLDialogElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (open && dialog && !dialog.open) dialog.showModal();
    if (!open && wasOpen.current) button.current?.focus();
    wasOpen.current = open;
  }, [open]);

  // Portalled to <body>, so a form inside is never nested in a form around the button.
  const dialog = open
    ? createPortal(
        <dialog
          ref={ref}
          aria-labelledby={titleId}
          onClose={() => setOpen(false)}
          onClick={(event) => {
            // A click on the dimmed page beside the sheet (the dialog's own box).
            if (event.target === event.currentTarget) setOpen(false);
          }}
          className="dialog sheet-dialog"
        >
          <div className="sheet-dialog-inner stack">
            <div className="row dialog-head">
              <h2 id={titleId}>{title}</h2>
              <span className="spacer" />
              <button type="button" className="link" onClick={() => setOpen(false)}>
                {t.close}
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
