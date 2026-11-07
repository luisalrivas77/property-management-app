"use client";

// Form plumbing for §9.5: the API's errors go next to the fields they belong to.
//
// - 422 `validation`: each `detail[].loc` (["body", "address", "postal_code"]) is matched to
//   the longest registered field key that prefixes it ("address.postal_code"); its `msg`
//   appears under that input. Unmatched errors stay in the banner.
// - 422 `rule_violation` / `invalid` with a code the form maps to a field (e.g. `iban.*` →
//   "iban"): the German text appears under that field.
// - Everything else (409 conflict, 404, 500 …) is the banner: the API's `error`/`message`.
// The input is never repeated in any of it; the API does not echo it either (§3.6).
import { cloneElement, useCallback, useMemo, useState, type ReactElement, type ReactNode } from "react";

import { ApiError, ruleCodes, ruleCodeText } from "@/api/errors";
import type { FieldError } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import type { Parsed } from "./input";

export type FieldMessages = Record<string, string[]>;

export interface FormErrors {
  fields: FieldMessages;
  /** What did not fit a field: an ApiError reduced to the unmapped parts, or any other error. */
  banner: unknown;
}

const NONE: FormErrors = { fields: {}, banner: null };

function locPath(loc: FieldError["loc"]): string[] {
  const path = loc.map(String);
  return path[0] === "body" ? path.slice(1) : path;
}

function matchField(path: string[], keys: readonly string[]): string | null {
  let best: string | null = null;
  let bestLength = -1;
  for (const key of keys) {
    const segments = key.split(".");
    if (segments.length > path.length || segments.length <= bestLength) continue;
    if (segments.every((segment, index) => segment === path[index])) {
      best = key;
      bestLength = segments.length;
    }
  }
  return best;
}

function matchCode(code: string, codeFields: Readonly<Record<string, string>>): string | null {
  if (codeFields[code]) return codeFields[code];
  for (const [pattern, field] of Object.entries(codeFields)) {
    if (pattern.endsWith(".") && code.startsWith(pattern)) return field;
  }
  return null;
}

function push(fields: FieldMessages, key: string, message: string): void {
  (fields[key] ??= []).push(message);
}

/** Split an error thrown by a submit into per-field messages and a banner (see above). */
export function splitErrors(
  error: unknown,
  keys: readonly string[],
  codeFields: Readonly<Record<string, string>> = {},
): FormErrors {
  if (!(error instanceof ApiError) || !error.body) return { fields: {}, banner: error };
  const body = error.body;
  const fields: FieldMessages = {};

  const restDetail: FieldError[] = [];
  for (const fieldError of body.detail ?? []) {
    const key = matchField(locPath(fieldError.loc), keys);
    if (key) push(fields, key, fieldError.msg);
    else restDetail.push(fieldError);
  }

  const codes = ruleCodes(body);
  const restCodes: string[] = [];
  for (const code of codes) {
    const key = matchCode(code, codeFields);
    const text = ruleCodeText(code, body.at_number);
    if (key && text) push(fields, key, text);
    else restCodes.push(code);
  }

  const mappedSomething = Object.keys(fields).length > 0;
  if (!mappedSomething) return { fields, banner: error };
  const codesAllMapped = codes.length > 0 && restCodes.length === 0;
  if (restDetail.length === 0 && (codes.length === 0 || codesAllMapped)) return { fields, banner: null };
  const reduced = new ApiError(
    error.status,
    { ...body, detail: restDetail, codes: restCodes, code: restCodes[0] ?? null },
    error.requestId,
  );
  return { fields, banner: reduced };
}

export interface FormState {
  errors: FormErrors;
  /** Messages for one field key. */
  of: (key: string) => string[];
  /** Show a failed submit (any thrown error). */
  fail: (error: unknown) => void;
  /** Client-side format errors (from the input normalisers). */
  invalid: (fields: FieldMessages) => void;
  clear: () => void;
}

/** Error state of one form. `keys` are the body paths its inputs edit ("address.city"). */
export function useFormErrors(keys: readonly string[], codeFields: Readonly<Record<string, string>> = {}): FormState {
  const [errors, setErrors] = useState<FormErrors>(NONE);
  const keyList = keys.join("|");
  const codeList = JSON.stringify(codeFields);
  const fail = useCallback(
    (error: unknown) =>
      setErrors(splitErrors(error, keyList.split("|"), JSON.parse(codeList) as Record<string, string>)),
    [keyList, codeList],
  );
  const invalid = useCallback((fields: FieldMessages) => setErrors({ fields, banner: null }), []);
  const clear = useCallback(() => setErrors(NONE), []);
  return useMemo(
    () => ({ errors, of: (key: string) => errors.fields[key] ?? [], fail, invalid, clear }),
    [errors, fail, invalid, clear],
  );
}

/**
 * Collects the values of normalised inputs. `take` returns the parsed value, or records the
 * message under `key` and returns undefined; `messages` is empty when every input parsed.
 */
export function collector() {
  const messages: FieldMessages = {};
  return {
    messages,
    take<T>(key: string, parsed: Parsed<T>): T | undefined {
      if (parsed.ok) return parsed.value;
      push(messages, key, parsed.message);
      return undefined;
    },
    /** Like take, but an empty input is null (an optional field). */
    optional<T>(key: string, raw: string, parse: (raw: string) => Parsed<T>): T | null | undefined {
      if (raw.trim() === "") return null;
      return this.take(key, parse(raw));
    },
    get ok(): boolean {
      return Object.keys(messages).length === 0;
    },
  };
}

type InputLike = ReactElement<{ id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string }>;

/**
 * The "i" next to a label: a tap or click shows or hides what the field means (no hover-only
 * tooltip, so it works on a phone). It sits outside the <label>, so the input's accessible name
 * stays the label text, and it has no aria-label (tests and screen readers find inputs by
 * label; an aria-label "Erklärung: Baujahr" would match "Baujahr" too). Its name comes from
 * the visually hidden text instead, as on the source badge.
 */
export function InfoToggle({
  open,
  onToggle,
  controls,
  label,
}: {
  open: boolean;
  onToggle: () => void;
  /** The id of the description it shows. */
  controls: string;
  label: ReactNode;
}) {
  return (
    <button type="button" className="info-toggle" aria-expanded={open} aria-controls={controls} onClick={onToggle}>
      <span aria-hidden="true">i</span>
      <span className="visually-hidden">Erklärung: {label}</span>
    </button>
  );
}

/** A field's description, shown by its InfoToggle; always in the DOM so aria-controls points
 * at it, and described-by only while open. */
export function FieldDescription({ id, open, children }: { id: string; open: boolean; children: ReactNode }) {
  return (
    <p className="muted field-description" id={id} hidden={!open}>
      {children}
    </p>
  );
}

/** A labelled input with its messages underneath; wires id, aria-invalid, aria-describedby.
 * `hint` is always shown (a format example, a short note); `description` says what the value
 * is and where to find it, behind the "i" next to the label. */
export function Field({
  id,
  label,
  hint,
  description,
  errors = [],
  children,
}: {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  description?: ReactNode;
  errors?: string[];
  children: InputLike;
}) {
  const [open, setOpen] = useState(false);
  const descriptionId = `${id}-description`;
  const hintId = hint ? `${id}-hint` : null;
  const errorId = errors.length > 0 ? `${id}-error` : null;
  const describedBy = [description && open ? descriptionId : null, hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={errors.length > 0 ? "field field-invalid" : "field"}>
      {description ? (
        <>
          <div className="field-head">
            <label htmlFor={id}>{label}</label>
            <InfoToggle open={open} onToggle={() => setOpen((current) => !current)} controls={descriptionId} label={label} />
          </div>
          <FieldDescription id={descriptionId} open={open}>
            {description}
          </FieldDescription>
        </>
      ) : (
        <label htmlFor={id}>{label}</label>
      )}
      {cloneElement(children, {
        id,
        "aria-invalid": errors.length > 0 ? true : undefined,
        "aria-describedby": describedBy,
      })}
      {hint ? (
        <p className="muted hint" id={hintId ?? undefined}>
          {hint}
        </p>
      ) : null}
      {errors.length > 0 ? (
        <ul className="field-errors" id={errorId ?? undefined}>
          {errors.map((message, index) => (
            <li key={index}>{message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** The banner of a form: what no field took, or a pointer to the marked fields. */
export function FormBanner({ errors }: { errors: FormErrors }) {
  if (errors.banner != null) return <ErrorNotice error={errors.banner} />;
  if (Object.keys(errors.fields).length > 0) {
    return (
      <div className="notice notice-error" role="alert">
        <p>Bitte die markierten Felder prüfen.</p>
      </div>
    );
  }
  return null;
}

/** The submit button of a form, with its busy label. */
export function Submit({ busy, children, busyLabel = "Speichert …" }: { busy: boolean; children: ReactNode; busyLabel?: string }) {
  return (
    <button type="submit" disabled={busy}>
      {busy ? busyLabel : children}
    </button>
  );
}
