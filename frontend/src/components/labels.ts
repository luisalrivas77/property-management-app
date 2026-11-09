// German labels for the API's enums. Typed as Record<Enum, string>, so a value added to the
// API (and to the generated types) fails the typecheck until it has a label here.
import type {
  AccountSource,
  AcquisitionCostKind,
  BalanceSource,
  DepositForm,
  DocumentSubtype,
  DocumentType,
  DuplicateStatus,
  EventType,
  ExtractedValue,
  ExtractionKind,
  ImportStatus,
  LinkTarget,
  PriceSplitSource,
  PropertyKind,
  ProposalRecord,
  ProposalStatus,
  RentChangeReason,
  RentModel,
  RetentionClass,
  RetentionHold,
  StatementFormat,
  TaxSourceKind,
  TenancyRole,
  WarningCode,
} from "@/api/schemas";
import type { components } from "@/api/types";

import { formatDate, formatMoney } from "./format";

// Step 4's enums have no alias in schemas.ts yet.
type AllocationCategory = components["schemas"]["AllocationCategory"];
type AllocationMethod = components["schemas"]["AllocationMethod"];
type InboxItemKind = components["schemas"]["InboxItemKind"];

type Confidence = ExtractedValue["confidence"];
type Check = NonNullable<ExtractedValue["checks"]>[number];
type RecordMode = ProposalRecord["mode"];

export const PROPERTY_KIND: Record<PropertyKind, string> = {
  etw: "Eigentumswohnung (ETW)",
  efh: "Einfamilienhaus (EFH)",
  mfh: "Mehrfamilienhaus (MFH)",
};

export const ACQUISITION_COST_KIND: Record<AcquisitionCostKind, string> = {
  real_estate_transfer_tax: "Grunderwerbsteuer",
  notary: "Notar",
  land_registry: "Grundbuchamt",
  broker: "Makler",
  other: "Sonstige",
};

export const PRICE_SPLIT_SOURCE: Record<PriceSplitSource, string> = {
  purchase_contract: "Kaufvertrag",
  bmf_tool: "BMF-Arbeitshilfe",
  appraisal: "Gutachten",
};

export const RENT_MODEL: Record<RentModel, string> = {
  fixed: "Festmiete",
  staffel: "Staffelmiete",
  index: "Indexmiete",
};

export const DEPOSIT_FORM: Record<DepositForm, string> = {
  none: "Keine Kaution",
  cash_account: "Barkaution / Kautionskonto",
  bank_guarantee: "Bankbürgschaft",
  surety: "Bürgschaft",
};

export const TENANCY_ROLE: Record<TenancyRole, string> = {
  primary: "Hauptmieter",
  co_tenant: "Mitmieter",
};

export const RENT_CHANGE_REASON: Record<RentChangeReason, string> = {
  initial: "Anfangsmiete",
  staffel_step: "Staffelstufe",
  index_adjustment: "Indexanpassung",
  rent_increase: "Mieterhöhung",
  modernisation: "Modernisierung",
  agreement: "Vereinbarung",
  correction: "Korrektur",
};

export const NOTICE_GIVEN_BY: Record<"tenant" | "landlord", string> = {
  tenant: "Mieter",
  landlord: "Vermieter",
};

export const DOCUMENT_TYPE: Record<DocumentType, string> = {
  statement: "Kontoauszug",
  invoice: "Rechnung",
  letter: "Brief",
  contract: "Vertrag",
  bescheid: "Bescheid",
  zinsbescheinigung: "Zinsbescheinigung",
  wirtschaftsplan: "Wirtschaftsplan",
  hausgeldabrechnung: "Hausgeldabrechnung",
  other: "Sonstiges",
};

export const DOCUMENT_SUBTYPE: Record<DocumentSubtype, string> = {
  purchase_contract: "Kaufvertrag",
  price_split_evidence: "Nachweis Kaufpreisaufteilung",
  appraisal: "Gutachten",
  loan_contract: "Darlehensvertrag",
  lease: "Mietvertrag",
  handover_protocol: "Übergabeprotokoll",
  deposit_record: "Kautionsnachweis",
  cost_basis_evidence: "Nachweis Anschaffungskosten",
  grundsteuerbescheid: "Grundsteuerbescheid",
};

export const LINK_TARGET: Record<LinkTarget, string> = {
  owner: "Eigentümer",
  property: "Immobilie",
  unit: "Einheit",
  tenant: "Mieter",
  tenancy: "Mietverhältnis",
  loan: "Darlehen",
};

export const RETENTION_CLASS: Record<RetentionClass, string> = {
  ownership_records: "Eigentumsunterlagen (Besitzdauer + 10 Jahre)",
  tax_records: "Steuerunterlagen (10 Jahre nach dem Steuerjahr)",
  tenancy_records: "Mietunterlagen (Mietende + 4 Jahre)",
  tenant_contact: "Kontaktdaten Mieter (Mietende + 4 Jahre)",
};

export const RETENTION_HOLD: Record<RetentionHold, string> = {
  while_owned: "solange die Immobilie im Eigentum ist",
  tenancy_active: "solange das Mietverhältnis läuft",
  untyped: "Dokument ist noch nicht typisiert",
  unlinked: "Dokument ist noch nicht verknüpft",
};

/** History and audit labels per event type (§9.4). */
export const EVENT_TYPE: Record<EventType, string> = {
  "owner.created": "Eigentümer angelegt",
  "owner.updated": "Eigentümer geändert",
  "property.created": "Immobilie angelegt",
  "property.updated": "Immobilie geändert",
  "property.purchase_notarized": "Kaufvertrag beurkundet",
  "property.possession_transferred": "Übergang Besitz, Nutzen und Lasten",
  "property.ownership_set": "Eigentumsanteile festgelegt",
  "property.acquisition_cost_recorded": "Anschaffungsnebenkosten erfasst",
  "property.acquisition_cost_updated": "Anschaffungsnebenkosten geändert",
  "property.acquisition_cost_removed": "Anschaffungsnebenkosten entfernt",
  "property.price_split_recorded": "Kaufpreisaufteilung erfasst",
  "property.price_split_selected": "Kaufpreisaufteilung als AfA-Basis gewählt",
  "property.tax_input_recorded": "Steuerliche Angabe erfasst",
  "property.anlage_v_exported": "Anlage V exportiert",
  "unit.created": "Einheit angelegt",
  "unit.updated": "Einheit geändert",
  "tenant.created": "Mieter angelegt",
  "tenant.updated": "Mieterdaten geändert",
  "tenant.erased": "Mieterdaten gelöscht",
  "tenancy.created": "Mietvertrag unterschrieben",
  "tenancy.updated": "Mietverhältnis geändert",
  "tenancy.parties_set": "Mieter geändert",
  "tenancy.started": "Einzug",
  "tenancy.rent_scheduled": "Miete",
  "tenancy.deposit_received": "Kaution erhalten",
  "tenancy.notice_given": "Kündigung",
  "tenancy.ended": "Auszug",
  "loan.created": "Darlehensvertrag unterschrieben",
  "loan.updated": "Darlehen geändert",
  "loan.disbursed": "Darlehen ausgezahlt",
  "document.uploaded": "Dokument hochgeladen",
  "document.typed": "Dokument typisiert",
  "document.linked": "Dokument verknüpft",
  "document.unlinked": "Verknüpfung entfernt",
  "document.classified": "Dokument klassifiziert",
  "document.extracted": "Felder ausgelesen",
  "document.read_failed": "Dokument konnte nicht gelesen werden",
  "workspace.settings_changed": "Einstellungen geändert",
  "proposal.created": "Vorschlag erstellt",
  "proposal.superseded": "Vorschlag ersetzt",
  "proposal.rejected": "Vorschlag abgelehnt",
  "proposal.applied": "Vorschlag übernommen",
  "user.signed_in": "Angemeldet",
  "user.signed_out": "Abgemeldet",
  "user.sessions_revoked": "Sitzungen beendet",
  "user.passkey_enrolled": "Passkey hinzugefügt",
  "user.passkey_renamed": "Passkey umbenannt",
  "user.passkey_removed": "Passkey entfernt",
  "user.invited": "Mitglied eingeladen",
  "user.enrolled": "Mitglied beigetreten",
  "account.created": "Konto angelegt",
  "account.updated": "Konto geändert",
  "statement.imported": "Kontoauszug importiert",
  "statement.reconciled": "Kontoauszug abgestimmt",
  "statement.unreconciled": "Kontoauszug nicht abgestimmt",
  "statement.duplicate_resolved": "Mögliche Doppelbuchung entschieden",
  "transaction.allocated": "Umsatz zugeordnet",
  "transaction.reallocated": "Zuordnung korrigiert",
  "rule.created": "Regel angelegt",
  "rule.disabled": "Regel deaktiviert",
};

// --- pre-fill proposals (Vorschläge, phase-2.md §6.4) ---

export const PROPOSAL_STATUS: Record<ProposalStatus, string> = {
  queued: "wartet auf das Auslesen",
  running: "wird ausgelesen",
  ready: "bereit zur Prüfung",
  applied: "übernommen",
  rejected: "verworfen",
  superseded: "ersetzt",
  failed: "konnte nicht gelesen werden",
  blocked_tenant_data: "enthält Mieterdaten, nicht ausgelesen",
};

export const EXTRACTION_KIND: Record<ExtractionKind, string> = {
  manual: "von Hand",
  purchase_contract: "Kaufvertrag",
  loan_contract: "Darlehensvertrag",
  lease: "Mietvertrag",
  handover_protocol: "Übergabeprotokoll",
  deposit_record: "Kautionsnachweis",
  invoice: "Rechnung",
  bescheid: "Bescheid",
  grundsteuerbescheid: "Grundsteuerbescheid",
  wirtschaftsplan: "Wirtschaftsplan",
  hausgeldabrechnung: "Hausgeldabrechnung",
  zinsbescheinigung: "Zinsbescheinigung",
  statement: "Kontoauszug",
};

/** The confidence dot's text (computed by code from the checks, D15). */
export const CONFIDENCE: Record<Confidence, string> = {
  high: "hoch",
  medium: "mittel",
  low: "niedrig",
};

/** What code verified about a value (ExtractedValue.checks). */
export const CHECK: Record<Check, string> = {
  cited: "Fundstelle im Dokument",
  value_in_quote: "Wert steht im Zitat",
  validates: "Format gültig",
  derived: "berechnet",
};

/** The pill next to a record's title: what applying it does. */
export const RECORD_MODE: Record<RecordMode, string> = {
  create: "neu",
  patch: "ändert Bestehendes",
  correction: "Korrektur",
  unchanged: "unverändert",
};

// --- bank accounts and statement imports (phase 2 step 3) ---

export const ACCOUNT_SOURCE: Record<AccountSource, string> = {
  upload: "Upload",
  fints: "FinTS",
  aggregator: "Kontoaggregator",
};

export const STATEMENT_FORMAT: Record<StatementFormat, string> = {
  camt: "camt (XML)",
  csv_camt: "CSV-CAMT",
  pdf: "PDF-Kontoauszug",
};

/** The pill of an import. `imported` is an import whose closing balance is still missing
 * (the API's "pending balance": a CSV export, which states none). */
export const IMPORT_STATUS: Record<ImportStatus, string> = {
  imported: "Saldo fehlt",
  reconciled: "abgestimmt",
  unreconciled: "nicht abgestimmt",
  failed: "fehlgeschlagen",
};

export const BALANCE_SOURCE: Record<BalanceSource, string> = {
  file: "aus der Datei",
  user: "eingegeben (Online-Banking)",
  none: "noch keiner",
};

export const DUPLICATE_STATUS: Record<DuplicateStatus, string> = {
  open: "offen",
  kept_both: "beide behalten",
  merged: "zusammengeführt",
};

// --- allocations and the review queue (phase 2 step 4, §8.2, §8.3) ---

/** The fixed taxonomy (§8.2). The tax treatment and the Anlage V line come from the rule set,
 * never from this label, so renaming one changes no figure. */
export const ALLOCATION_CATEGORY: Record<AllocationCategory, string> = {
  rent_cold: "Kaltmiete",
  operating_costs_prepayment: "Nebenkostenvorauszahlung",
  operating_costs_settlement: "Nebenkostenabrechnung (Nachzahlung oder Erstattung)",
  deposit_in: "Kaution erhalten",
  deposit_out: "Kaution zurückgezahlt",
  owner_transfer_in: "Einlage von einem Eigentümer",
  owner_transfer_out: "Entnahme an einen Eigentümer",
  loan_interest: "Darlehenszinsen",
  loan_principal: "Tilgung",
  loan_fees: "Geldbeschaffungskosten",
  loan_disbursement: "Darlehensauszahlung",
  hausgeld_umlagefaehig: "Hausgeld, umlagefähig",
  hausgeld_verwaltung: "Hausgeld, nicht umlagefähig (Verwaltung)",
  hausgeld_ruecklage: "Hausgeld, Erhaltungsrücklage",
  property_management: "Mietverwaltung (Sondereigentumsverwaltung)",
  grundsteuer: "Grundsteuer",
  insurance: "Versicherung",
  repairs: "Erhaltungsaufwand (Reparatur)",
  renovation: "Renovierung oder Modernisierung",
  purchase_price: "Kaufpreiszahlung",
  acquisition_cost: "Anschaffungsnebenkosten",
  professional_fees: "Beratungs- und Rechtskosten",
  bank_fees: "Kontoführung und Bankgebühren",
  other_income: "Sonstige Einnahme",
  other_expense: "Sonstige Ausgabe",
  uncategorized: "Noch offen (nicht zugeordnet)",
};

/** Who proposed an allocation set. */
export const ALLOCATION_METHOD: Record<AllocationMethod, string> = {
  rule: "gelernte Regel",
  matcher_rent: "Mietabgleich",
  matcher_loan: "Tilgungsplan des Darlehens",
  matcher_owner: "Überweisung eines Eigentümers",
  matcher_hausgeld: "Wirtschaftsplan",
  document_match: "passender Beleg",
  llm: "KI",
  user: "von Hand",
};

/** The pill of a review-queue item. */
export const INBOX_ITEM_KIND: Record<InboxItemKind, string> = {
  proposal: "Vorschlag",
  read_failed: "Nicht gelesen",
  blocked_tenant_data: "Mieterdaten",
  import_unreconciled: "Nicht abgestimmt",
  import_pending_balance: "Saldo fehlt",
  possible_duplicate: "Mögliche Doppelbuchung",
  allocation_proposed: "Vorschlag prüfen",
  unmatched: "Nicht zugeordnet",
};

// --- The Anlage V draft (phase-3.md §11) ----------------------------------------------

type Params = Record<string, string>;

/** An amount param ("4760.00") as "4.760,00 €"; "–" when the rule sent none. */
const money = (params: Params, key = "amount") => formatMoney(params[key]);

/** The German sentence of a draft warning, its params filled in (amounts as decimal strings,
 * dates ISO, phase-3.md §5). The rules send codes and params only; the text lives here. */
export const WARNING_TEXT: Record<WarningCode, (params: Params) => string> = {
  year_end_straddle: (p) =>
    `Eine Zahlung${p.amount ? ` über ${money(p)}` : ""} ist im einen Jahr gebucht und im anderen wertgestellt; sie zählt im Jahr der Buchung.`,
  "afa.no_split": () => "Keine Kaufpreisaufteilung gewählt: ohne Gebäudeanteil bleibt die AfA leer.",
  "afa.cost_row_unpaid": (p) =>
    `${p.kind ? (ACQUISITION_COST_KIND[p.kind as AcquisitionCostKind] ?? p.kind) : "Nebenkosten"} ${money(p)}: keine Zahlung vom Mietkonto gefunden (vielleicht privat bezahlt). Sie zählt trotzdem zur AfA-Basis.`,
  "afa.acquisition_cost_without_row": (p) =>
    `Zahlung von Anschaffungsnebenkosten über ${money(p)} ohne passenden Eintrag unter Nebenkosten: sie zählt erst zur AfA-Basis, wenn sie dort erfasst ist.`,
  "afa.purchase_price_mismatch": (p) =>
    `Vom Konto bezahlter Kaufpreis ${money(p, "paid")} weicht vom Kaufpreis ${money(p, "price")} ab.`,
  "limit.exceeded": (p) =>
    `Die 15 %-Grenze ist${p.exceeded_in_year ? ` ${p.exceeded_in_year}` : ""} überschritten: die Renovierungskosten des Dreijahresfensters werden Herstellungskosten und laufen über die AfA.`,
  "limit.prior_year_amendment": (p) =>
    `Die 15 %-Grenze wurde ${p.exceeded_in_year ?? "später"} überschritten: ${money(p)} dieses Jahres werden nachträglich Herstellungskosten; der Bescheid für dieses Jahr wird geändert.`,
  "limit.estimated_net": () =>
    "Für mindestens eine Rechnung fehlt der Nettobetrag; die 15 %-Grenze rechnet dort mit geschätzter Umsatzsteuer.",
  "renovation.undecided": (p) =>
    `Renovierung ${money(p)} einordnen: Erhaltungsaufwand oder Herstellungskosten? Bis dahin nicht abgezogen.`,
  "interest.certificate_mismatch": (p) =>
    `Zinsbescheinigung ${money(p, "certified")}, gebucht ${money(p, "booked")}: Unterschied ${money(p, "difference")}. Meist eine Rate, die die Zehn-Tage-Regel ins andere Jahr verschiebt.`,
  "hausgeld.abrechnung_pending": () =>
    "Die Hausgeldabrechnung fehlt noch: was die WEG aus der Rücklage ausgegeben hat, ist vorläufig nicht enthalten.",
  "income.other_review": (p) =>
    `Sonstige Einnahme ${money(p)}${p.counterparty_iban ? ` von ${p.counterparty_iban}` : ""} prüfen.`,
  "income.pre_handover": (p) =>
    `${p.count ?? "Einige"} ${p.count === "1" ? "Einnahme" : "Einnahmen"} vor dem Übergang (${money(p)}): noch nicht festgelegt, wie sie zählen.`,
  "income.living_area_missing": () =>
    "Die Wohnfläche der Immobilie fehlt; Zeile 13 nimmt die Summe der Flächen der Einheiten.",
  "umlagen.mixed_terms": () =>
    "Mietverhältnisse mit Nebenkosten inklusive und mit Vorauszahlung gemischt: das Kreuz in Zeile 24 bleibt leer.",
  "reduced_rent.no_market_rent": () =>
    "Ortsübliche Miete eintragen, um die verbilligte Vermietung (66 %-Grenze) zu prüfen.",
  "reduced_rent.prognosis_needed": () =>
    "Die Miete liegt zwischen 50 % und 66 % der ortsüblichen: das Finanzamt kann eine Überschussprognose verlangen.",
  "reduced_rent.partial": (p) =>
    `Die Miete liegt unter 50 % der ortsüblichen: die Werbungskosten sind nur anteilig abziehbar${p.cut_percent ? ` (Kürzung um ${p.cut_percent} %)` : ""}.`,
  "draft.open_lines_excluded": (p) =>
    `${p.count ?? "Einige"} ${p.count === "1" ? "Zeile" : "Zeilen"} in Zu prüfen (${money(p, "sum")}) ${p.count === "1" ? "ist" : "sind"} im Entwurf noch nicht enthalten.`,
  "draft.form_provisional": () =>
    "Das Formular für dieses Jahr ist noch nicht veröffentlicht; Zeilen und Kennzahlen sind vorläufig.",
  "draft.allocation_without_property": (p) =>
    `Eine Zahlung des Kontos${p.amount ? ` über ${money(p)}` : ""} ist keiner Immobilie zugeordnet.`,
  "archive.finanzamt_copy_needed": () =>
    "Ein Dokument öffnet nicht in jedem Programm (Adobe-Formular oder Anmerkungen): für das Finanzamt eine Kopie drucken.",
};

/** What a line's source is, in the sources drawer. */
export const TAX_SOURCE_KIND: Record<TaxSourceKind, string> = {
  allocation: "Zahlung",
  transaction: "Kontoumsatz",
  document: "Dokument",
  property: "Kaufpreis der Immobilie",
  price_split: "Kaufpreisaufteilung",
  acquisition_cost: "Anschaffungsnebenkosten",
  loan: "Darlehen",
  tax_input: "Steuerliche Angabe",
  rule: "Regelwerk des Steuerjahres",
};

/** A line note of the draft (the §5.1 notes); an unknown note is shown as sent. */
const TAX_NOTE: Record<string, string> = {
  due_date_missing: "Fälligkeit unbekannt: zählt im Jahr der Zahlung",
  year_end_straddle: "Wertstellung im anderen Jahr",
};

export function taxNote(note: string): string {
  return TAX_NOTE[note] ?? note;
}

/** "29.12.2026 → 2027" for a payment the ten-day rule moved. */
export function movedLabel(bookingDate: string, taxYear: number): string {
  return `${formatDate(bookingDate)} → ${taxYear}`;
}

/** The options of a <select> over an enum label map, in declaration order. */
export function options<K extends string>(labels: Record<K, string>): [K, string][] {
  return Object.entries(labels) as [K, string][];
}
