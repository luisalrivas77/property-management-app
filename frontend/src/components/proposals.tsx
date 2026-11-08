"use client";

// Vorschlag (docs/phases/phase-2.md §6.4): the pre-fill review on the document page.
//
// - Finds the document's current proposal (GET /documents/{id}/proposals, newest first) and
//   polls GET /proposals/{id} every 2 s while it is queued or running; polling stops on any
//   other status and on unmount.
// - `ready`: one card per record. Each row has the German label, the proposed value in an
//   input of the field's type, a confidence dot (computed by code from the checks, D15), the
//   page badge with the quote (keyboard and tap: a toggle; mouse: its title) and the link that
//   opens the original at that page, and for an existing record its current value with
//   "übernehmen / behalten". A record that creates shows every field of its *Create model
//   (CREATE_FIELDS), not only the proposed ones: the AI pre-fills what it found, the rest is
//   typed by hand (Luis, 7 Nov 2026). A tenancy's unit and a Kaution's tenancy are picked from
//   the workspace's units and tenancies, preselected with the one code matched.
//   Nothing is saved before "Übernehmen" (D14): apply sends every
//   record with the final values and the per-field decisions; a 422's `loc` starts with the
//   record key and lands at its field.
// - `failed` / `blocked_tenant_data`: the reason in German and a "Von Hand eingeben" link. A
//   blocked document offers "Neu auslesen" once the household lets the AI read tenant documents
//   (GET /workspace/settings, read only while a blocked proposal is shown; D42): the re-run
//   supersedes the blocked proposal and queues a read with the tenant flag kept.
//
// Every euro figure shown is the backend's: values are only reformatted (decimal strings moved
// between "1400.50" and "1.400,50"), never computed here (principle 1). A derived value names
// the fields code computed it from, as the backend gives them.
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { aiErrorText } from "@/api/errors";
import type {
  AppliedEntity,
  DocumentDetail,
  ExtractedValue,
  ExtractionKind,
  FieldDecision,
  Fraction,
  Proposal,
  ProposalApplyRecord,
  ProposalApplyResult,
  ProposalRecord,
  ProposalStatus,
  SourceRef,
} from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { NO_TAX_YEAR } from "./documents";
import type { Directory } from "./entities";
import { targets, tenantName } from "./entities";
import { FieldDescription, FormBanner, InfoToggle, useFormErrors, type FieldMessages } from "./form";
import { formatArea, formatDate, formatDateTime, formatFraction, formatMoney, formatPercent, formatShare } from "./format";
import { decimalInput, percentInput, toDecimalString, toFraction, toMoneyString, toRate, toYear, type Parsed } from "./input";
import {
  ACQUISITION_COST_KIND,
  CHECK,
  CONFIDENCE,
  DEPOSIT_FORM,
  DOCUMENT_SUBTYPE,
  DOCUMENT_TYPE,
  EVENT_TYPE,
  EXTRACTION_KIND,
  LINK_TARGET,
  options,
  PROPERTY_KIND,
  RECORD_MODE,
  RENT_MODEL,
} from "./labels";
import { Notice } from "./ui";

const POLL_MS = 2_000;
const IN_FLIGHT: readonly ProposalStatus[] = ["queued", "running"];

// --- what each field is -----------------------------------------------------------------

/** "unit" / "tenancy": the id of one of the workspace's units / tenancies, picked from a list.
 * "bool": a checkbox; ticked is sent as true, unticked is left out (the *Create default, false). */
type FieldKind = "text" | "money" | "date" | "year" | "rate" | "area" | "fraction" | "enum" | "fixed" | "unit" | "tenancy" | "bool";

interface FieldSpec {
  label: string;
  kind: FieldKind;
  choices?: Readonly<Record<string, string>>;
  /** What the value is and where it stands, behind the "i" next to the label. */
  description?: string;
  /** An "area" value's decimal places, when not 2 (a unit's rooms: 1, as the backend's Rooms). */
  decimals?: number;
}

const text = (label: string, description?: string): FieldSpec => ({ label, kind: "text", description });
const date = (label: string, description?: string): FieldSpec => ({ label, kind: "date", description });
const money = (label: string, description?: string): FieldSpec => ({ label, kind: "money", description });
const choice = (label: string, choices: Readonly<Record<string, string>>, description?: string): FieldSpec => ({
  label,
  kind: "enum",
  choices,
  description,
});

const PROPERTY_FIELDS: Record<string, FieldSpec> = {
  label: text(
    "Bezeichnung",
    "Ein frei wählbarer Name, unter dem Sie die Immobilie in Hausbuch wiederfinden. Leer gelassen heißt sie nach Straße und Hausnummer.",
  ),
  kind: choice(
    "Art",
    PROPERTY_KIND,
    "Eigentumswohnung, Einfamilien- oder Mehrfamilienhaus, wie im Kaufvertrag beschrieben. Bei einer Eigentumswohnung fragt die Unterlagen-Checkliste zusätzlich nach Wirtschaftsplan und Hausgeldabrechnung.",
  ),
  "address.street": text("Straße", "Die Straße der Immobilie, wie sie im Kaufvertrag steht."),
  "address.house_number": text("Hausnummer", "Die Hausnummer mit Zusatz, wie im Kaufvertrag, z. B. 12a oder 3-5, ohne die Wohnungsnummer (z. B. WE 03): die gehört in die Bezeichnung der Einheit. Mit Straße und Hausnummer findet Hausbuch die Immobilie in hochgeladenen Dokumenten wieder."),
  "address.postal_code": text("PLZ", "Die fünfstellige Postleitzahl der Immobilie."),
  "address.city": text("Ort", "Die Stadt oder Gemeinde, in der die Immobilie liegt."),
  build_year: {
    label: "Baujahr",
    kind: "year",
    description:
      "Das Jahr, in dem das Gebäude fertiggestellt wurde, laut Kaufvertrag, Exposé oder Energieausweis. Danach richtet sich der AfA-Satz: vor 1925 2,5 %, von 1925 bis 2022 2 %, ab 2023 3 % im Jahr.",
  },
  notarized_on: date(
    "Notartermin (Kaufvertrag beurkundet)",
    "Der Tag, an dem der Kaufvertrag beim Notar beurkundet wurde: das Datum der Urkunde. Ab diesem Tag laufen die zehn Jahre der Spekulationsfrist, nach denen ein Verkauf steuerfrei ist.",
  ),
  purchase_date: date(
    "Übergang Besitz, Nutzen und Lasten",
    "Der Tag, an dem die Immobilie laut Kaufvertrag auf Sie überging, meist nach Zahlung des Kaufpreises. Ab diesem Monat läuft die AfA; ab diesem Jahr fragt die Unterlagen-Checkliste nach Unterlagen.",
  ),
  purchase_price: money(
    "Kaufpreis (€)",
    "Der Kaufpreis laut Kaufvertrag, ohne Nebenkosten wie Grunderwerbsteuer, Notar oder Makler; die stehen als eigene Einträge unter „Nebenkosten“.",
  ),
};

/** By record key prefix, then by the *Create / *Patch field name the backend uses. */
const FIELDS: Record<string, Record<string, FieldSpec>> = {
  typing: {
    type: choice(
      "Typ des Dokuments",
      DOCUMENT_TYPE,
      "Welche Art Dokument das ist, z. B. Rechnung, Kontoauszug oder Bescheid. Danach richtet sich, welche Werte gelesen werden und wofür das Dokument in der Unterlagen-Checkliste zählt.",
    ),
    subtype: choice(
      "Untertyp",
      DOCUMENT_SUBTYPE,
      "Genauer als der Typ, z. B. Kaufvertrag, Mietvertrag oder Grundsteuerbescheid. Danach richten sich die Aufbewahrungsfrist und die Unterlagen-Checkliste.",
    ),
    title: text("Titel", "Ein kurzer Name, unter dem Sie das Dokument wiederfinden."),
    document_date: date(
      "Datum des Dokuments",
      "Das Datum, das auf dem Dokument steht, z. B. das Rechnungs- oder Bescheiddatum. Ohne Steuerjahr zählt die Aufbewahrungsfrist ab dem Jahr dieses Datums.",
    ),
    tax_year: {
      label: "Steuerjahr",
      kind: "year",
      description:
        "Das Jahr, für das das Dokument gilt, z. B. 2026 bei der Zinsbescheinigung 2026. Die Unterlagen-Checkliste ordnet Jahresunterlagen danach zu, und die Aufbewahrungsfrist zählt ab diesem Jahr.",
    },
  },
  property: PROPERTY_FIELDS,
  correction: PROPERTY_FIELDS,
  unit: {
    label: text("Bezeichnung", "Wie die Einheit in Teilungserklärung oder Kaufvertrag heißt, z. B. „WE 3“ für Wohnung Nr. 3 oder ein Stellplatz."),
    mea: {
      label: "MEA (Miteigentumsanteil)",
      kind: "fraction",
      description:
        "Ihr Anteil am gemeinschaftlichen Eigentum der WEG (Grundstück, Dach, Treppenhaus …), laut Teilungserklärung oder Kaufvertrag, z. B. 56,18/1.000. Nach ihm verteilt die WEG meist die gemeinsamen Kosten (Hausgeld). Bei einzeln vermieteten Zimmern einer Wohnung leer lassen.",
    },
    floor_area_m2: {
      label: "Fläche (m²)",
      kind: "area",
      description:
        "Die Wohnfläche in Quadratmetern laut Mietvertrag, Kaufvertrag oder Wohnflächenberechnung. Hausbuch rechnet noch nicht damit; gebraucht wird sie später z. B. für die Betriebskostenabrechnung.",
    },
    rooms: { label: "Zimmer", kind: "area", decimals: 1, description: "Die Zahl der Zimmer laut Mietvertrag oder Exposé; ein halbes Zimmer als ,5." },
  },
  price_split: {
    building_share: {
      label: "Gebäudeanteil (%)",
      kind: "rate",
      description:
        "Der Anteil des Kaufpreises, der auf das Gebäude entfällt; der Rest ist Grund und Boden, der nicht abgeschrieben wird. Der Gebäudeanteil ist die Grundlage der AfA.",
    },
    note: text("Notiz", "Freier Text für Sie, z. B. wie der Wert zustande kam."),
  },
  ownership: {
    shares: {
      label: "Eigentumsanteile",
      kind: "fixed",
      description:
        "Wem die Immobilie zu welchem Anteil gehört, laut Kaufvertrag (z. B. „je zur Hälfte“). Hausbuch hat die Käufer im Vertrag den angelegten Eigentümern über den Namen zugeordnet.",
    },
    effective_date: date("Gültig ab", "Ab wann diese Anteile gelten. Leer beim Kauf; ein Datum nur, wenn sich die Anteile später ändern."),
  },
  cost: {
    kind: choice(
      "Art",
      ACQUISITION_COST_KIND,
      "Welche Erwerbsnebenkosten das sind: Grunderwerbsteuer (Bescheid des Finanzamts), Notar, Grundbuchamt, Makler oder Sonstige.",
    ),
    amount: money(
      "Betrag (€)",
      "Der Betrag laut Rechnung oder Bescheid. Erwerbsnebenkosten zählen zu den Anschaffungskosten; ihr Gebäudeanteil gehört zur AfA-Grundlage.",
    ),
    paid_on: date("Bezahlt am", "Der Tag der Zahlung, laut Kontoauszug."),
    note: text("Notiz", "Freier Text für Sie, z. B. eine Rechnungsnummer."),
  },
  loan: {
    lender: text(
      "Bank",
      "Der Darlehensgeber laut Darlehensvertrag. Steht dieser Name im Kontoauszug als Empfänger, erkennt Hausbuch daran auch die Raten.",
    ),
    contract_number: text("Vertragsnummer", "Die Darlehens- oder Vertragsnummer laut Darlehensvertrag; sie unterscheidet mehrere Darlehen derselben Bank."),
    principal: money(
      "Darlehenssumme (€)",
      "Der Darlehensbetrag laut Darlehensvertrag (Nennbetrag). Aus ihm, dem Zins und der Rate rechnet Hausbuch den Tilgungsplan.",
    ),
    nominal_rate: {
      label: "Nominalzins (% p. a.)",
      kind: "rate",
      description:
        "Der gebundene Sollzins pro Jahr laut Darlehensvertrag, nicht der effektive Jahreszins. Mit ihm rechnet der Tilgungsplan den Zinsanteil jeder Rate.",
    },
    monthly_payment: money(
      "Rate (€ / Monat)",
      "Die monatliche Rate aus Zins und Tilgung laut Darlehensvertrag. Eine Abbuchung in dieser Höhe teilt Hausbuch nach dem Tilgungsplan in Zinsen und Tilgung auf.",
    ),
    fixed_until: date(
      "Zinsbindung bis",
      "Das Ende der Sollzinsbindung laut Darlehensvertrag. Danach rechnet der Tilgungsplan mit dem Anschlusszins.",
    ),
    follow_up_rate: {
      label: "Anschlusszins (% p. a.)",
      kind: "rate",
      description:
        "Ihre Annahme für den Zins nach der Zinsbindung, solange keine Anschlussfinanzierung vereinbart ist. Leer: der Tilgungsplan rechnet mit dem Nominalzins weiter.",
    },
    disbursed_on: date(
      "Auszahlung am",
      "Der Tag, an dem die Bank das Darlehen ausgezahlt hat, laut Kontoauszug oder Auszahlungsbestätigung. Ab diesem Tag laufen im Tilgungsplan die Zinsen.",
    ),
    first_payment_on: date(
      "Erste Rate am",
      "Der Tag, an dem die erste Rate fällig ist, laut Darlehensvertrag. Die weiteren Raten folgen monatlich zum selben Tag.",
    ),
    interest_only_before_first_payment: {
      label: "Vor der ersten Rate nur Zinsen",
      kind: "bool",
      description:
        "Bei einer Auszahlung in Teilen berechnet die Bank bis zur ersten vollen Rate nur Zinsen auf den ausgezahlten Betrag; diese Zahlungen werden ganz als Darlehenszinsen gebucht, wenn der Buchungstext die Zinsen in genau dieser Höhe nennt. Der Tilgungsplan beginnt dann mit der ersten Rate.",
    },
    account_iban: text(
      "Darlehenskonto (IBAN)",
      "Die IBAN des Darlehenskontos laut Darlehensvertrag. Gespeichert wird sie nur maskiert und als nicht umkehrbarer Prüfwert; daran erkennt Hausbuch die Raten im Kontoauszug.",
    ),
    signed_on: date("Vertragsdatum", "Der Tag, an dem der Darlehensvertrag unterschrieben wurde."),
  },
  tenant: {
    full_name: text("Name", "Der Name der Mieterin oder des Mieters, wie im Mietvertrag."),
    email: text("E-Mail", "Eine Kontaktangabe, z. B. aus dem Mietvertrag."),
    phone: text("Telefon", "Eine Kontaktangabe, z. B. aus dem Mietvertrag."),
    iban: text(
      "IBAN",
      "Die IBAN des Kontos, von dem die Miete kommt. Gespeichert wird sie nur maskiert und als nicht umkehrbarer Prüfwert; daran erkennt Hausbuch die Mietzahlungen im Kontoauszug.",
    ),
  },
  tenancy: {
    unit_id: {
      label: "Einheit",
      kind: "unit",
      description:
        "Die vermietete Wohnung oder das vermietete Zimmer. Hausbuch schlägt die Einheit vor, deren Bezeichnung zur Anschrift und zur Wohnungs- oder Zimmerangabe im Mietvertrag passt, oder die einzige Einheit der Immobilie; sonst wählen Sie sie hier.",
    },
    start_date: date("Mietbeginn", "Der Tag, ab dem das Mietverhältnis laut Mietvertrag läuft."),
    signed_on: date("Vertragsdatum", "Der Tag, an dem der Mietvertrag unterschrieben wurde, meist neben den Unterschriften."),
    fixed_term_until: date("Befristet bis", "Nur bei einem Zeitmietvertrag: das vereinbarte Ende. Leer, wenn der Vertrag unbefristet ist."),
    end_date: date(
      "Auszug",
      "Nur bei einem schon beendeten Mietverhältnis: der Tag, an dem es endete; sonst leer lassen. Danach richtet sich, wie lange Mietunterlagen und Mieterdaten aufbewahrt werden.",
    ),
    rent_model: choice(
      "Mietmodell",
      RENT_MODEL,
      "Festmiete, Staffelmiete (die Erhöhungen stehen schon im Vertrag) oder Indexmiete (an den Verbraucherpreisindex gekoppelt), laut Mietvertrag.",
    ),
    deposit_amount: money("Kaution (€)", "Die vereinbarte Kaution laut Mietvertrag."),
    deposit_form: choice(
      "Art der Kaution",
      DEPOSIT_FORM,
      "Wie die Kaution geleistet wird, laut Mietvertrag. Ist eine vereinbart, fragt die Unterlagen-Checkliste nach einem Kautionsnachweis.",
    ),
    deposit_bank: text("Bank des Kautionskontos", "Die Bank, bei der die Kaution angelegt ist, z. B. laut Kontoeröffnung oder Sparbuch."),
    deposit_iban: text("IBAN des Kautionskontos", "Die IBAN des Kontos, auf dem die Kaution liegt. Gespeichert wird sie nur maskiert."),
  },
  rent: {
    cold_rent: money(
      "Kaltmiete (€ / Monat)",
      "Die monatliche Miete ohne Nebenkosten laut Mietvertrag. Mit ihr und der NK-Vorauszahlung erkennt Hausbuch eingehende Mietzahlungen und teilt sie auf.",
    ),
    operating_costs_prepayment: money(
      "NK-Vorauszahlung (€ / Monat)",
      "Die monatliche Vorauszahlung auf Betriebs- und Heizkosten laut Mietvertrag, beide zusammen.",
    ),
    effective_from: date("Gültig ab", "Der erste Tag, für den diese Miete gilt, meist der Mietbeginn."),
  },
  deposit_received: {
    amount: money("Betrag (€)", "Der Kautionsbetrag, der laut Dokument eingegangen ist."),
    received_on: date("Eingegangen am", "Der Tag, an dem die Kaution einging, laut Quittung oder Kontoauszug."),
    tenancy_id: {
      label: "Mietverhältnis",
      kind: "tenancy",
      description:
        "Das Mietverhältnis, zu dem die Kaution gehört. Hausbuch schlägt das vor, dessen vereinbarte Kaution diesem Betrag entspricht, oder das einzige; sonst wählen Sie es hier.",
    },
  },
};

/**
 * The fields of a record that creates (mode `create`), in this order: every field of the model
 * its apply validates against (backend services/proposals.py `_Apply`), so a value the proposal
 * lacks can be typed by hand. Left out: what the apply fills itself (the property, the document
 * as Beleg or evidence, a split's source, a tenancy's parties, a loan's property) and what the
 * review cannot edit (the address's country, always DE; a BMF split's inputs). A record that
 * patches or corrects shows only its proposed fields.
 */
const CREATE_FIELDS: Readonly<Record<string, readonly string[]>> = {
  // DocumentTyping
  typing: ["type", "subtype", "title", "document_date", "tax_year"],
  // PropertyCreate
  property: [
    "label",
    "kind",
    "address.street",
    "address.house_number",
    "address.postal_code",
    "address.city",
    "build_year",
    "notarized_on",
    "purchase_date",
    "purchase_price",
  ],
  // UnitCreate
  unit: ["label", "floor_area_m2", "rooms", "mea"],
  // PriceSplitCreate
  price_split: ["building_share", "note"],
  // OwnershipBody (the shares are shown only when proposed: they are not typed here)
  ownership: ["shares", "effective_date"],
  // AcquisitionCostCreate
  cost: ["kind", "amount", "paid_on", "note"],
  // LoanCreateRequest
  loan: [
    "lender",
    "contract_number",
    "principal",
    "nominal_rate",
    "monthly_payment",
    "fixed_until",
    "follow_up_rate",
    "disbursed_on",
    "first_payment_on",
    "interest_only_before_first_payment",
    "account_iban",
    "signed_on",
  ],
  // TenantCreate
  tenant: ["full_name", "email", "phone", "iban"],
  // TenancyCreate
  tenancy: [
    "unit_id",
    "start_date",
    "signed_on",
    "fixed_term_until",
    "end_date",
    "rent_model",
    "deposit_amount",
    "deposit_form",
    "deposit_bank",
    "deposit_iban",
  ],
  // InitialRent
  rent: ["cold_rent", "operating_costs_prepayment", "effective_from"],
  // _DepositValues (DepositReceivedBody + tenancy_id)
  deposit_received: ["tenancy_id", "amount", "received_on"],
};

/** The extraction fields a derived value names in `derived_from`. */
const DERIVED_INPUT: Record<string, string> = {
  purchase_price: "Kaufpreis",
  land_value: "Bodenwert",
  principal: "Darlehenssumme",
  nominal_rate: "Sollzins",
  initial_repayment_rate: "anfängliche Tilgung",
  buyers: "Käufer",
  ownership_clause: "Erwerbsverhältnis (z. B. „je zur Hälfte“)",
  operating_costs_prepayment: "Betriebskosten-Vorauszahlung",
  heating_costs_prepayment: "Heizkosten-Vorauszahlung",
  street: "Straße",
  house_number: "Hausnummer",
  postal_code: "PLZ",
  unit_label: "WE-Nummer",
  rent_clause: "Mietklausel",
  amount: "Kautionsbetrag",
};

function lookup(map: Readonly<Record<string, string>>, key: string): string {
  return map[key] ?? key;
}

function prefixOf(key: string): [string, string] {
  const at = key.indexOf(":");
  return at < 0 ? [key, ""] : [key.slice(0, at), key.slice(at + 1)];
}

function specFor(record: ProposalRecord, name: string): FieldSpec {
  return FIELDS[prefixOf(record.key)[0]]?.[name] ?? text(name);
}

function fieldsOf(record: ProposalRecord): [string, ExtractedValue][] {
  return Object.entries(record.fields ?? {});
}

/** A row of a record's card: the field and its proposed value, null when the proposal did not
 * contain the field (a record that creates: typed by hand, or left empty). */
type Row = [string, ExtractedValue | null];

/** The rows of a record: for one that creates, every field of CREATE_FIELDS in that order (a
 * structured "fixed" value only when proposed), then any proposed field the list lacks; for
 * every other mode, only the proposed fields, as before. */
function rowsOf(record: ProposalRecord): Row[] {
  const proposed = fieldsOf(record);
  const schema = record.mode === "create" ? CREATE_FIELDS[prefixOf(record.key)[0]] : undefined;
  if (!schema) return proposed;
  const byName = new Map(proposed);
  const rows: Row[] = [];
  for (const name of schema) {
    const value = byName.get(name);
    if (value) rows.push([name, value]);
    else if (specFor(record, name).kind !== "fixed") rows.push([name, null]);
  }
  for (const [name, value] of proposed) if (!schema.includes(name)) rows.push([name, value]);
  return rows;
}

/** The rows the card shows: as rowsOf, but an empty Steuerjahr the proposal did not contain is
 * hidden for the subtypes the typing form hides it for (NO_TAX_YEAR), while it stays empty. */
function visibleRows(record: ProposalRecord, state: RecordState): Row[] {
  return rowsOf(record).filter(([name, value]) => {
    if (value || name !== "tax_year" || prefixOf(record.key)[0] !== "typing") return true;
    return !(NO_TAX_YEAR.has(state.fields.subtype?.raw ?? "") && (state.fields.tax_year?.raw ?? "") === "");
  });
}

function recordTitle(record: ProposalRecord): string {
  const [prefix, rest] = prefixOf(record.key);
  switch (prefix) {
    case "typing":
      return "Typisierung";
    case "property":
      return "Immobilie";
    case "correction":
      return `Korrektur: ${specFor(record, rest).label}`;
    case "unit": {
      const label = record.fields?.label?.value;
      return typeof label === "string" && label ? `Einheit ${label}` : "Einheit";
    }
    case "price_split":
      return "Kaufpreisaufteilung";
    case "ownership":
      return "Eigentumsanteile";
    case "cost":
      return `Nebenkosten: ${lookup(ACQUISITION_COST_KIND, rest)}`;
    case "loan":
      return "Darlehen";
    case "tenant":
      return `Mieter ${rest}`;
    case "tenancy":
      return "Mietverhältnis";
    case "rent":
      return "Anfangsmiete";
    case "deposit_received":
      return EVENT_TYPE["tenancy.deposit_received"];
    case "link":
      return `Verknüpfung: ${lookup(LINK_TARGET, rest)}`;
    default:
      return record.key;
  }
}

// --- values: the API's JSON <-> the input's text ----------------------------------------

function isFraction(value: unknown): value is Fraction {
  return (
    typeof value === "object" &&
    value !== null &&
    "numerator" in value &&
    "denominator" in value &&
    typeof value.numerator === "number" &&
    typeof value.denominator === "number"
  );
}

function scalar(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return "";
}

/** The proposed value as the text an input starts with. */
function toRaw(spec: FieldSpec, value: unknown): string {
  if (value === null || value === undefined) return "";
  switch (spec.kind) {
    case "money":
    case "area":
      return decimalInput(scalar(value));
    case "rate":
      return percentInput(scalar(value));
    case "fraction":
      return isFraction(value) ? formatFraction(value) : "";
    case "bool":
      return value === true ? "true" : "";
    default:
      return scalar(value);
  }
}

/** The input's text as the value to send; null for an empty input. */
function fromRaw(spec: FieldSpec, raw: string): Parsed<unknown> {
  if (raw.trim() === "") return { ok: true, value: null };
  switch (spec.kind) {
    case "money":
      return toMoneyString(raw);
    case "rate":
      return toRate(raw, 6);
    case "area":
      return toDecimalString(raw, spec.decimals ?? 2);
    case "year":
      return toYear(raw);
    case "fraction":
      return toFraction(raw);
    case "bool":
      return { ok: true, value: raw === "true" ? true : null };
    default:
      return { ok: true, value: raw.trim() };
  }
}

/** A value for reading (the current value of an existing record, an unchanged record). */
function display(spec: FieldSpec, value: unknown, fixed: (value: unknown) => string): string {
  if (value === null || value === undefined || value === "") return "–";
  switch (spec.kind) {
    case "money":
      return formatMoney(scalar(value));
    case "rate":
      return formatPercent(scalar(value));
    case "area":
      return formatArea(scalar(value));
    case "fraction":
      return isFraction(value) ? formatShare(value) : "–";
    case "date":
      return formatDate(scalar(value));
    case "enum":
      return lookup(spec.choices ?? {}, scalar(value));
    case "bool":
      return value === true ? "Ja" : "Nein";
    case "fixed":
    case "unit":
    case "tenancy":
      return fixed(value);
    default:
      return scalar(value) || "–";
  }
}

/** Ids and structured values as text: ownership shares (matched by owner names, shown, not
 * edited) and the unit or tenancy an id names (for a stored value, or one the list lacks). */
function fixedText(directory: Directory | null): (value: unknown) => string {
  return (value) => {
    if (typeof value === "string") {
      const unit = directory?.units.find((u) => u.id === value);
      if (unit) return unit.label;
      const tenancy = directory?.tenancies.find((t) => t.id === value);
      if (tenancy) return `Mietverhältnis ab ${formatDate(tenancy.start_date)}`;
      return "zugeordneter Eintrag";
    }
    if (Array.isArray(value)) {
      return value
        .map((entry: unknown) => {
          if (typeof entry !== "object" || entry === null || !("owner_id" in entry) || !("share" in entry)) return "?";
          const owner = directory?.owners.find((o) => o.id === entry.owner_id);
          const share = isFraction(entry.share) ? formatShare(entry.share) : "?";
          return `${owner?.full_name ?? "Eigentümer"}: ${share}`;
        })
        .join(", ");
    }
    return "–";
  };
}

// --- the current proposal, polled while it is read ------------------------------------------

/** `onRead` runs when a polled proposal leaves queued/running: the read wrote a new extraction
 * version, so the page reloads the document and its typing form shows the new reading (D37). */
function useCurrentProposal(documentId: string, onRead: () => void) {
  const [proposal, setProposal] = useState<Proposal | null | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [version, setVersion] = useState(0);
  const onReadRef = useRef(onRead);
  useEffect(() => {
    onReadRef.current = onRead;
  }, [onRead]);

  useEffect(() => {
    let cancelled = false;
    ok(api.GET("/api/documents/{document_id}/proposals", { params: { path: { document_id: documentId } } })).then(
      (list) => {
        if (cancelled) return;
        setProposal(list[0] ?? null);
        setError(null);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, version]);

  // Each answer is a new object, so the effect re-arms after every poll while in flight; a
  // failed poll stops it (the error offers a retry, which reloads the list).
  useEffect(() => {
    if (!proposal || !IN_FLIGHT.includes(proposal.status)) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      ok(api.GET("/api/proposals/{proposal_id}", { params: { path: { proposal_id: proposal.id } } })).then(
        (next) => {
          if (cancelled) return;
          setProposal(next);
          // Only an in-flight proposal is polled, so a settled answer is the transition.
          if (!IN_FLIGHT.includes(next.status)) onReadRef.current();
        },
        (caught: unknown) => {
          if (!cancelled) setError(caught);
        },
      );
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [proposal]);

  const reload = useCallback(() => {
    setError(null);
    setVersion((n) => n + 1);
  }, []);
  return { proposal, error, setProposal, reload };
}

// --- the section ----------------------------------------------------------------------------

/** Where the phase 1 forms take what this kind of document would have proposed. */
function manualEntry(kind: ExtractionKind | null, directory: Directory | null): { href: string; where: string } {
  const only = directory?.properties.length === 1 ? directory.properties[0] : undefined;
  switch (kind) {
    case "purchase_contract":
      return { href: "/properties/new", where: "Immobilie anlegen" };
    case "loan_contract":
      return only ? { href: `/loans/new?property=${only.id}`, where: "Darlehen anlegen" } : { href: "/", where: "Darlehen bei der Immobilie anlegen" };
    case "invoice":
    case "bescheid":
      return only
        ? { href: `/properties/${only.id}?tab=nebenkosten`, where: "Nebenkosten der Immobilie" }
        : { href: "/", where: "Nebenkosten bei der Immobilie" };
    case "lease":
      return { href: "/tenancies/new", where: "Mietverhältnis anlegen" };
    default:
      return { href: "#typisierung", where: "Typisierung unten" };
  }
}

function ManualEntry({ kind, directory }: { kind: ExtractionKind | null; directory: Directory | null }) {
  const { href, where } = manualEntry(kind, directory);
  return (
    <p>
      <Link href={href}>Von Hand eingeben</Link> <span className="muted">({where}; das Dokument unten typisieren und verknüpfen)</span>
    </p>
  );
}

/**
 * The "Vorschlag" section of a document page. `onApplied` reloads the page's document (its
 * typing and links change with an apply; a finished read changes its extraction).
 */
export function ProposalSection({
  detail,
  directory,
  onApplied,
}: {
  detail: DocumentDetail;
  directory: Directory | null;
  onApplied: () => void;
}) {
  const documentId = detail.document.id;
  const { proposal, error, setProposal, reload } = useCurrentProposal(documentId, onApplied);
  const [applied, setApplied] = useState<ProposalApplyResult | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [tenantData, setTenantData] = useState(false);
  const tenantDataId = useId();
  // Whether tenant documents may go to the AI now: a blocked proposal can then be read again.
  // Read only while a blocked proposal is shown; an error leaves the notice as it is.
  const [tenantDocumentsAllowed, setTenantDocumentsAllowed] = useState(false);
  const blockedId = proposal?.status === "blocked_tenant_data" ? proposal.id : null;
  useEffect(() => {
    if (!blockedId) return;
    let cancelled = false;
    ok(api.GET("/api/workspace/settings")).then(
      (settings) => {
        if (!cancelled) setTenantDocumentsAllowed(settings.ai_tenant_documents);
      },
      () => {
        if (!cancelled) setTenantDocumentsAllowed(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [blockedId]);

  async function rerun(confirmText: string | null) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setActionError(null);
    setApplied(null);
    try {
      const next = await ok(
        api.POST("/api/documents/{document_id}/proposals", {
          params: { path: { document_id: documentId } },
          // A re-run keeps the flag the uploader set: a document marked as holding tenant data
          // is never sent to the AI because a later button had no checkbox.
          body: { contains_tenant_data: (proposal?.contains_tenant_data ?? false) || tenantData },
        }),
      );
      setProposal(next);
    } catch (caught) {
      setActionError(caught);
    } finally {
      setBusy(false);
    }
  }

  const rerunButton = (label: string, confirmText: string | null) => (
    <button type="button" className="secondary" disabled={busy} onClick={() => void rerun(confirmText)}>
      {busy ? "Startet …" : label}
    </button>
  );

  let body: ReactNode;
  if (proposal === undefined) {
    body = error ? null : <p className="muted">Lädt …</p>;
  } else if (proposal === null) {
    body = (
      <div className="stack">
        <p className="muted">Dieses Dokument wurde noch nicht automatisch ausgelesen.</p>
        <label className="inline" htmlFor={tenantDataId}>
          <input id={tenantDataId} type="checkbox" checked={tenantData} onChange={(e) => setTenantData(e.target.checked)} />
          Enthält Mieterdaten
        </label>
        <div className="row">{rerunButton("Automatisch auslesen", null)}</div>
      </div>
    );
  } else if (IN_FLIGHT.includes(proposal.status)) {
    body = (
      <div className="notice notice-info" role="status">
        <p>
          {proposal.status === "queued" ? "Wartet auf das Auslesen …" : "Wird ausgelesen …"} Lange Verträge brauchen bis zu einer Minute; die
          Seite aktualisiert sich von selbst.
        </p>
      </div>
    );
  } else if (proposal.status === "ready") {
    body = (
      <ProposalReview
        key={proposal.id}
        proposal={proposal}
        detail={detail}
        directory={directory}
        onDecided={(result) => {
          setProposal(result.proposal);
          if ("applied" in result) {
            setApplied(result);
            onApplied();
          }
        }}
        onRerun={() => void rerun("Das Dokument neu auslesen? Dieser Vorschlag wird ersetzt.")}
        busy={busy}
      />
    );
  } else if (proposal.status === "blocked_tenant_data") {
    const hasTyping = (proposal.records ?? []).length > 0;
    body = (
      <div className="stack">
        <div className="notice notice-warn" role="status">
          {tenantDocumentsAllowed ? (
            <p>
              Dieses Dokument enthält Mieterdaten und wurde deshalb nicht an die KI geschickt. Inzwischen ist das für den Haushalt freigegeben; es
              lässt sich jetzt neu auslesen.
            </p>
          ) : (
            <p>
              Dieses Dokument enthält Mieterdaten. Solche Dokumente werden erst an die KI geschickt, wenn das für den Haushalt freigegeben ist: unter{" "}
              <Link href="/settings">Einstellungen</Link>, „Mieterdokumente von der KI lesen lassen“. Bis dahin bitte von Hand eingeben.
            </p>
          )}
        </div>
        {tenantDocumentsAllowed ? (
          <div className="row">{rerunButton("Neu auslesen", hasTyping ? "Das Dokument neu auslesen? Dieser Vorschlag wird ersetzt." : null)}</div>
        ) : null}
        <ManualEntry kind={proposal.kind} directory={directory} />
        {hasTyping ? (
          <>
            <p className="muted">Erkannt wurde nur die Art des Dokuments; diese Typisierung lässt sich übernehmen.</p>
            <ProposalReview
              key={proposal.id}
              proposal={proposal}
              detail={detail}
              directory={directory}
              onDecided={(result) => {
                setProposal(result.proposal);
                if ("applied" in result) {
                  setApplied(result);
                  onApplied();
                }
              }}
              onRerun={null}
              busy={busy}
            />
          </>
        ) : null}
      </div>
    );
  } else if (proposal.status === "failed") {
    body = (
      <div className="stack">
        <div className="notice notice-error" role="alert">
          <p>
            Konnte nicht gelesen werden: {aiErrorText(proposal.error_code)}
            {proposal.error_code ? <code className="error-code">{proposal.error_code}</code> : null}
          </p>
        </div>
        <ManualEntry kind={proposal.kind} directory={directory} />
        <div className="row">{rerunButton("Neu auslesen", null)}</div>
      </div>
    );
  } else {
    // applied, rejected, superseded
    body = (
      <div className="stack">
        {applied ? (
          <AppliedSummary result={applied} />
        ) : (
          <p className="muted">
            Vorschlag {proposal.status === "applied" ? "übernommen" : proposal.status === "rejected" ? "verworfen" : "ersetzt"}
            {proposal.decided_at ? ` am ${formatDateTime(proposal.decided_at)}` : ""}.
          </p>
        )}
        <div className="row">{rerunButton("Neu auslesen", "Das Dokument neu auslesen? Das legt einen neuen Vorschlag an.")}</div>
      </div>
    );
  }

  return (
    <section aria-labelledby="vorschlag" className="stack">
      <h2 id="vorschlag">
        Vorschlag
        {proposal?.kind ? <span className="tag proposal-kind">{EXTRACTION_KIND[proposal.kind]}</span> : null}
      </h2>
      {error ? (
        <div>
          <ErrorNotice error={error} />
          <button type="button" className="secondary" onClick={reload}>
            Erneut versuchen
          </button>
        </div>
      ) : null}
      {body}
      <ErrorNotice error={actionError} />
    </section>
  );
}

/** What an apply created or changed, each linking to its page where it has one. */
function AppliedSummary({ result }: { result: ProposalApplyResult }) {
  const property =
    result.applied.find((a) => a.key === "property" && a.entity_id)?.entity_id ??
    result.applied.find((a) => a.key === "link:property" && a.entity_id)?.entity_id ??
    null;
  const done = result.applied.filter((a) => !a.skipped);
  return (
    <Notice>
      Übernommen.{" "}
      {done.length > 0 ? (
        <>
          {done.map((entity, index) => {
            const href = entityHref(entity, property);
            const label = appliedLabel(entity);
            return (
              <span key={entity.key}>
                {index > 0 ? ", " : ""}
                {href ? <Link href={href}>{label}</Link> : label}
              </span>
            );
          })}
          .
        </>
      ) : (
        "Nichts angelegt oder geändert."
      )}
    </Notice>
  );
}

function appliedLabel(entity: AppliedEntity): string {
  const [prefix, rest] = prefixOf(entity.key);
  const verb = entity.mode === "create" ? "angelegt" : entity.mode === "correction" ? "korrigiert" : "geändert";
  switch (prefix) {
    case "typing":
      return "Dokument typisiert";
    case "link":
      return `verknüpft mit ${lookup(LINK_TARGET, rest)}`;
    case "property":
      return `Immobilie ${verb}`;
    case "correction":
      return `${lookup({ notarized_on: "Notartermin", purchase_date: "Übergang" }, rest)} korrigiert`;
    case "unit":
      return `Einheit ${verb}`;
    case "price_split":
      return "Kaufpreisaufteilung erfasst";
    case "ownership":
      return "Eigentumsanteile festgelegt";
    case "cost":
      return `Nebenkosten (${lookup(ACQUISITION_COST_KIND, rest)}) ${entity.mode === "create" ? "erfasst" : "mit Beleg verknüpft"}`;
    case "loan":
      return `Darlehen ${verb}`;
    case "tenant":
      return `Mieter ${rest} ${verb}`;
    case "tenancy":
      return `Mietverhältnis ${verb}`;
    case "rent":
      return "Anfangsmiete erfasst";
    case "deposit_received":
      return "Kautionseingang erfasst";
    default:
      return entity.key;
  }
}

function entityHref(entity: AppliedEntity, propertyId: string | null): string | null {
  const id = entity.entity_id;
  if (!id) return null;
  const [prefix, rest] = prefixOf(entity.key);
  const target = prefix === "link" ? rest : prefix === "correction" ? "property" : prefix;
  switch (target) {
    case "property":
      return `/properties/${id}`;
    case "unit":
      return `/units/${id}`;
    case "loan":
      return `/loans/${id}`;
    case "tenant":
      return `/tenants/${id}`;
    case "tenancy":
    case "deposit_received":
      return `/tenancies/${id}`;
    case "cost":
      return propertyId ? `/properties/${propertyId}?tab=nebenkosten` : null;
    case "price_split":
      return propertyId ? `/properties/${propertyId}?tab=aufteilung` : null;
    case "ownership":
      return `/properties/${id}?tab=eigentuemer`;
    default:
      return null;
  }
}

// --- the review form ------------------------------------------------------------------------

interface FieldState {
  raw: string;
  /** For an existing record: take the proposed value (übernehmen) or keep the current one. */
  take: boolean;
}

interface RecordState {
  skip: boolean;
  fields: Record<string, FieldState>;
}

function initialState(records: ProposalRecord[]): Record<string, RecordState> {
  const state: Record<string, RecordState> = {};
  for (const record of records) {
    const fields: Record<string, FieldState> = {};
    for (const [name, value] of rowsOf(record)) fields[name] = { raw: value ? toRaw(specFor(record, name), value.value) : "", take: true };
    // An unchanged record would write nothing: it is sent as skipped.
    state[record.key] = { skip: record.mode === "unchanged", fields };
  }
  return state;
}

/** The apply body from the form, or the messages of the inputs that do not parse. */
function buildApply(
  records: ProposalRecord[],
  state: Record<string, RecordState>,
): { ok: true; records: ProposalApplyRecord[] } | { ok: false; messages: FieldMessages } {
  const messages: FieldMessages = {};
  const out: ProposalApplyRecord[] = [];
  for (const record of records) {
    const recordState = state[record.key];
    if (!recordState || recordState.skip || record.mode === "unchanged") {
      out.push({ key: record.key, skip: true });
      continue;
    }
    const values: Record<string, unknown> = {};
    const decisions: Record<string, FieldDecision> = {};
    let keepAll = record.mode === "correction";
    for (const [name, proposed] of rowsOf(record)) {
      const field = recordState.fields[name];
      if (!field) continue;
      const spec = specFor(record, name);
      if ((record.mode === "patch" || record.mode === "correction") && !field.take) {
        decisions[name] = "skipped"; // "behalten": the current value stays
        continue;
      }
      keepAll = false;
      if (spec.kind === "fixed") {
        if (proposed && proposed.value !== null) values[name] = proposed.value;
        continue;
      }
      // A picked unit or tenancy is its id (the raw value), like any text below. A field the
      // proposal did not contain is sent only when typed; the backend records it as edited.
      const parsed = fromRaw(spec, field.raw);
      if (!parsed.ok) {
        (messages[`${record.key}.${name}`] ??= []).push(parsed.message);
        continue;
      }
      // Empty stays out: the backend records it as cleared (or accepted, when nothing was
      // proposed) and the *Create default applies.
      if (parsed.value !== null) values[name] = parsed.value;
    }
    // A correction kept as it is changes nothing: the record is skipped.
    if (keepAll) out.push({ key: record.key, skip: true, decisions });
    else out.push({ key: record.key, skip: false, values, decisions });
  }
  return Object.keys(messages).length > 0 ? { ok: false, messages } : { ok: true, records: out };
}

type Decided = ProposalApplyResult | { proposal: Proposal };

function ProposalReview({
  proposal,
  detail,
  directory,
  onDecided,
  onRerun,
  busy: rerunBusy,
}: {
  proposal: Proposal;
  detail: DocumentDetail;
  directory: Directory | null;
  onDecided: (result: Decided) => void;
  onRerun: (() => void) | null;
  busy: boolean;
}) {
  const records = useMemo(() => proposal.records ?? [], [proposal.records]);
  const [state, setState] = useState(() => initialState(records));
  const [busy, setBusy] = useState<"apply" | "reject" | null>(null);
  const keys = useMemo(
    () => records.flatMap((record) => [record.key, ...rowsOf(record).map(([name]) => `${record.key}.${name}`)]),
    [records],
  );
  const form = useFormErrors(keys);
  const originalUrl = detail.opens_inline ? `/api/documents/${detail.document.id}/original?disposition=inline` : null;
  const fixed = fixedText(directory);

  function update(key: string, change: (current: RecordState) => RecordState) {
    setState((current) => {
      const record = current[key];
      return record ? { ...current, [key]: change(record) } : current;
    });
  }

  async function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const built = buildApply(records, state);
    if (!built.ok) return form.invalid(built.messages);
    form.clear();
    setBusy("apply");
    try {
      const result = await ok(
        api.POST("/api/proposals/{proposal_id}/apply", {
          params: { path: { proposal_id: proposal.id } },
          body: { records: built.records },
        }),
      );
      onDecided(result);
    } catch (caught) {
      form.fail(caught);
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    if (!window.confirm("Den Vorschlag verwerfen? Es wird nichts angelegt oder geändert.")) return;
    form.clear();
    setBusy("reject");
    try {
      const rejected = await ok(api.POST("/api/proposals/{proposal_id}/reject", { params: { path: { proposal_id: proposal.id } } }));
      onDecided({ proposal: rejected });
    } catch (caught) {
      form.fail(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <form className="stack proposal" onSubmit={apply} noValidate aria-label="Vorschlag prüfen">
      <p className="muted">
        Bitte jeden Wert mit der Fundstelle vergleichen. Gespeichert wird erst mit „Übernehmen“; jeder Eintrag läuft dabei durch dieselben Prüfungen
        wie beim Eingeben von Hand.
      </p>
      <div className="proposal-records">
        {records.map((record) => {
          const recordState = state[record.key];
          if (!recordState) return null;
          return (
            <RecordCard
              key={record.key}
              record={record}
              state={recordState}
              errors={form.of(record.key)}
              fieldErrors={(name) => form.of(`${record.key}.${name}`)}
              originalUrl={originalUrl}
              directory={directory}
              fixed={fixed}
              onChange={(change) => update(record.key, change)}
            />
          );
        })}
      </div>
      <FormBanner errors={form.errors} />
      <div className="row proposal-foot">
        <button type="submit" disabled={busy !== null}>
          {busy === "apply" ? "Übernimmt …" : "Übernehmen"}
        </button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void reject()}>
          {busy === "reject" ? "Verwirft …" : "Verwerfen"}
        </button>
        {onRerun ? (
          <button type="button" className="secondary" disabled={busy !== null || rerunBusy} onClick={onRerun}>
            Neu auslesen
          </button>
        ) : null}
      </div>
    </form>
  );
}

function skipLabel(record: ProposalRecord): string {
  if (record.entity_type === "link") return "nicht verknüpfen";
  if (record.mode === "create") return "nicht anlegen";
  return "nicht ändern";
}

function RecordCard({
  record,
  state,
  errors,
  fieldErrors,
  originalUrl,
  directory,
  fixed,
  onChange,
}: {
  record: ProposalRecord;
  state: RecordState;
  errors: string[];
  fieldErrors: (name: string) => string[];
  originalUrl: string | null;
  directory: Directory | null;
  fixed: (value: unknown) => string;
  onChange: (change: (current: RecordState) => RecordState) => void;
}) {
  const headingId = useId();
  const skipId = useId();
  const title = recordTitle(record);
  const unchanged = record.mode === "unchanged";
  const fields = visibleRows(record, state);
  const notes = record.notes ?? [];

  return (
    <section className={state.skip && !unchanged ? "card proposal-card proposal-skipped" : "card proposal-card"} aria-labelledby={headingId}>
      <div className="row card-head proposal-card-head">
        <h3 id={headingId}>{title}</h3>
        <span className={record.mode === "correction" ? "corrected" : record.mode === "create" ? "badge" : "tag"}>{RECORD_MODE[record.mode]}</span>
        <span className="spacer" />
        {unchanged ? null : (
          <label className="inline" htmlFor={skipId}>
            <input
              id={skipId}
              type="checkbox"
              checked={state.skip}
              onChange={(e) => {
                const skip = e.target.checked;
                onChange((current) => ({ ...current, skip }));
              }}
            />
            {skipLabel(record)}
          </label>
        )}
      </div>
      {errors.length > 0 ? (
        <ul className="field-errors" role="alert">
          {errors.map((message, index) => (
            <li key={index}>{message}</li>
          ))}
        </ul>
      ) : null}
      {notes.length > 0 ? (
        <ul className="proposal-notes">
          {notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      ) : null}
      {record.entity_type === "link" ? (
        <p className="proposal-link">
          Das Dokument wird verknüpft mit: <strong>{linkTarget(record, directory)}</strong>
        </p>
      ) : null}
      {unchanged ? <p className="muted">Stimmt mit dem Gespeicherten überein; beim Übernehmen ändert sich hier nichts.</p> : null}
      {fields.length > 0 ? (
        <div className="proposal-rows">
          {fields.map(([name, value]) => (
            <FieldRow
              key={name}
              record={record}
              name={name}
              value={value}
              state={state.fields[name]}
              disabled={state.skip}
              errors={fieldErrors(name)}
              hasNotes={notes.length > 0}
              originalUrl={originalUrl}
              directory={directory}
              fixed={fixed}
              onChange={(next) =>
                onChange((current) => {
                  const field = current.fields[name];
                  return field ? { ...current, fields: { ...current.fields, [name]: { ...field, ...next } } } : current;
                })
              }
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function linkTarget(record: ProposalRecord, directory: Directory | null): string {
  const [, target] = prefixOf(record.key);
  const id = record.target_id;
  if (!id) return target === "property" ? "der neu angelegten Immobilie" : target === "loan" ? "dem neu angelegten Darlehen" : "dem neu angelegten Eintrag";
  switch (target) {
    case "property":
      return directory?.properties.find((p) => p.id === id)?.label ?? "Immobilie";
    case "loan": {
      const loan = directory?.loans.find((l) => l.id === id);
      return loan ? `${loan.lender}${loan.contract_number ? ` ${loan.contract_number}` : ""}` : "Darlehen";
    }
    case "tenancy": {
      const tenancy = directory?.tenancies.find((t) => t.id === id);
      return tenancy ? `Mietverhältnis ab ${formatDate(tenancy.start_date)}` : "Mietverhältnis";
    }
    case "tenant":
      return tenantName(directory?.tenants.find((t) => t.id === id));
    default:
      return lookup(LINK_TARGET, target);
  }
}

function FieldRow({
  record,
  name,
  value,
  state,
  disabled,
  errors,
  hasNotes,
  originalUrl,
  directory,
  fixed,
  onChange,
}: {
  record: ProposalRecord;
  name: string;
  /** null: the proposal did not contain this field (a record that creates). */
  value: ExtractedValue | null;
  state: FieldState | undefined;
  disabled: boolean;
  errors: string[];
  hasNotes: boolean;
  originalUrl: string | null;
  directory: Directory | null;
  fixed: (value: unknown) => string;
  onChange: (next: Partial<FieldState>) => void;
}) {
  const id = useId();
  const quoteId = useId();
  const hintId = useId();
  const errorId = useId();
  const descriptionId = useId();
  const [quoteOpen, setQuoteOpen] = useState(false);
  const [descriptionOpen, setDescriptionOpen] = useState(false);
  const spec = specFor(record, name);
  const unchanged = record.mode === "unchanged";
  const existing = record.mode === "patch" || record.mode === "correction";
  const take = state?.take ?? true;
  const current = record.current?.[name];
  const hint = fieldHint(spec, value, hasNotes);
  const source = value?.source ?? null;
  const describedBy =
    [spec.description && descriptionOpen ? descriptionId : null, hint ? hintId : null, errors.length > 0 ? errorId : null].filter(Boolean).join(" ") ||
    undefined;
  const inputDisabled = disabled || (existing && !take);

  let input: ReactNode;
  if (unchanged || spec.kind === "fixed") {
    input = (
      <output id={id} className="proposal-fixed" aria-describedby={describedBy}>
        {display(spec, value?.value, fixed)}
      </output>
    );
  } else if ((spec.kind === "unit" || spec.kind === "tenancy") && directory && pickable(spec.kind, directory).length === 0) {
    input = (
      <output id={id} className="proposal-fixed" aria-describedby={describedBy}>
        <NothingToPick kind={spec.kind} directory={directory} />
      </output>
    );
  } else {
    const common = {
      id,
      value: state?.raw ?? "",
      disabled: inputDisabled,
      "aria-invalid": errors.length > 0 ? true : undefined,
      "aria-describedby": describedBy,
    };
    if (spec.kind === "enum") {
      const choices = options(spec.choices ?? {});
      const raw = state?.raw ?? "";
      input = (
        <select {...common} onChange={(e) => onChange({ raw: e.target.value })}>
          <option value="">– wählen –</option>
          {raw && !choices.some(([key]) => key === raw) ? <option value={raw}>{raw}</option> : null}
          {choices.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      );
    } else if (spec.kind === "unit" || spec.kind === "tenancy") {
      const choices: [string, string][] = directory ? pickable(spec.kind, directory) : [];
      const raw = state?.raw ?? "";
      input = (
        <select {...common} onChange={(e) => onChange({ raw: e.target.value })}>
          <option value="">– wählen –</option>
          {raw && !choices.some(([key]) => key === raw) ? <option value={raw}>{fixed(raw)}</option> : null}
          {choices.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      );
    } else if (spec.kind === "bool") {
      const { value: raw, ...rest } = common;
      input = <input type="checkbox" {...rest} checked={raw === "true"} onChange={(e) => onChange({ raw: e.target.checked ? "true" : "" })} />;
    } else if (spec.kind === "date") {
      input = <input type="date" {...common} onChange={(e) => onChange({ raw: e.target.value })} />;
    } else {
      const numeric = spec.kind === "money" || spec.kind === "rate" || spec.kind === "area" || spec.kind === "year";
      input = (
        <input
          type="text"
          inputMode={numeric ? (spec.kind === "year" ? "numeric" : "decimal") : undefined}
          autoComplete="off"
          {...common}
          onChange={(e) => onChange({ raw: e.target.value })}
        />
      );
    }
  }

  return (
    <div className={errors.length > 0 ? "proposal-row field-invalid" : "proposal-row"}>
      <div className="proposal-row-head">
        <label htmlFor={id}>{spec.label}</label>
        {spec.description ? (
          <InfoToggle
            open={descriptionOpen}
            onToggle={() => setDescriptionOpen((open) => !open)}
            controls={descriptionId}
            label={spec.label}
          />
        ) : null}
        {value ? <ConfidenceDot value={value} /> : null}
        {source ? (
          <button
            type="button"
            className="source-badge"
            aria-expanded={quoteOpen}
            aria-controls={quoteId}
            title={`„${source.quote}“`}
            onClick={() => setQuoteOpen((open) => !open)}
          >
            S. {source.page}
            <span className="visually-hidden"> – Fundstelle anzeigen</span>
          </button>
        ) : null}
      </div>
      {spec.description ? (
        <FieldDescription id={descriptionId} open={descriptionOpen}>
          {spec.description}
        </FieldDescription>
      ) : null}
      <div className={existing && !unchanged ? "proposal-row-body with-current" : "proposal-row-body"}>
        <div>{input}</div>
        {existing && !unchanged ? (
          <CurrentValue
            name={`${record.key}.${name}`}
            label={spec.label}
            current={display(spec, current, fixed)}
            take={take}
            disabled={disabled}
            onChange={(next) => onChange({ take: next })}
          />
        ) : null}
      </div>
      {source ? (
        <div id={quoteId} className="source-quote" hidden={!quoteOpen}>
          <blockquote>„{source.quote}“</blockquote>
          <SourceLink source={source} originalUrl={originalUrl} />
        </div>
      ) : null}
      {hint ? (
        <p className="muted hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      {errors.length > 0 ? (
        <ul className="field-errors" id={errorId}>
          {errors.map((message, index) => (
            <li key={index}>{message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function fieldHint(spec: FieldSpec, value: ExtractedValue | null, hasNotes: boolean): string | null {
  if (!value) return spec.kind === "bool" ? "Nicht im Vorschlag; bei Bedarf von Hand ankreuzen." : "Nicht im Vorschlag; bei Bedarf von Hand eintragen.";
  if (value.value === null) {
    if (spec.kind === "unit") return "Keine Einheit passt eindeutig zum Mietvertrag; bitte wählen.";
    if (spec.kind === "tenancy") return "Kein Mietverhältnis passt eindeutig; bitte wählen.";
    return hasNotes ? "Im Dokument nicht als Wert gefunden; siehe Hinweis oben." : "Im Dokument nicht gefunden.";
  }
  const derived = value.derived_from ?? [];
  if (derived.length > 0) return `Berechnet aus: ${derived.map((name) => lookup(DERIVED_INPUT, name)).join(", ")}.`;
  if (!value.source) return "Ohne Fundstelle (vom Programm gesetzt).";
  return null;
}

/** The units ("<Immobilie> · <Einheit>") or tenancies (the link picker's label) to pick from. */
function pickable(kind: "unit" | "tenancy", directory: Directory): [string, string][] {
  if (kind === "tenancy") {
    return targets(directory)
      .filter((target) => target.type === "tenancy")
      .map((target): [string, string] => [target.id, target.label]);
  }
  const propertyById = new Map(directory.properties.map((property) => [property.id, property]));
  return directory.units.map((unit): [string, string] => [unit.id, `${propertyById.get(unit.property_id)?.label ?? "?"} · ${unit.label}`]);
}

/** Nothing to pick yet: where to create it first (nothing here is saved before then). */
function NothingToPick({ kind, directory }: { kind: "unit" | "tenancy"; directory: Directory }) {
  if (kind === "tenancy") {
    return (
      <>
        Noch kein Mietverhältnis angelegt. Bitte zuerst <Link href="/tenancies/new">das Mietverhältnis anlegen</Link>; danach steht es hier zur
        Wahl.
      </>
    );
  }
  const only = directory.properties.length === 1 ? directory.properties[0] : undefined;
  return only ? (
    <>
      Noch keine Einheit angelegt. Bitte zuerst bei der Immobilie <Link href={`/properties/${only.id}/units/new`}>die Einheit anlegen</Link>;
      danach steht sie hier zur Wahl.
    </>
  ) : (
    <>
      Noch keine Einheit angelegt. Bitte zuerst bei der Immobilie (<Link href="/">Übersicht</Link>) die Einheit anlegen; danach steht sie hier zur
      Wahl.
    </>
  );
}

function SourceLink({ source, originalUrl }: { source: SourceRef; originalUrl: string | null }) {
  if (!originalUrl) return <span className="muted">Dieser Dateityp öffnet nicht im Browser.</span>;
  return (
    <a href={`${originalUrl}#page=${source.page}`} target="_blank" rel="noopener">
      Original auf S. {source.page} öffnen
    </a>
  );
}

function ConfidenceDot({ value }: { value: ExtractedValue }) {
  const checks = (value.checks ?? []).map((check) => CHECK[check]).join(", ");
  const label = `Sicherheit ${CONFIDENCE[value.confidence]}${checks ? ` (${checks})` : ""}`;
  return <span className={`confidence confidence-${value.confidence}`} role="img" aria-label={label} title={label} />;
}

function CurrentValue({
  name,
  label,
  current,
  take,
  disabled,
  onChange,
}: {
  name: string;
  label: string;
  current: string;
  take: boolean;
  disabled: boolean;
  onChange: (take: boolean) => void;
}) {
  const takeId = useId();
  const keepId = useId();
  return (
    <div className="proposal-current">
      <p className="muted">
        Gespeichert: <strong>{current}</strong>
      </p>
      <div className="row" role="radiogroup" aria-label={`${label}: übernehmen oder behalten`}>
        <label className="inline" htmlFor={takeId}>
          <input id={takeId} type="radio" name={name} checked={take} disabled={disabled} onChange={() => onChange(true)} />
          übernehmen
        </label>
        <label className="inline" htmlFor={keepId}>
          <input id={keepId} type="radio" name={name} checked={!take} disabled={disabled} onChange={() => onChange(false)} />
          behalten
        </label>
      </div>
    </div>
  );
}
