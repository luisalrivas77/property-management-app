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

import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDate, formatDecimal, formatMoney, todayIso } from "./format";
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

/** The one sentence that says this is information, not advice (D155 (6)). */
export const FLAT_RATE_NOT_ADVICE =
  "Ob und ab wann eine Pauschale steigt oder sinkt, entscheidet § 560 BGB; die erste Erhöhung lohnt eine Prüfung beim Vermieterverein.";

const ZERO = /^-?0*(\.0*)?$/;

function isZero(value: string): boolean {
  return ZERO.test(value);
}

/** "+12,34 €", "-12,34 €", "0,00 €": the sign of a backend figure, formatted. */
function signed(value: string): string {
  if (isZero(value)) return formatMoney(value.replace(/^-/, ""));
  return value.startsWith("-") ? formatMoney(value) : `+${formatMoney(value)}`;
}

/** A fall as the amount it falls by ("-12.34" → "12,34 €"), formatting only. */
function unsigned(value: string): string {
  return formatMoney(value.replace(/^-/, ""));
}

export function flatRateCardHref(propertyId: string): string {
  return `/planning?immobilie=${propertyId}#${FLAT_RATE_CARD_ID}`;
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
  const entries: TodoEntry[] = [];
  for (const tenancy of check.tenancies ?? []) {
    if (tenancy.verdict === "none") continue;
    if (onlyTenancyId && tenancy.tenancy_id !== onlyTenancyId) continue;
    const reasons = reasonText(tenancy);
    const since = `seit die Pauschale am ${formatDate(tenancy.set_on)} festgelegt wurde`;
    const text =
      tenancy.verdict === "raise"
        ? `Die umlagefähigen Kosten der Wohnung sind gestiegen, ${since}${reasons ? ` (${reasons})` : ""}. ` +
          `Auf dieses Zimmer entfallen ${signed(tenancy.rise)} pro Monat; frühester Beginn ist der ` +
          `${formatDate(tenancy.earliest_start)}, wenn das Schreiben diesen Monat zugeht. ` +
          `„Schreiben erstellen“ macht den Brief an den Mieter, „Mietänderung“ trägt die neue Pauschale ein. ${FLAT_RATE_NOT_ADVICE}`
        : `Die umlagefähigen Kosten der Wohnung sind gesunken, ${since}${reasons ? ` (${reasons})` : ""}. ` +
          `Die Pauschale dieses Zimmers sinkt um ${unsigned(tenancy.rise)} pro Monat, ab dem ${formatDate(tenancy.fell_on)}, ` +
          `und der Mieter ist unverzüglich zu informieren (§ 560 Abs. 3 BGB). ` +
          `„Schreiben erstellen“ macht den Brief, „Mietänderung“ trägt die neue Pauschale ein. ${FLAT_RATE_NOT_ADVICE}`;
    entries.push({
      key: `flat-rate-${tenancy.tenancy_id}`,
      title: `Nebenkostenpauschale ${tenancy.unit_label} ${tenancy.verdict === "raise" ? "kann steigen" : "senken"}`,
      text,
      action: <FlatRateActions tenancyId={tenancy.tenancy_id} propertyId={propertyId} />,
      context,
    });
  }
  return entries;
}

/** The to-do's buttons: the pre-filled „Mietänderung“, the letter and the card with sources. */
export function FlatRateActions({ tenancyId, propertyId, withCard = true }: { tenancyId: string; propertyId: string; withCard?: boolean }) {
  return (
    <>
      <Link href={rentChangeHref(tenancyId)}>Mietänderung</Link>
      <FlatRateLetterButton tenancyId={tenancyId} />
      {withCard ? <Link href={flatRateCardHref(propertyId)}>Kosten und Quellen ansehen</Link> : null}
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
  return (
    <ActionDialog label="Schreiben erstellen" title="Schreiben an den Mieter" buttonClass="secondary">
      {() => <LetterForm tenancyId={tenancyId} />}
    </ActionDialog>
  );
}

const LETTER_KEYS = ["sent_on", "sender_name", "sender_address"] as const;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const loadOwners = () => ok(api.GET("/api/owners"));

function LetterForm({ tenancyId }: { tenancyId: string }) {
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
    if (!DATE.test(sentOn)) values.messages.sent_on = ["Bitte das Datum eingeben."];
    if (!name.trim()) values.messages.sender_name = ["Bitte den Namen des Absenders eingeben."];
    else if (name.trim().length > MAX_LINE) values.messages.sender_name = [`Höchstens ${MAX_LINE} Zeichen.`];
    if (lines.length > MAX_ADDRESS_LINES) values.messages.sender_address = [`Höchstens ${MAX_ADDRESS_LINES} Zeilen.`];
    else if (lines.some((line) => line.length > MAX_LINE)) values.messages.sender_address = [`Höchstens ${MAX_LINE} Zeichen je Zeile.`];
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
        label="Datum des Schreibens"
        description="Der Tag, an dem das Schreiben dem Mieter zugeht. Eine Erhöhung gilt ab dem übernächsten Monatsersten danach (§ 560 Abs. 2 BGB)."
        errors={form.of("sent_on")}
      >
        <input type="date" value={sentOn} onChange={(e) => setSentOn(e.target.value)} required />
      </Field>
      <Field
        id="flat-rate-letter-name"
        label="Absender"
        description="Der oder die Vermieter, wie im Mietvertrag. Vorbelegt mit den Eigentümern."
        errors={form.of("sender_name")}
      >
        <input type="text" value={name} onChange={(e) => setTypedName(e.target.value)} maxLength={MAX_LINE} autoComplete="name" />
      </Field>
      <Field
        id="flat-rate-letter-address"
        label="Anschrift des Absenders"
        hint="Bis zu vier Zeilen, z. B. Straße und Hausnummer, Postleitzahl und Ort."
        description="Wird nur auf das Schreiben gedruckt und nicht in Hausbuch gespeichert."
        errors={form.of("sender_address")}
      >
        <textarea rows={3} value={address} onChange={(e) => setAddress(e.target.value)} autoComplete="street-address" />
      </Field>
      <label className="inline">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Anschrift in diesem Browser
        merken
      </label>
      <Submit busy={busy} busyLabel="Erstellt …">
        PDF erstellen
      </Submit>
      {done ? <p className="muted">„{done}“ heruntergeladen; Hausbuch speichert das Schreiben nicht.</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** What the letter will say, before the PDF is made: the new Pauschale, the start, the reasons. */
function LetterPreview({ figures }: { figures: FlatRateLetterFigures }) {
  const raise = figures.kind === "raise";
  return (
    <div className="stack">
      <p>
        <strong>
          {raise ? "Erhöhung" : "Herabsetzung"} der Nebenkostenpauschale {figures.unit_label}: {formatMoney(figures.pauschale)} →{" "}
          {formatMoney(figures.new_pauschale)} pro Monat, ab dem {formatDate(figures.starts_on)}.
        </strong>
      </p>
      <p className="muted">
        Neue Gesamtmiete {formatMoney(figures.new_total)} (Kaltmiete {formatMoney(figures.cold_rent)} und Pauschale). Begründung im
        Schreiben:{" "}
        {figures.reasons.map((reason) => `${ALLOCATION_CATEGORY[reason.category]} ${signed(reason.change)}`).join(", ")} pro Monat
        für die Wohnung, davon {formatDecimal(figures.share_percent, 1)} % für dieses Zimmer.
      </p>
      <p className="muted">{FLAT_RATE_NOT_ADVICE}</p>
    </div>
  );
}
