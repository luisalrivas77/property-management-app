"use client";

// The Nebenkostenpauschale rise test (phase-4.md D155) outside Planung: the to-dos „… kann
// steigen“ / „… senken“ (Übersicht's „Was noch fehlt“ and the tenancy page), the link that opens
// the tenancy's „Mietänderung“ pre-filled, and the letter to a tenant (D155 (7)).
//
// Every figure here is one GET /properties/{id}/flat-rate-check or GET /tenancies/{id}/
// flat-rate-letter returned (principle 1): the screen formats and links, it computes nothing.
// The letter is rendered by the backend and downloaded, never stored; the sender's address is
// typed here and remembered only in this browser if the user wants (never in the database).
import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { FlatRateCheck, FlatRateLetterFigures, RentChangeReason, TenancyFlatRate } from "@/api/schemas";

import { useFormat, useT, translate } from "@/i18n";
import { flatRateText } from "@/i18n/flatRate";
import { formatter } from "@/i18n/format";

import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { todayIso } from "./format";
import { decimalInput } from "./input";
import { ALLOCATION_CATEGORY } from "./labels";
import type { TodoEntry } from "./todos";
import { ActionDialog, Loading } from "./ui";
import { useLoad } from "./useLoad";

/** The section id of the card on Planung. */
export const FLAT_RATE_CARD_ID = "nebenkostenpauschalen";

/** `?mietaenderung=pauschale` on a tenancy page opens its „Mietänderung“ pre-filled. */
export const RENT_CHANGE_PARAM = "mietaenderung";
export const RENT_CHANGE_PAUSCHALE = "pauschale";

const ZERO = /^-?0*(\.0*)?$/;

function isZero(value: string): boolean {
  return ZERO.test(value);
}

/** "+12,34 €", "-12,34 €", "0,00 €": the sign of a backend figure, formatted. */
function signed(value: string): string {
  const f = formatter();
  if (isZero(value)) return f.money(value.replace(/^-/, ""));
  return value.startsWith("-") ? f.money(value) : `+${f.money(value)}`;
}

/** A fall as the amount it falls by ("-12.34" → "12,34 €"), formatting only. */
function unsigned(value: string): string {
  return formatter().money(value.replace(/^-/, ""));
}

export function flatRateCardHref(propertyId: string): string {
  // On the property page beside the tenancies since phase 10 §7.6 (on Planung until then).
  return `/properties/${propertyId}#${FLAT_RATE_CARD_ID}`;
}

export function rentChangeHref(tenancyId: string): string {
  return `/tenancies/${tenancyId}?${RENT_CHANGE_PARAM}=${RENT_CHANGE_PAUSCHALE}`;
}

/** „Hausgeld, umlagefähig +18,00 €, Versicherung +4,50 €“: the BetrKV categories that changed
 * for the whole flat since the Pauschale was set (electricity never counts, D155 (3)). */
function reasonText(tenancy: TenancyFlatRate): string {
  return tenancy.changes
    .filter((change) => change.category !== "electricity" && !isZero(change.change))
    .map((change) => `${ALLOCATION_CATEGORY[change.category]} ${signed(change.change)}`)
    .join(", ");
}

/** The to-dos of one property's check: one per tenancy whose verdict is raise or lower.
 * `onlyTenancyId` keeps the one of a tenancy page. */
export function flatRateTodos(
  check: FlatRateCheck | null,
  { propertyId, context, onlyTenancyId }: { propertyId: string; context?: string; onlyTenancyId?: string },
): TodoEntry[] {
  if (!check) return [];
  const t = translate(flatRateText);
  const f = formatter();
  const entries: TodoEntry[] = [];
  for (const tenancy of check.tenancies ?? []) {
    if (tenancy.verdict === "none") continue;
    if (onlyTenancyId && tenancy.tenancy_id !== onlyTenancyId) continue;
    const reasons = reasonText(tenancy);
    const since = t.since(f.date(tenancy.set_on));
    const text =
      tenancy.verdict === "raise"
        ? t.raiseText(since, reasons, signed(tenancy.rise), f.date(tenancy.earliest_start), t.notAdvice)
        : t.lowerText(since, reasons, unsigned(tenancy.rise), f.date(tenancy.fell_on), t.notAdvice);
    entries.push({
      key: `flat-rate-${tenancy.tenancy_id}`,
      title: t.todoTitle(tenancy.unit_label, tenancy.verdict === "raise"),
      text,
      action: <FlatRateActions tenancyId={tenancy.tenancy_id} propertyId={propertyId} />,
      context,
    });
  }
  return entries;
}

/** The to-do's buttons: the pre-filled „Mietänderung“, the letter and the card with sources. */
export function FlatRateActions({ tenancyId, propertyId, withCard = true }: { tenancyId: string; propertyId: string; withCard?: boolean }) {
  const t = useT(flatRateText);
  return (
    <>
      <Link href={rentChangeHref(tenancyId)}>{t.rentChange}</Link>
      <FlatRateLetterButton tenancyId={tenancyId} />
      {withCard ? <Link href={flatRateCardHref(propertyId)}>{t.costsAndSources}</Link> : null}
    </>
  );
}

/** The values that pre-fill „Mietänderung“ from the letter's figures of today (the backend's
 * new Pauschale and start, never computed here); null when there is nothing to change. */
export interface PauschaleChange {
  effectiveFrom: string;
  prepayment: string;
  reason: RentChangeReason;
}

export function loadPauschaleChange(tenancyId: string): Promise<PauschaleChange | null> {
  return ok(api.GET("/api/tenancies/{tenancy_id}/flat-rate-letter", { params: { path: { tenancy_id: tenancyId } } })).then(
    (figures): PauschaleChange => ({
      effectiveFrom: figures.starts_on,
      prepayment: decimalInput(figures.new_pauschale),
      // A fall is not a Mieterhöhung; of the reasons a rent row knows, „Vereinbarung“ fits best.
      reason: figures.kind === "raise" ? "rent_increase" : "agreement",
    }),
    () => null,
  );
}

// --- The letter (D155 (7)) --------------------------------------------------------------------

const ADDRESS_KEY = "hausbuch.flatRateLetter.senderAddress";
const MAX_LINE = 120;
const MAX_ADDRESS_LINES = 4;

/** The sender's address remembered in this browser; empty when storage is unavailable. */
function rememberedAddress(): string {
  try {
    return window.localStorage.getItem(ADDRESS_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberAddress(value: string | null): void {
  try {
    if (value) window.localStorage.setItem(ADDRESS_KEY, value);
    else window.localStorage.removeItem(ADDRESS_KEY);
  } catch {
    // Storage blocked (private window, site data off): nothing is remembered, nothing breaks.
  }
}

/** `attachment; filename="…"; filename*=UTF-8''…` → the name. */
function fileName(disposition: string | null): string {
  const encoded = disposition ? /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1] : undefined;
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      // fall through to the ASCII name
    }
  }
  const plain = disposition ? /filename="([^"]+)"/i.exec(disposition) : null;
  return plain?.[1] ?? "Nebenkostenpauschale.pdf";
}

function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function FlatRateLetterButton({ tenancyId }: { tenancyId: string }) {
  const t = useT(flatRateText);
  return (
    <ActionDialog label={t.writeLetter} title={t.letterTitle} buttonClass="secondary">
      {() => <LetterForm tenancyId={tenancyId} />}
    </ActionDialog>
  );
}

const LETTER_KEYS = ["sent_on", "sender_name", "sender_address"] as const;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const loadOwners = () => ok(api.GET("/api/owners"));

function LetterForm({ tenancyId }: { tenancyId: string }) {
  const t = useT(flatRateText);
  const [sentOn, setSentOn] = useState(todayIso());
  const [typedName, setTypedName] = useState<string | null>(null);
  const [address, setAddress] = useState(rememberedAddress);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const form = useFormErrors(LETTER_KEYS);

  const owners = useLoad(loadOwners);
  const ownerNames = (owners.data ?? []).map((owner) => owner.full_name).join(" und ");
  const name = typedName ?? ownerNames;

  const loadFigures = useCallback(
    (): Promise<FlatRateLetterFigures | null> =>
      DATE.test(sentOn)
        ? ok(
            api.GET("/api/tenancies/{tenancy_id}/flat-rate-letter", {
              params: { path: { tenancy_id: tenancyId }, query: { sent_on: sentOn } },
            }),
          )
        : Promise.resolve(null),
    [tenancyId, sentOn],
  );
  const figures = useLoad(loadFigures);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const lines = address
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (!DATE.test(sentOn)) values.messages.sent_on = [t.dateRequired];
    if (!name.trim()) values.messages.sender_name = [t.senderRequired];
    else if (name.trim().length > MAX_LINE) values.messages.sender_name = [t.maxChars(MAX_LINE)];
    if (lines.length > MAX_ADDRESS_LINES) values.messages.sender_address = [t.maxLines(MAX_ADDRESS_LINES)];
    else if (lines.some((line) => line.length > MAX_LINE)) values.messages.sender_address = [t.maxCharsPerLine(MAX_LINE)];
    if (Object.keys(values.messages).length > 0) return form.invalid(values.messages);
    setBusy(true);
    setDone(null);
    form.clear();
    try {
      const { data, response } = await api.POST("/api/tenancies/{tenancy_id}/flat-rate-letter", {
        params: { path: { tenancy_id: tenancyId } },
        body: { sent_on: sentOn, sender_name: name.trim(), sender_address: lines },
        parseAs: "blob",
      });
      rememberAddress(remember ? lines.join("\n") : null);
      const file = fileName(response.headers.get("Content-Disposition"));
      download(data as Blob, file);
      setDone(file);
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <Loading state={figures}>{(data) => (data ? <LetterPreview figures={data} /> : null)}</Loading>
      <Field
        id="flat-rate-letter-sent-on"
        label={t.sentOn}
        description={t.sentOnDescription}
        errors={form.of("sent_on")}
      >
        <input type="date" value={sentOn} onChange={(e) => setSentOn(e.target.value)} required />
      </Field>
      <Field
        id="flat-rate-letter-name"
        label={t.sender}
        description={t.senderDescription}
        errors={form.of("sender_name")}
      >
        <input type="text" value={name} onChange={(e) => setTypedName(e.target.value)} maxLength={MAX_LINE} autoComplete="name" />
      </Field>
      <Field
        id="flat-rate-letter-address"
        label={t.senderAddress}
        hint={t.senderAddressHint}
        description={t.senderAddressDescription}
        errors={form.of("sender_address")}
      >
        <textarea rows={3} value={address} onChange={(e) => setAddress(e.target.value)} autoComplete="street-address" />
      </Field>
      <label className="inline">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> {t.rememberAddress}
      </label>
      <Submit busy={busy} busyLabel={t.creating}>
        {t.createPdf}
      </Submit>
      {done ? <p className="muted">{t.downloaded(done)}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** What the letter will say, before the PDF is made: the new Pauschale, the start, the reasons. */
function LetterPreview({ figures }: { figures: FlatRateLetterFigures }) {
  const t = useT(flatRateText);
  const f = useFormat();
  const raise = figures.kind === "raise";
  return (
    <div className="stack">
      <p>
        <strong>
          {t.previewHeading(raise, figures.unit_label, f.money(figures.pauschale), f.money(figures.new_pauschale), f.date(figures.starts_on))}
        </strong>
      </p>
      <p className="muted">
        {t.previewText(
          f.money(figures.new_total),
          f.money(figures.cold_rent),
          figures.reasons.map((reason) => `${ALLOCATION_CATEGORY[reason.category]} ${signed(reason.change)}`).join(", "),
          f.decimal(figures.share_percent, 1),
        )}
      </p>
      <p className="muted">{t.notAdvice}</p>
    </div>
  );
}
