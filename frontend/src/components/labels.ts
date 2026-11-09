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
  TenancyRole,
} from "@/api/schemas";
import type { components } from "@/api/types";

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

/** The options of a <select> over an enum label map, in declaration order. */
export function options<K extends string>(labels: Record<K, string>): [K, string][] {
  return Object.entries(labels) as [K, string][];
}
