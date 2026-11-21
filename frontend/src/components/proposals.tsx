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
//   The typing card also carries the document's hand fields (Betrag, Gegenpartei, Zeitraum,
//   Referenz, Notiz as the kind has them; D132): read, computed by code ("Berechnet aus …") or
//   empty to type. While a proposal is pending review (isPendingReview) the page hides its own
//   typing form, so the card is the one form; an empty hand field is sent as null (a clear).
//   When it proposes a link (proposesLink), the page also hides its link form and remove
//   buttons and shows the existing links read-only.
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
import { formatter, getLocale, localized, translate, useT, type Locale } from "@/i18n";
import {
  DERIVED_INPUT_DE,
  DERIVED_INPUT_EN,
  MOVEMENT_KIND_DE,
  MOVEMENT_KIND_EN,
  proposalFields,
  proposalsText,
  TAX_INPUT_TITLE_DE,
  TAX_INPUT_TITLE_EN,
} from "@/i18n/proposals";

import { DocumentExplanationBox, ImportStoredStatement, NO_TAX_YEAR } from "./documents";
import type { Directory } from "./entities";
import { targets, tenantName } from "./entities";
import { FieldDescription, FormBanner, InfoToggle, useFormErrors, type FieldMessages } from "./form";
import { formatFraction, formatShare, todayIso } from "./format";

// Shown values follow the chosen language, read at call time (the German is the legacy
// formatters' own); the inputs keep their German form (toRaw, fromRaw).
const formatMoney = (v: string | null | undefined) => formatter().money(v);
const formatPercent = (v: string | null | undefined) => formatter().percent(v);
const formatArea = (v: string | null | undefined) => formatter().area(v);
const formatDate = (v: string | null | undefined) => formatter().date(v);
const formatDateTime = (v: string | null | undefined) => formatter().dateTime(v);
import {
  decimalInput,
  percentInput,
  toDecimalString,
  toFraction,
  toMoneyString,
  toRate,
  toSignedMoneyString,
  toYear,
  type Parsed,
} from "./input";
import {
  ACQUISITION_COST_KIND,
  ALLOCATION_CATEGORY,
  CHECK,
  CONFIDENCE,
  DEPOSIT_FORM,
  DOCUMENT_SUBTYPE,
  DOCUMENT_TYPE,
  EVENT_TYPE,
  EXTRACTION_KIND,
  GRUNDSTEUER_MODE,
  GRUNDSTEUER_RHYTHM,
  LINK_TARGET,
  options,
  PRIVATE_COST_CLASS,
  PROPERTY_KIND,
  RECORD_MODE,
  RENT_MODEL,
} from "./labels";
import { OPERATING_COSTS_TERMS } from "./taxInputs";
import { payInputsHref } from "./taxYear";
import { Notice } from "./ui";

const POLL_MS = 2_000;
const IN_FLIGHT: readonly ProposalStatus[] = ["queued", "running"];

// --- what each field is -----------------------------------------------------------------

/** "unit" / "tenancy" / "loan": the id of one of the workspace's units / tenancies / loans,
 * picked from a list.
 * "bool": a checkbox; ticked is sent as true, unticked is left out (the *Create default, false). */
type FieldKind =
  | "text"
  | "money"
  | "date"
  | "year"
  | "count"
  | "rate"
  | "area"
  | "fraction"
  | "enum"
  | "fixed"
  | "unit"
  | "tenancy"
  | "loan"
  | "bool"
  // Phase 6 step 4: units of a security (signed, up to 10 decimals) and a value picked from
  // the record's own options (a depot reading's account).
  | "units"
  | "offered";

/** The kinds picked from the workspace's records. */
type PickKind = "unit" | "tenancy" | "loan";

function isPick(kind: FieldKind): kind is PickKind {
  return kind === "unit" || kind === "tenancy" || kind === "loan";
}

interface FieldSpec {
  label: string;
  kind: FieldKind;
  choices?: Readonly<Record<string, string>>;
  /** What the value is and where it stands, behind the "i" next to the label. */
  description?: string;
  /** An "area" value's decimal places, when not 2 (a unit's rooms: 1, as the backend's Rooms). */
  decimals?: number;
  /** A "money" value that may be negative (a Bescheid's zvE in a loss year). */
  signed?: boolean;
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

/** The categories a cost paid from another account may carry (PrivateCostValue.category,
 * phase 2 D135): the deductible ones, as backend schemas/tax.PRIVATE_COST_CATEGORIES. */
const PRIVATE_COST_CODES = [
  "loan_interest",
  "loan_fees",
  "hausgeld_umlagefaehig",
  "hausgeld_verwaltung",
  "property_management",
  "grundsteuer",
  "insurance",
  "electricity",
  "repairs",
  "professional_fees",
  "bank_fees",
] as const;

/** A depot movement's kind (backend schemas/household.MovementKind, the ones a depot
 * document proposes). */
const MOVEMENT_KIND: Readonly<Record<string, string>> = localized(MOVEMENT_KIND_DE, MOVEMENT_KIND_EN);

type FieldTexts = (typeof proposalFields)["de"];

/** The field specs in one language (phase 10 D284): built on first use per language, so the
 * labels and choices follow the device's language without freezing it at module load. */
interface Specs {
  FIELDS: Record<string, Record<string, FieldSpec>>;
  USEFUL_LIFE_FIELDS: Readonly<Record<string, FieldSpec>>;
  PRIVATE_COST_AMOUNT: FieldSpec;
  MANAGEMENT_FEE: FieldSpec;
}

const specCache: Partial<Record<Locale, Specs>> = {};

function specs(): Specs {
  const locale = getLocale();
  return (specCache[locale] ??= buildSpecs(proposalFields[locale]));
}

function buildSpecs(m: FieldTexts): Specs {
  const PRIVATE_COST_CATEGORY: Readonly<Record<string, string>> = Object.fromEntries(
    PRIVATE_COST_CODES.map((c) => [c, ALLOCATION_CATEGORY[c]]),
  );

  /** A Gutachten's shorter useful life (tax_input:useful_life, D147): the years and the first year,
   * both computed by Hausbuch from the Gutachten's Restnutzungsdauer and Stichtag. */
  const USEFUL_LIFE_FIELDS: Readonly<Record<string, FieldSpec>> = {
    "value.years": { label: m.ulYearsL, kind: "count", description: m.ulYearsD },
    tax_year: { label: m.ulFromL, kind: "year", description: m.ulFromD },
  };

  /** A cost paid from another account's amount (D135): one row per bill and category, so it
   * replaces only the same bill's earlier row, never another bill's. */
  const PRIVATE_COST_AMOUNT: FieldSpec = { label: m.amountL, kind: "money", description: m.privateCostAmountD };

  const PROPERTY_FIELDS: Record<string, FieldSpec> = {
    label: text(m.nameL, m.propLabelD),
    kind: choice(m.kindL, PROPERTY_KIND, m.propKindD),
    "address.street": text(m.streetL, m.streetD),
    "address.house_number": text(m.houseNumberL, m.houseNumberD),
    "address.postal_code": text(m.postalCodeL, m.postalCodeD),
    "address.city": text(m.cityL, m.cityD),
    build_year: { label: m.buildYearL, kind: "year", description: m.buildYearD },
    notarized_on: date(m.notarizedL, m.notarizedD),
    purchase_date: date(m.purchaseDateL, m.purchaseDateD),
    purchase_price: money(m.purchasePriceL, m.purchasePriceD),
    living_area_m2: { label: m.livingAreaL, kind: "area", description: m.livingAreaD },
  };

  const DEPOT_ACCOUNT: FieldSpec = { label: m.depotL, kind: "offered", description: m.depotD };
  const ISIN: FieldSpec = text(m.isinL, m.isinD);
  const SECURITY_NAME: FieldSpec = text(m.securityL, m.securityD);

  /** A depot reading's records (phase 6 step 4): movement:<n> and holding:<n>. */
  const MOVEMENT_FIELDS: Record<string, FieldSpec> = {
    account_id: DEPOT_ACCOUNT,
    isin: ISIN,
    name: SECURITY_NAME,
    kind: choice(m.movementKindL, MOVEMENT_KIND, m.movementKindD),
    quantity: { label: m.unitsL, kind: "units", description: m.unitsD },
    eur_amount: { label: m.amountL, kind: "money", signed: true, description: m.movementAmountD },
    fee_eur: money(m.feesL, m.feesD),
    tax_withheld_eur: money(m.taxWithheldL, m.taxWithheldD),
    occurred_on: date(m.dateL, m.occurredD),
    external_ref: text(m.referenceL, m.movementRefD),
  };

  const HOLDING_FIELDS: Record<string, FieldSpec> = {
    account_id: DEPOT_ACCOUNT,
    isin: ISIN,
    name: SECURITY_NAME,
    as_of: date(m.asOfL, m.asOfD),
    declared_units: { label: m.declaredUnitsL, kind: "units", description: m.declaredUnitsD },
    declared_value_eur: money(m.declaredValueL, m.declaredValueD),
  };

  /** By record key prefix, then by the *Create / *Patch field name the backend uses. */
  const FIELDS: Record<string, Record<string, FieldSpec>> = {
    movement: MOVEMENT_FIELDS,
    holding: HOLDING_FIELDS,
    typing: {
      type: choice(m.docTypeL, DOCUMENT_TYPE, m.docTypeD),
      subtype: choice(m.subtypeL, DOCUMENT_SUBTYPE, m.subtypeD),
      title: text(m.titleL, m.titleD),
      document_date: date(m.docDateL, m.docDateD),
      tax_year: { label: m.taxYearL, kind: "year", description: m.typingTaxYearD },
      // The document's hand-entry fields (D132), as the typing form on the page offers them.
      amount_total: { label: m.amountTotalL, kind: "money", signed: true, description: m.amountTotalD },
      counterparty: text(m.counterpartyL, m.counterpartyD),
      period_start: date(m.periodStartL, m.periodStartD),
      period_end: date(m.periodEndL, m.periodEndD),
      reference: text(m.referenceL, m.typingRefD),
      notes: text(m.noteL, m.freeTextD),
    },
    property: PROPERTY_FIELDS,
    correction: PROPERTY_FIELDS,
    unit: {
      label: text(m.nameL, m.unitLabelD),
      mea: { label: m.meaL, kind: "fraction", description: m.meaD },
      floor_area_m2: { label: m.floorAreaL, kind: "area", description: m.floorAreaD },
      rooms: { label: m.roomsL, kind: "area", decimals: 1, description: m.roomsD },
    },
    price_split: {
      building_share: { label: m.buildingShareL, kind: "rate", description: m.buildingShareD },
      note: text(m.noteL, m.splitNoteD),
    },
    ownership: {
      shares: { label: m.sharesL, kind: "fixed", description: m.sharesD },
      effective_date: date(m.validFromL, m.ownershipFromD),
    },
    cost: {
      kind: choice(m.kindL, ACQUISITION_COST_KIND, m.costKindD),
      amount: money(m.amountL, m.costAmountD),
      paid_on: date(m.paidOnL, m.paidOnD),
      note: text(m.noteL, m.costNoteD),
    },
    loan: {
      lender: text(m.lenderL, m.lenderD),
      contract_number: text(m.contractNumberL, m.contractNumberD),
      principal: money(m.principalL, m.principalD),
      nominal_rate: { label: m.nominalRateL, kind: "rate", description: m.nominalRateD },
      monthly_payment: money(m.monthlyPaymentL, m.monthlyPaymentD),
      fixed_until: date(m.fixedUntilL, m.fixedUntilD),
      follow_up_rate: { label: m.followUpL, kind: "rate", description: m.followUpD },
      disbursed_on: date(m.disbursedL, m.disbursedD),
      first_payment_on: date(m.firstPaymentL, m.firstPaymentD),
      interest_only_before_first_payment: { label: m.interestOnlyL, kind: "bool", description: m.interestOnlyD },
      account_iban: text(m.loanIbanL, m.loanIbanD),
      signed_on: date(m.contractDateL, m.loanSignedD),
    },
    tenant: {
      full_name: text(m.tenantNameL, m.tenantNameD),
      email: text(m.emailL, m.contactD),
      phone: text(m.phoneL, m.contactD),
      iban: text(m.ibanL, m.tenantIbanD),
    },
    tenancy: {
      unit_id: { label: m.unitL, kind: "unit", description: m.tenancyUnitD },
      start_date: date(m.startL, m.startD),
      signed_on: date(m.contractDateL, m.tenancySignedD),
      fixed_term_until: date(m.fixedTermL, m.fixedTermD),
      end_date: date(m.endL, m.endD),
      rent_model: choice(m.rentModelL, RENT_MODEL, m.rentModelD),
      deposit_amount: money(m.depositL, m.depositD),
      deposit_form: choice(m.depositFormL, DEPOSIT_FORM, m.depositFormD),
      deposit_bank: text(m.depositBankL, m.depositBankD),
      deposit_iban: text(m.depositIbanL, m.depositIbanD),
      // D152: both read from the lease's clauses and mapped by Hausbuch, never by the model.
      operating_costs_terms: choice(m.opCostsL, OPERATING_COSTS_TERMS, m.opCostsD),
      rent_due_day: { label: m.dueDayL, kind: "count", description: m.dueDayD },
    },
    rent: {
      cold_rent: money(m.coldRentL, m.coldRentD),
      operating_costs_prepayment: money(m.prepaymentL, m.prepaymentD),
      effective_from: date(m.validFromL, m.rentFromD),
    },
    deposit_received: {
      amount: money(m.amountL, m.depositReceivedD),
      received_on: date(m.receivedOnL, m.receivedOnD),
      tenancy_id: { label: m.tenancyL, kind: "tenancy", description: m.tenancyD },
    },
    // A certificate's figure for the tax year (phase 3 §9): the record's key names the kind
    // (tax_input:interest_certified or tax_input:ruecklage_spent); both propose all their fields.
    tax_input: {
      tax_year: { label: m.taxYearL, kind: "year", description: m.certTaxYearD },
      // A bill's part paid from another account (tax_input:private_cost:<category>, D135).
      "value.cost_class": choice(m.kindL, { financing: PRIVATE_COST_CLASS.financing, other: PRIVATE_COST_CLASS.other }, m.costClassD),
      "value.category": choice(m.categoryL, PRIVATE_COST_CATEGORY, m.categoryD),
      "value.label": text(m.nameL, m.privateLabelD),
      loan_id: { label: m.loanL, kind: "loan", description: m.loanD },
      "value.amount": money(m.amountL, m.taxAmountD),
      // A repair paid from another account (phase 3 D137): when the work was done and its net,
      // for the 15 % limit on renovation in the first three years after the purchase.
      "value.performed_on": date(m.performedOnL, m.performedOnD),
      "value.amount_net": money(m.netL, m.netD),
      // The Grundsteuerbescheid's schedule (tax_input:grundsteuer_schedule, phase 5 D126): one
      // date and one amount per Fälligkeit; empty slots are left out when it is applied.
      "value.annual_amount": money(m.annualL, m.annualD),
      ...grundsteuerSlots(m),
      "value.rhythm": choice(m.rhythmL, GRUNDSTEUER_RHYTHM, m.rhythmD),
      "value.payment_mode": choice(
        m.paymentModeL,
        { remind: GRUNDSTEUER_MODE.remind, sepa_mandate: GRUNDSTEUER_MODE.sepa_mandate },
        m.paymentModeD,
      ),
    },
    // A payment proof's year move (phase 3 D143): private_cost_year:<n> patches one privately paid
    // cost to the year it was paid in; applied as withdraw + create.
    private_cost_year: {
      tax_year: { label: m.taxYearL, kind: "year", description: m.yearMoveD },
    },
    // The Einkommensteuerbescheid's zvE (phase 4b D129): income_input:bescheid_zve, household-wide.
    income_input: {
      tax_year: { label: m.taxYearL, kind: "year", description: m.zveTaxYearD },
      "value.amount": { label: m.zveL, kind: "money", signed: true, description: m.zveD },
    },
  };

  /** A Verwaltervertrag's Betrag holds its monthly fee with MwSt., so it is named so (as on the page). */
  const MANAGEMENT_FEE: FieldSpec = { label: m.feeL, kind: "money", signed: true, description: m.feeD };

  return { FIELDS, USEFUL_LIFE_FIELDS, PRIVATE_COST_AMOUNT, MANAGEMENT_FEE };
}

/** Four instalment slots of the Grundsteuer schedule: a due day and an amount each. */
function grundsteuerSlots(m: FieldTexts): Record<string, FieldSpec> {
  const slots: Record<string, FieldSpec> = {};
  for (const n of [1, 2, 3, 4]) {
    slots[`value.due_on_${n}`] = date(m.slotDueL(n), m.slotDueD);
    slots[`value.amount_${n}`] = money(m.slotAmountL(n), m.slotAmountD);
  }
  return slots;
}

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
    "living_area_m2",
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
    "operating_costs_terms",
    "rent_due_day",
  ],
  // InitialRent
  rent: ["cold_rent", "operating_costs_prepayment", "effective_from"],
  // services/depot.DepotMovementValues and DepotHoldingValues (phase 6 step 4)
  movement: ["account_id", "kind", "isin", "name", "occurred_on", "quantity", "eur_amount", "fee_eur", "tax_withheld_eur", "external_ref"],
  holding: ["account_id", "isin", "name", "as_of", "declared_units", "declared_value_eur"],
  // _DepositValues (DepositReceivedBody + tenancy_id)
  deposit_received: ["tenancy_id", "amount", "received_on"],
  // IncomeInputCreate, kind bescheid_zve (the apply sets the kind and this document)
  income_input: ["tax_year", "value.amount"],
  // GrundsteuerSchedule (by the record's whole key: the certificates' tax_input records keep
  // their proposed fields only), so the payment mode can be chosen before applying.
  "tax_input:grundsteuer_schedule": [
    "tax_year",
    "value.annual_amount",
    "value.due_on_1",
    "value.amount_1",
    "value.due_on_2",
    "value.amount_2",
    "value.due_on_3",
    "value.amount_3",
    "value.due_on_4",
    "value.amount_4",
    "value.rhythm",
    "value.payment_mode",
  ],
};

/** The hand-entry fields a typing record may carry (D132), in the page form's order. They are
 * shown only when the record carries them: a kind without hand fields gets no empty boxes. */
const HAND_FIELDS: readonly string[] = ["amount_total", "counterparty", "period_start", "period_end", "reference", "notes"];

function isHandField(record: ProposalRecord, name: string): boolean {
  return prefixOf(record.key)[0] === "typing" && HAND_FIELDS.includes(name);
}

/** A tax_input record's title, by the kind its key names. */
const TAX_INPUT_TITLE: Record<string, string> = localized(TAX_INPUT_TITLE_DE, TAX_INPUT_TITLE_EN);

/** The extraction fields a derived value names in `derived_from`. */
const DERIVED_INPUT: Record<string, string> = localized(DERIVED_INPUT_DE, DERIVED_INPUT_EN);

function lookup(map: Readonly<Record<string, string>>, key: string): string {
  return map[key] ?? key;
}

/** A payment proof read without a bill (phase 3 D149): the record that links it to one of the
 * bills the code offers (options.target_id, the draft's bills still waiting for a proof), or to
 * none („keine dieser Rechnungen“, the record skipped). */
const PROOF_LINK_KEY = "link:document";

function isProofLink(record: ProposalRecord): boolean {
  return record.key === PROOF_LINK_KEY;
}

/** The proof's bill as the form stands: the picked bill's id, "" for none. */
function pickedBill(state: RecordState | undefined): string {
  return state && !state.skip ? (state.fields.target_id?.raw ?? "") : "";
}

/** The year moves (private_cost_year:<n>) the code proposed for the bill it pre-selected
 * apply only while that bill stays picked: when the user picks another bill or none, they are
 * hidden and sent as skipped (the backend refuses a move of a bill the proof is not linked to). */
function proofMovesDeclined(records: ProposalRecord[], state: Record<string, RecordState>): boolean {
  const link = records.find(isProofLink);
  if (!link) return false;
  const proposed = scalar(link.fields?.target_id?.value);
  return proposed === "" || pickedBill(state[link.key]) !== proposed;
}

function isYearMove(record: ProposalRecord): boolean {
  return prefixOf(record.key)[0] === "private_cost_year";
}

/** A depot record's security: its name, else its ISIN. */
function securityOf(record: ProposalRecord): string {
  return scalar(record.fields?.name?.value) || scalar(record.fields?.isin?.value) || translate(proposalsText).security;
}

function prefixOf(key: string): [string, string] {
  const at = key.indexOf(":");
  return at < 0 ? [key, ""] : [key.slice(0, at), key.slice(at + 1)];
}

function specFor(record: ProposalRecord, name: string): FieldSpec {
  const { FIELDS, USEFUL_LIFE_FIELDS, PRIVATE_COST_AMOUNT, MANAGEMENT_FEE } = specs();
  if (name === "amount_total" && isHandField(record, name) && record.fields?.subtype?.value === "management_contract") return MANAGEMENT_FEE;
  if (name === "value.amount" && record.key.startsWith("tax_input:private_cost:")) return PRIVATE_COST_AMOUNT;
  if (record.key === "tax_input:useful_life" && USEFUL_LIFE_FIELDS[name]) return USEFUL_LIFE_FIELDS[name];
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
  const prefix = prefixOf(record.key)[0];
  let schema = record.mode === "create" ? (CREATE_FIELDS[record.key] ?? CREATE_FIELDS[prefix]) : undefined;
  // The hand fields the typing record carries, after the typing's own, in the page form's order.
  if (schema && prefix === "typing") schema = [...schema, ...HAND_FIELDS.filter((name) => record.fields?.[name] !== undefined)];
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

/** A cost record's kind (cost:<kind>), and for cost:<kind>:other_notice the rest of a
 * Grunderwerbsteuer row that belongs to the other buyer's notice (D145). */
function costLabel(rest: string): string {
  const [kind, part] = prefixOf(rest);
  const label = lookup(ACQUISITION_COST_KIND, kind);
  return part === "other_notice" ? translate(proposalsText).costOtherNotice(label) : label;
}

function recordTitle(record: ProposalRecord): string {
  const m = translate(proposalsText);
  const [prefix, rest] = prefixOf(record.key);
  switch (prefix) {
    case "typing":
      return m.typing;
    case "property":
      return m.property;
    case "correction":
      return m.correction(specFor(record, rest).label);
    case "unit": {
      const label = record.fields?.label?.value;
      return typeof label === "string" && label ? m.unitNamed(label) : m.unit;
    }
    case "price_split":
      return m.priceSplit;
    case "ownership":
      return m.ownership;
    case "cost":
      return m.cost(costLabel(rest));
    case "loan":
      return m.loan;
    case "tenant":
      return m.tenant(rest);
    case "tenancy":
      return m.tenancy;
    case "rent":
      return m.rent;
    case "deposit_received":
      return EVENT_TYPE["tenancy.deposit_received"];
    case "link":
      if (rest === "document") return m.proofBill;
      return m.linkTo(lookup(LINK_TARGET, rest));
    case "tax_input":
      if (rest.startsWith("private_cost:")) {
        return m.privateCost(lookup(ALLOCATION_CATEGORY, rest.slice("private_cost:".length)));
      }
      return lookup(TAX_INPUT_TITLE, rest);
    case "income_input":
      return m.zve;
    case "private_cost_year":
      return m.yearMove;
    case "movement":
      return `${lookup(MOVEMENT_KIND, scalar(record.fields?.kind?.value))}: ${securityOf(record)}`;
    case "holding":
      return m.holding(securityOf(record));
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
    case "units":
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

/** Units of a security, signed, to the table's 10 decimals: "-15,25" → "-15.25". */
function toUnits(raw: string): Parsed<string> {
  const text = raw.trim();
  const negative = text.startsWith("-");
  const parsed = toDecimalString(negative ? text.slice(1) : text, 10);
  if (!parsed.ok || !negative) return parsed;
  return { ok: true, value: /^0+(\.0+)?$/.test(parsed.value) ? parsed.value : `-${parsed.value}` };
}

/** A whole number of at least 1, e.g. a useful life in years (D147). */
function toCount(raw: string): Parsed<number> {
  const text = raw.trim();
  if (!/^\d+$/.test(text) || Number(text) < 1) return { ok: false, message: translate(proposalsText).countWholeNumber };
  return { ok: true, value: Number(text) };
}

/** The input's text as the value to send; null for an empty input. */
function fromRaw(spec: FieldSpec, raw: string): Parsed<unknown> {
  if (raw.trim() === "") return { ok: true, value: null };
  switch (spec.kind) {
    case "money":
      return spec.signed ? toSignedMoneyString(raw) : toMoneyString(raw);
    case "rate":
      return toRate(raw, 6);
    case "area":
      return toDecimalString(raw, spec.decimals ?? 2);
    case "year":
      return toYear(raw);
    case "count":
      return toCount(raw);
    case "units":
      return toUnits(raw);
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
    case "units":
      return decimalInput(scalar(value));
    case "fraction":
      return isFraction(value) ? formatShare(value) : "–";
    case "date":
      return formatDate(scalar(value));
    case "enum":
      return lookup(spec.choices ?? {}, scalar(value));
    case "bool":
      return value === true ? translate(proposalsText).yes : translate(proposalsText).no;
    case "fixed":
    case "unit":
    case "tenancy":
    case "loan":
      return fixed(value);
    default:
      return scalar(value) || "–";
  }
}

/** Ids and structured values as text: ownership shares (matched by owner names, shown, not
 * edited) and the unit, tenancy or loan an id names (for a stored value, or one the list lacks). */
function fixedText(directory: Directory | null): (value: unknown) => string {
  const m = translate(proposalsText);
  return (value) => {
    if (typeof value === "string") {
      const unit = directory?.units.find((u) => u.id === value);
      if (unit) return unit.label;
      const tenancy = directory?.tenancies.find((t) => t.id === value);
      if (tenancy) return m.tenancyFrom(formatDate(tenancy.start_date));
      const loan = directory?.loans.find((l) => l.id === value);
      if (loan) return loanLabel(loan);
      return m.assignedEntry;
    }
    if (Array.isArray(value)) {
      return value
        .map((entry: unknown) => {
          if (typeof entry !== "object" || entry === null || !("owner_id" in entry) || !("share" in entry)) return "?";
          const owner = directory?.owners.find((o) => o.id === entry.owner_id);
          const share = isFraction(entry.share) ? formatShare(entry.share) : "?";
          return `${owner?.full_name ?? m.owner}: ${share}`;
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
  const m = translate(proposalsText);
  const only = directory?.properties.length === 1 ? directory.properties[0] : undefined;
  switch (kind) {
    case "purchase_contract":
      return { href: "/properties/new", where: m.createProperty };
    case "loan_contract":
      return only ? { href: `/loans/new?property=${only.id}`, where: m.createLoan } : { href: "/", where: m.createLoanAtProperty };
    case "invoice":
    case "bescheid":
      return only
        ? { href: `/properties/${only.id}?tab=nebenkosten`, where: m.propertyCosts }
        : { href: "/", where: m.costsAtProperty };
    case "lease":
      return { href: "/tenancies/new", where: m.createTenancy };
    default:
      return { href: "#typisierung", where: m.typingBelow };
  }
}

function ManualEntry({ kind, directory, inCard = false }: { kind: ExtractionKind | null; directory: Directory | null; inCard?: boolean }) {
  const t = useT(proposalsText);
  // inCard: the typing card below is the document's one form while the proposal is pending (D132).
  const { href, where } = inCard && manualEntry(kind, directory).href === "#typisierung" ? { href: "#vorschlag", where: t.typingInSuggestion } : manualEntry(kind, directory);
  return (
    <p>
      <Link href={href}>{t.manualEntry}</Link>{" "}
      <span className="muted">({where}; {inCard ? t.manualInCard : t.manualBelow})</span>
    </p>
  );
}

/** A proposal whose typing card is the document's one form (D132): one shown for review (ready,
 * or blocked by the tenant gate with its typing). While it is, the page hides its typing form. */
export function isPendingReview(proposal: Proposal): boolean {
  return proposal.status === "ready" || (proposal.status === "blocked_tenant_data" && (proposal.records ?? []).length > 0);
}

/** A pending proposal that proposes a link (link:property, link:loan, link:tenancy, and a payment
 * proof's bill, link:document, D149): its card is
 * then the one place to link, and the page hides its own link form (D132). */
export function proposesLink(proposal: Proposal): boolean {
  return isPendingReview(proposal) && (proposal.records ?? []).some((record) => record.entity_type === "link");
}

/**
 * The "Vorschlag" section of a document page. `onApplied` reloads the page's document (its
 * typing and links change with an apply; a finished read changes its extraction).
 * `onPendingChange` tells the page whether a proposal is pending review (isPendingReview), and
 * `onPendingLinksChange` whether that proposal proposes a link (proposesLink): undefined while
 * the proposals load, so the page shows neither of its forms until it knows.
 */
export function ProposalSection({
  detail,
  directory,
  onApplied,
  onPendingChange,
  onPendingLinksChange,
}: {
  detail: DocumentDetail;
  directory: Directory | null;
  onApplied: () => void;
  onPendingChange?: (pending: boolean | undefined) => void;
  onPendingLinksChange?: (pending: boolean | undefined) => void;
}) {
  const t = useT(proposalsText);
  const documentId = detail.document.id;
  const { proposal, error, setProposal, reload } = useCurrentProposal(documentId, onApplied);
  // A failed load counts as none pending: the page's own form is then the way to type.
  const pending = proposal ? isPendingReview(proposal) : proposal === null || error ? false : undefined;
  const pendingLinks = proposal ? proposesLink(proposal) : pending;
  useEffect(() => {
    onPendingChange?.(pending);
  }, [pending, onPendingChange]);
  useEffect(() => {
    onPendingLinksChange?.(pendingLinks);
  }, [pendingLinks, onPendingLinksChange]);
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
      {busy ? t.starting : label}
    </button>
  );

  let body: ReactNode;
  if (proposal === undefined) {
    body = error ? null : <p className="muted">{t.loading}</p>;
  } else if (proposal === null && detail.document.source === "bank_sync") {
    // Phase 5 D154: a file the bank sync fetched is never read (the API refuses it too).
    body = (
      <p className="muted">{t.bankSyncFile}</p>
    );
  } else if (proposal === null) {
    body = (
      <div className="stack">
        <p className="muted">{t.notReadYet}</p>
        <label className="inline" htmlFor={tenantDataId}>
          <input id={tenantDataId} type="checkbox" checked={tenantData} onChange={(e) => setTenantData(e.target.checked)} />
          {t.containsTenantData}
        </label>
        <div className="row">{rerunButton(t.readAutomatically, null)}</div>
      </div>
    );
  } else if (IN_FLIGHT.includes(proposal.status)) {
    body = (
      <div className="notice notice-info" role="status">
        <p>
          {proposal.status === "queued" ? t.waiting : t.reading} {t.readingTakes}
        </p>
      </div>
    );
  } else if (proposal.status === "ready") {
    body = (
      <>
        {/* The apply just made, when it led to this new suggestion (D149, review F1). */}
        {applied && applied.proposal.id !== proposal.id ? <AppliedSummary result={applied} /> : null}
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
            // A payment proof linked to a bill the user picked (D149, review F1): the server may
            // have proposed moving that bill to the year paid; the list shows it next.
            if (result.applied.some((a) => a.key === PROOF_LINK_KEY && !a.skipped)) reload();
          }
        }}
        onRerun={() => void rerun(t.confirmRerunReplace)}
        busy={busy}
      />
      </>
    );
  } else if (proposal.status === "blocked_tenant_data") {
    const hasTyping = (proposal.records ?? []).length > 0;
    body = (
      <div className="stack">
        <div className="notice notice-warn" role="status">
          {tenantDocumentsAllowed ? (
            <p>{t.blockedAllowed}</p>
          ) : (
            <p>
              {t.blockedBefore}{" "}
              <Link href="/settings">{t.settings}</Link>
              {t.blockedAfter}
            </p>
          )}
        </div>
        {tenantDocumentsAllowed ? (
          <div className="row">{rerunButton(t.readAgain, hasTyping ? t.confirmRerunReplace : null)}</div>
        ) : null}
        <ManualEntry kind={proposal.kind} directory={directory} inCard={hasTyping} />
        {hasTyping ? (
          <>
            <p className="muted">{t.typingOnly}</p>
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
  } else if (proposal.status === "failed" && proposal.error_code === "proposal.bank_file_not_read") {
    // Phase 6 D176 (3), step 4: a card or depot statement is read by code on its account; a new
    // read would fail the same way, and nothing is typed by hand. „Auf Konto importieren“ takes
    // the stored file there, with no second upload.
    body = (
      <div className="stack">
        {/* The form explains itself; the code's sentence only when it has nothing to offer. */}
        {detail.statement_offer || detail.statement_imports?.length ? null : (
          <div className="notice notice-info" role="status">
            <p>{aiErrorText(proposal.error_code)}</p>
          </div>
        )}
        <ImportStoredStatement
          documentId={documentId}
          detail={detail}
          onImported={() => {
            reload();
            onApplied();
          }}
        />
      </div>
    );
  } else if (proposal.status === "failed" && proposal.error_code === "proposal.exchange_export_not_read") {
    // Phase 6 D166, step 5a: an exchange export is read by code on its exchange account; a new
    // read would stop the same way, and nothing is typed by hand here.
    body = (
      <div className="stack">
        <div className="notice notice-info" role="status">
          <p>{aiErrorText(proposal.error_code)}</p>
        </div>
        <div className="row">
          <Link href="/accounts">{t.openAccounts}</Link>
        </div>
      </div>
    );
  } else if (proposal.status === "failed") {
    body = (
      <div className="stack">
        <div className="notice notice-error" role="alert">
          <p>
            {t.couldNotRead} {aiErrorText(proposal.error_code)}
            {proposal.error_code ? <code className="error-code">{proposal.error_code}</code> : null}
          </p>
        </div>
        <ManualEntry kind={proposal.kind} directory={directory} />
        <div className="row">{rerunButton(t.readAgain, null)}</div>
      </div>
    );
  } else {
    // applied, rejected, superseded
    body = (
      <div className="stack">
        {applied ? (
          <AppliedSummary result={applied} />
        ) : (
          <p className="muted">{t.decided(proposal.status, proposal.decided_at ? formatDateTime(proposal.decided_at) : null)}</p>
        )}
        <div className="row">{rerunButton(t.readAgain, t.confirmRerunNew)}</div>
      </div>
    );
  }

  return (
    <section aria-labelledby="vorschlag" className="stack">
      <h2 id="vorschlag">
        {t.suggestion}
        {proposal?.kind ? <span className="tag proposal-kind">{EXTRACTION_KIND[proposal.kind]}</span> : null}
      </h2>
      {error ? (
        <div>
          <ErrorNotice error={error} />
          <button type="button" className="secondary" onClick={reload}>
            {t.retry}
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
  const t = useT(proposalsText);
  const property =
    result.applied.find((a) => a.key === "property" && a.entity_id)?.entity_id ??
    result.applied.find((a) => a.key === "link:property" && a.entity_id)?.entity_id ??
    null;
  // A Bescheid's zvE links to Eure Angaben of the year it was saved for (phase 10 D281).
  const zveYear =
    Number(scalar((result.proposal.records ?? []).find((r) => r.key.startsWith("income_input"))?.fields?.tax_year?.value)) || null;
  const done = result.applied.filter((a) => !a.skipped);
  return (
    <Notice>
      {t.applied}{" "}
      {done.length > 0 ? (
        <>
          {done.map((entity, index) => {
            const href = entityHref(entity, property, zveYear);
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
        t.nothingChanged
      )}
    </Notice>
  );
}

function appliedLabel(entity: AppliedEntity): string {
  const m = translate(proposalsText);
  const [prefix, rest] = prefixOf(entity.key);
  const verb = m.verb(entity.mode);
  switch (prefix) {
    case "typing":
      return m.typed;
    case "link":
      if (rest === "document") return m.proofLinked;
      return m.linkedWith(lookup(LINK_TARGET, rest));
    case "property":
      return m.propertyDone(verb);
    case "correction":
      return m.corrected(lookup({ notarized_on: m.notarizedShort, purchase_date: m.handoverShort }, rest));
    case "unit":
      return m.unitDone(verb);
    case "price_split":
      return m.priceSplitDone;
    case "ownership":
      return m.ownershipDone;
    case "cost":
      return m.costDone(costLabel(rest), entity.mode === "create");
    case "loan":
      return m.loanDone(verb);
    case "tenant":
      return m.tenantDone(rest, verb);
    case "tenancy":
      return m.tenancyDone(verb);
    case "rent":
      return m.rentDone;
    case "deposit_received":
      return m.depositDone;
    case "tax_input":
      return m.taxInputDone(lookup(TAX_INPUT_TITLE, rest));
    case "income_input":
      return m.zveDone;
    case "private_cost_year":
      return m.yearMoveDone;
    case "movement":
      return m.movementDone;
    case "holding":
      return m.holdingDone;
    default:
      return entity.key;
  }
}

function entityHref(entity: AppliedEntity, propertyId: string | null, zveYear: number | null): string | null {
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
    case "income_input":
      // „zvE laut Bescheid“ under Steuern › Eure Angaben › Arbeitslohn (phase 10 D281).
      return payInputsHref(zveYear ?? Number(todayIso().slice(0, 4)));
    case "document":
      return `/documents/${id}`; // a payment proof's bill (D149)
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
    // An unchanged record would write nothing: it is sent as skipped. A proof's bill the code
    // could not pick (D149) starts at „keine dieser Rechnungen“: nothing is linked unless picked.
    const noBill = isProofLink(record) && (fields.target_id?.raw ?? "") === "";
    state[record.key] = { skip: record.mode === "unchanged" || noBill, fields };
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
  const movesDeclined = proofMovesDeclined(records, state);
  for (const record of records) {
    const recordState = state[record.key];
    const declined =
      (isProofLink(record) && pickedBill(recordState) === "") || (movesDeclined && isYearMove(record));
    if (!recordState || recordState.skip || record.mode === "unchanged" || declined) {
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
      // proposed) and the *Create default applies. A hand field is sent empty as null: the
      // backend leaves a hand field it is not sent as it is, so a clear must be explicit (D132).
      if (parsed.value !== null) values[name] = parsed.value;
      else if (isHandField(record, name)) values[name] = null;
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
  const t = useT(proposalsText);
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
    if (!window.confirm(t.confirmReject)) return;
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
    <form className="stack proposal" onSubmit={apply} noValidate aria-label={t.reviewForm}>
      <DocumentExplanationBox explanation={proposal.explanation} />
      <p className="muted">{t.reviewIntro}</p>
      <div className="proposal-records">
        {records.map((record) => {
          const recordState = state[record.key];
          if (!recordState) return null;
          if (isYearMove(record) && proofMovesDeclined(records, state)) return null;
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
          {busy === "apply" ? t.applying : t.apply}
        </button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void reject()}>
          {busy === "reject" ? t.rejecting : t.reject}
        </button>
        {onRerun ? (
          <button type="button" className="secondary" disabled={busy !== null || rerunBusy} onClick={onRerun}>
            {t.readAgain}
          </button>
        ) : null}
      </div>
    </form>
  );
}

function skipLabel(record: ProposalRecord): string {
  const m = translate(proposalsText);
  if (record.entity_type === "link") return m.skipLink;
  if (record.mode === "create") return m.skipCreate;
  return m.skipChange;
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
  const t = useT(proposalsText);
  const headingId = useId();
  const skipId = useId();
  const title = recordTitle(record);
  const unchanged = record.mode === "unchanged";
  const proofLink = isProofLink(record);
  const fields = proofLink ? [] : visibleRows(record, state);
  const notes = record.notes ?? [];

  return (
    <section
      className={state.skip && !unchanged && !proofLink ? "card proposal-card proposal-skipped" : "card proposal-card"}
      aria-labelledby={headingId}
    >
      <div className="row card-head proposal-card-head">
        <h3 id={headingId}>{title}</h3>
        <span className={record.mode === "correction" ? "corrected" : record.mode === "create" ? "badge" : "tag"}>{RECORD_MODE[record.mode]}</span>
        <span className="spacer" />
        {unchanged || proofLink ? null : (
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
      {proofLink ? (
        <BillPicker record={record} state={state} onChange={onChange} />
      ) : record.entity_type === "link" ? (
        <p className="proposal-link">
          {t.willLink}<strong>{linkTarget(record, directory)}</strong>
        </p>
      ) : null}
      {unchanged ? <p className="muted">{t.unchangedNote}</p> : null}
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
  const m = translate(proposalsText);
  const [, target] = prefixOf(record.key);
  const id = record.target_id;
  if (!id) return target === "property" ? m.newProperty : target === "loan" ? m.newLoan : m.newEntry;
  switch (target) {
    case "property":
      return directory?.properties.find((p) => p.id === id)?.label ?? m.property;
    case "loan": {
      const loan = directory?.loans.find((l) => l.id === id);
      return loan ? `${loan.lender}${loan.contract_number ? ` ${loan.contract_number}` : ""}` : m.loan;
    }
    case "tenancy": {
      const tenancy = directory?.tenancies.find((t) => t.id === id);
      return tenancy ? m.tenancyFrom(formatDate(tenancy.start_date)) : m.tenancy;
    }
    case "tenant":
      return tenantName(directory?.tenants.find((t) => t.id === id));
    default:
      return lookup(LINK_TARGET, target);
  }
}

/** The bills a payment proof may pay (phase 3 D149), each named by the code with its title,
 * date and amount and whether the proof's amount fits it, plus „keine dieser Rechnungen“. One
 * click picks; with „Übernehmen“ the proof is linked to the picked bill as the to-do's upload
 * links it, and nothing is linked for none. */
function BillPicker({
  record,
  state,
  onChange,
}: {
  record: ProposalRecord;
  state: RecordState;
  onChange: (change: (current: RecordState) => RecordState) => void;
}) {
  const t = useT(proposalsText);
  const name = useId();
  const picked = pickedBill(state);
  const offered = record.options?.target_id ?? [];
  function pick(bill: string) {
    onChange((current) => {
      const field = current.fields.target_id ?? { raw: "", take: true };
      return { ...current, skip: bill === "", fields: { ...current.fields, target_id: { ...field, raw: bill } } };
    });
  }
  return (
    <>
      <p className="proposal-link">{t.proofPays}</p>
      <div className="proposal-options" role="radiogroup" aria-label={t.proofBill}>
        {offered.map((option) => (
          <span key={option.value} className="row">
            <label className="inline">
              <input type="radio" name={name} checked={picked === option.value} onChange={() => pick(option.value)} />
              {option.label}
            </label>
            <Link href={`/documents/${option.value}`} target="_blank" rel="noopener">
              {t.viewBill}
            </Link>
          </span>
        ))}
        <label className="inline">
          <input type="radio" name={name} checked={picked === ""} onChange={() => pick("")} />
          {t.noneOfThese}
        </label>
      </div>
    </>
  );
}

/** The values the code offers for a field it could not fill by itself (D139: the dwellings of a
 * Wohnflächenberechnung); one click puts the value into the field, which stays editable. */
function FieldOptions({
  label,
  options,
  raw,
  disabled,
  onPick,
}: {
  label: string;
  options: { raw: string; label: string }[];
  raw: string;
  disabled: boolean;
  onPick: (raw: string) => void;
}) {
  const t = useT(proposalsText);
  const name = useId();
  return (
    <div className="proposal-options" role="radiogroup" aria-label={t.fieldOptions(label)}>
      {options.map((option, index) => (
        <label key={`${index}:${option.raw}`} className="inline">
          <input type="radio" name={name} checked={raw !== "" && raw === option.raw} disabled={disabled} onChange={() => onPick(option.raw)} />
          {option.label}
        </label>
      ))}
    </div>
  );
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
  const t = useT(proposalsText);
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
  const hint = fieldHint(spec, value, hasNotes, isHandField(record, name));
  const source = value?.source ?? null;
  const describedBy =
    [spec.description && descriptionOpen ? descriptionId : null, hint ? hintId : null, errors.length > 0 ? errorId : null].filter(Boolean).join(" ") ||
    undefined;
  const inputDisabled = disabled || (existing && !take);
  const offered = record.options?.[name];

  let input: ReactNode;
  if (unchanged || spec.kind === "fixed") {
    input = (
      <output id={id} className="proposal-fixed" aria-describedby={describedBy}>
        {display(spec, value?.value, fixed)}
      </output>
    );
  } else if (isPick(spec.kind) && directory && pickable(spec.kind, directory).length === 0) {
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
          <option value="">{t.choose}</option>
          {raw && !choices.some(([key]) => key === raw) ? <option value={raw}>{raw}</option> : null}
          {choices.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      );
    } else if (spec.kind === "offered") {
      const raw = state?.raw ?? "";
      const choices = offered ?? [];
      input = (
        <select {...common} onChange={(e) => onChange({ raw: e.target.value })}>
          <option value="">{t.choose}</option>
          {raw && !choices.some((option) => scalar(option.value) === raw) ? <option value={raw}>{raw}</option> : null}
          {choices.map((option) => (
            <option key={scalar(option.value)} value={scalar(option.value)}>
              {option.label}
            </option>
          ))}
        </select>
      );
    } else if (isPick(spec.kind)) {
      const choices: [string, string][] = directory ? pickable(spec.kind, directory) : [];
      const raw = state?.raw ?? "";
      input = (
        <select {...common} onChange={(e) => onChange({ raw: e.target.value })}>
          <option value="">{t.choose}</option>
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
      const numeric =
        spec.kind === "money" || spec.kind === "rate" || spec.kind === "area" || spec.kind === "units" || spec.kind === "year" || spec.kind === "count";
      input = (
        <input
          type="text"
          inputMode={numeric ? (spec.kind === "year" || spec.kind === "count" ? "numeric" : "decimal") : undefined}
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
            title={t.quoted(source.quote)}
            onClick={() => setQuoteOpen((open) => !open)}
          >
            {t.sourcePage(source.page)}
            <span className="visually-hidden">{t.showSource}</span>
          </button>
        ) : null}
      </div>
      {spec.description ? (
        <FieldDescription id={descriptionId} open={descriptionOpen}>
          {spec.description}
        </FieldDescription>
      ) : null}
      <div className={existing && !unchanged ? "proposal-row-body with-current" : "proposal-row-body"}>
        <div>
          {input}
          {!unchanged && spec.kind !== "offered" && offered?.length ? (
            <FieldOptions
              label={spec.label}
              options={offered.map((option) => ({ raw: toRaw(spec, option.value), label: option.label }))}
              raw={state?.raw ?? ""}
              disabled={inputDisabled}
              onPick={(raw) => onChange({ raw })}
            />
          ) : null}
        </div>
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
          <blockquote>{t.quoted(source.quote)}</blockquote>
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

function fieldHint(spec: FieldSpec, value: ExtractedValue | null, hasNotes: boolean, hand: boolean): string | null {
  const m = translate(proposalsText);
  if (!value) return spec.kind === "bool" ? m.notProposedBool : m.notProposed;
  // A hand field (D132): never read, or read but not as a value it can hold; or already stored.
  if (hand && value.value === null && !value.source) return m.notRead;
  if (hand && value.value !== null && !value.source && (value.derived_from ?? []).length === 0) return m.storedBefore;
  if (value.value === null) {
    if (spec.kind === "unit") return m.noUnitFits;
    if (spec.kind === "tenancy") return m.noTenancyFits;
    if (spec.kind === "loan") return m.noLoanFits;
    if (spec.kind === "offered") return m.chooseDepot;
    return hasNotes ? m.notFoundSeeNote : m.notFound;
  }
  const derived = value.derived_from ?? [];
  if (derived.length > 0) return m.computedFrom(derived.map((name) => lookup(DERIVED_INPUT, name)).join(", "));
  if (!value.source) return m.noSource;
  return null;
}

function loanLabel(loan: Directory["loans"][number]): string {
  return `${loan.lender}${loan.contract_number ? ` ${loan.contract_number}` : ""}`;
}

/** The units ("<Immobilie> · <Einheit>"), tenancies (the link picker's label) or loans
 * ("<Immobilie> · <Bank> <Vertragsnummer>") to pick from. */
function pickable(kind: PickKind, directory: Directory): [string, string][] {
  if (kind === "loan") {
    const byId = new Map(directory.properties.map((property) => [property.id, property]));
    return directory.loans.map((loan): [string, string] => [loan.id, `${byId.get(loan.property_id)?.label ?? "?"} · ${loanLabel(loan)}`]);
  }
  if (kind === "tenancy") {
    return targets(directory)
      .filter((target) => target.type === "tenancy")
      .map((target): [string, string] => [target.id, target.label]);
  }
  const propertyById = new Map(directory.properties.map((property) => [property.id, property]));
  return directory.units.map((unit): [string, string] => [unit.id, `${propertyById.get(unit.property_id)?.label ?? "?"} · ${unit.label}`]);
}

/** Nothing to pick yet: where to create it first (nothing here is saved before then). */
function NothingToPick({ kind, directory }: { kind: PickKind; directory: Directory }) {
  const t = useT(proposalsText);
  if (kind === "loan") {
    const single = directory.properties.length === 1 ? directory.properties[0] : undefined;
    return (
      <>
        {t.noLoan}
        <Link href={single ? `/loans/new?property=${single.id}` : "/"}>{t.noLoanLink}</Link>
        {t.pickAfterIt}
      </>
    );
  }
  if (kind === "tenancy") {
    return (
      <>
        {t.noTenancy}
        <Link href="/tenancies/new">{t.noTenancyLink}</Link>
        {t.pickAfterIt}
      </>
    );
  }
  const only = directory.properties.length === 1 ? directory.properties[0] : undefined;
  return only ? (
    <>
      {t.noUnit}
      <Link href={`/properties/${only.id}/units/new`}>{t.noUnitLink}</Link>
      {t.pickAfterHer}
    </>
  ) : (
    <>
      {t.noUnitOpen}
      <Link href="/">{t.overview}</Link>
      {t.noUnitClose}
    </>
  );
}

function SourceLink({ source, originalUrl }: { source: SourceRef; originalUrl: string | null }) {
  const t = useT(proposalsText);
  if (!originalUrl) return <span className="muted">{t.noBrowser}</span>;
  return (
    <a href={`${originalUrl}#page=${source.page}`} target="_blank" rel="noopener">
      {t.openOriginal(source.page)}
    </a>
  );
}

function ConfidenceDot({ value }: { value: ExtractedValue }) {
  const t = useT(proposalsText);
  const checks = (value.checks ?? []).map((check) => CHECK[check]).join(", ");
  const label = t.confidence(CONFIDENCE[value.confidence], checks);
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
  const t = useT(proposalsText);
  const takeId = useId();
  const keepId = useId();
  return (
    <div className="proposal-current">
      <p className="muted">
        {t.stored}<strong>{current}</strong>
      </p>
      <div className="row" role="radiogroup" aria-label={t.takeOrKeep(label)}>
        <label className="inline" htmlFor={takeId}>
          <input id={takeId} type="radio" name={name} checked={take} disabled={disabled} onChange={() => onChange(true)} />
          {t.take}
        </label>
        <label className="inline" htmlFor={keepId}>
          <input id={keepId} type="radio" name={name} checked={!take} disabled={disabled} onChange={() => onChange(false)} />
          {t.keep}
        </label>
      </div>
    </div>
  );
}
