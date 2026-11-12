// German labels for the API's enums. Typed as Record<Enum, string>, so a value added to the
// API (and to the generated types) fails the typecheck until it has a label here.
import type {
  AccountKind,
  AccountPurpose,
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
  StateCode,
  StatementFormat,
  SyncStatus,
  SyncTrigger,
  TaxSourceKind,
  TenancyRole,
  WarningCode,
} from "@/api/schemas";
import type { components } from "@/api/types";

import { formatDate, formatMoney, formatMonth } from "./format";

// Step 4's enums have no alias in schemas.ts yet.
type AllocationCategory = components["schemas"]["AllocationCategory"];
type AllocationMethod = components["schemas"]["AllocationMethod"];
type InboxItemKind = components["schemas"]["InboxItemKind"];
type ReturnInputKind = components["schemas"]["ReturnInputKind"];
type GrossSource = NonNullable<components["schemas"]["OwnerZve"]["gross_source"]>;
type GrundsteuerRhythm = NonNullable<components["schemas"]["GrundsteuerSchedule"]["rhythm"]>;
type GrundsteuerMode = NonNullable<components["schemas"]["GrundsteuerSchedule"]["payment_mode"]>;
type HouseholdServiceClass = components["schemas"]["HouseholdServicesFact"]["service_class"];
type HouseholdServiceSource = components["schemas"]["HouseholdServicesFact"]["source"];
type QuestionSection = components["schemas"]["Question"]["section"];
type QuestionnaireAnswerValue = components["schemas"]["QuestionnaireAnswerValue"];
type CommuteKind = components["schemas"]["CommuteFact"]["kind"];
type ItemClass = components["schemas"]["EquipmentFact"]["item_class"];
type OtherExpenseClass = components["schemas"]["OtherExpenseFact"]["expense_class"];
type WorkRoomPrefer = NonNullable<components["schemas"]["WorkRoomFact"]["prefer"]>;
type PrivateCostClass = components["schemas"]["PrivateCostValue"]["cost_class"];
type HouseholdGroup = components["schemas"]["HouseholdGroup"];

type Confidence = ExtractedValue["confidence"];
type Check = NonNullable<ExtractedValue["checks"]>[number];
type RecordMode = ProposalRecord["mode"];

export const PROPERTY_KIND: Record<PropertyKind, string> = {
  etw: "Eigentumswohnung (ETW)",
  efh: "Einfamilienhaus (EFH)",
  mfh: "Mehrfamilienhaus (MFH)",
};

/** Bundesland of an owner, for the church tax rate (phase 4 D90). */
export const STATE_CODE: Record<StateCode, string> = {
  BW: "Baden-Württemberg",
  BY: "Bayern",
  BE: "Berlin",
  BB: "Brandenburg",
  HB: "Bremen",
  HH: "Hamburg",
  HE: "Hessen",
  MV: "Mecklenburg-Vorpommern",
  NI: "Niedersachsen",
  NW: "Nordrhein-Westfalen",
  RP: "Rheinland-Pfalz",
  SL: "Saarland",
  SN: "Sachsen",
  ST: "Sachsen-Anhalt",
  SH: "Schleswig-Holstein",
  TH: "Thüringen",
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
  lohnsteuerbescheinigung: "Lohnsteuerbescheinigung",
  gehaltsabrechnung: "Gehaltsabrechnung",
  other: "Sonstiges",
};

// In the picker's order: by the groups of the document catalogue (phase-2.md §6.2.1, D131).
export const DOCUMENT_SUBTYPE: Record<DocumentSubtype, string> = {
  // Immobilie und Kauf
  purchase_contract: "Kaufvertrag",
  price_split_evidence: "Nachweis Kaufpreisaufteilung",
  appraisal: "Gutachten",
  cost_basis_evidence: "Nachweis Anschaffungskosten",
  grundsteuerbescheid: "Grundsteuerbescheid",
  land_register_extract: "Grundbuchauszug",
  declaration_of_division: "Teilungserklärung",
  land_charge_deed: "Grundschuldbestellungsurkunde",
  energy_certificate: "Energieausweis",
  building_records: "Grundriss, Wohnflächen, Baupläne",
  insurance_policy: "Versicherungsschein",
  management_contract: "Verwaltervertrag",
  service_contract: "Versorgungs- oder Wartungsvertrag",
  // WEG
  weg_minutes: "Protokoll Eigentümerversammlung",
  weg_invitation: "Einladung Eigentümerversammlung",
  special_levy: "Sonderumlage",
  // Darlehen
  loan_contract: "Darlehensvertrag",
  repayment_schedule: "Tilgungsplan",
  loan_terms_change: "Prolongation, Konditionsanpassung",
  loan_statement: "Darlehenskontoauszug",
  financing_invoice: "Rechnung zur Finanzierung (Grundschuld, Darlehen)",
  // Mietverhältnis
  lease: "Mietvertrag",
  lease_amendment: "Nachtrag zum Mietvertrag",
  handover_protocol: "Übergabeprotokoll",
  deposit_record: "Kautionsnachweis",
  rent_increase: "Mieterhöhung",
  termination_notice: "Kündigung, Mietaufhebung",
  tenant_self_disclosure: "Mieterselbstauskunft, Einkommensnachweis",
  landlord_confirmation: "Wohnungsgeberbestätigung",
  operating_costs_statement: "Betriebskostenabrechnung an Mieter",
  tenant_correspondence: "Schreiben von oder an Mieter",
  // Laufende Kosten
  utility_bill: "Abrechnung Strom, Gas, Wasser",
  quote: "Kostenvoranschlag, Angebot",
  chimney_sweep_notice: "Feuerstättenbescheid",
  // Set by code at the upload from a bill's to-do (phase 3 D143), never by the classifier.
  payment_proof: "Zahlungsnachweis (Überweisung, Kontoauszug)",
  // Einkommensteuer
  einkommensteuerbescheid: "Einkommensteuerbescheid",
  prepayment_notice: "Vorauszahlungsbescheid",
  tax_return_copy: "Steuererklärung, Übertragungsprotokoll",
  donation_receipt: "Zuwendungsbestätigung",
  childcare_invoice: "Kinderbetreuung",
  household_services_invoice: "Handwerker, haushaltsnahe Dienste (§35a)",
  insurance_contribution_certificate: "Beitragsbescheinigung Versicherung",
  pension_contribution_certificate: "Riester- oder Rürup-Bescheinigung",
  benefit_notice: "Bescheid Lohnersatzleistung",
  child_benefit_notice: "Kindergeldbescheid",
  employment_contract: "Arbeitsvertrag",
  employer_certificate: "Arbeitgeberbescheinigung",
  work_expense_invoice: "Arbeitsmittel, Fortbildung",
  // Haushaltsfinanzen
  depot_statement: "Depotauszug",
  depot_report: "Depotbericht",
  trade_confirmation: "Wertpapierabrechnung",
  capital_income_tax_certificate: "Jahressteuerbescheinigung Kapitalerträge",
  // Phase 6
  exchange_export: "Börse / Exchange-Export (CSV)",
  distribution_note: "Ertragsabrechnung (Ausschüttung)",
};

export const LINK_TARGET: Record<LinkTarget, string> = {
  owner: "Eigentümer",
  property: "Immobilie",
  unit: "Einheit",
  tenant: "Mieter",
  tenancy: "Mietverhältnis",
  loan: "Darlehen",
  // A payment proof's bill (phase 3 D143)
  document: "Rechnung",
};

export const RETENTION_CLASS: Record<RetentionClass, string> = {
  ownership_records: "Eigentumsunterlagen (Besitzdauer + 10 Jahre)",
  tax_records: "Steuerunterlagen (10 Jahre nach dem Steuerjahr)",
  tenancy_records: "Mietunterlagen (Mietende + 4 Jahre)",
  tenant_contact: "Kontaktdaten Mieter (Mietende + 4 Jahre)",
  holding_records: "Anlageunterlagen (solange gehalten + 10 Jahre)",
};

export const RETENTION_HOLD: Record<RetentionHold, string> = {
  while_owned: "solange die Immobilie im Eigentum ist",
  tenancy_active: "solange das Mietverhältnis läuft",
  untyped: "Dokument ist noch nicht typisiert",
  unlinked: "Dokument ist noch nicht verknüpft",
  while_held: "solange die Anlage gehalten wird",
};

/** History and audit labels per event type (§9.4). */
export const EVENT_TYPE: Record<EventType, string> = {
  "owner.created": "Eigentümer angelegt",
  "owner.updated": "Eigentümer geändert",
  "owner.income_input_recorded": "Einkommensangabe erfasst",
  "owner.return_input_recorded": "Angabe zur Steuererklärung erfasst",
  "owner.anlage_n_exported": "Anlage N exportiert",
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
  "property.scenario_recorded": "Prognose-Szenario erfasst",
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
  "document.received_by_email": "Per weitergeleiteter Mail erhalten",
  "workspace.settings_changed": "Einstellungen geändert",
  "workspace.income_input_recorded": "Einkommensangabe des Haushalts erfasst",
  "workspace.return_input_recorded": "Angabe des Haushalts zur Steuererklärung erfasst",
  "workspace.questionnaire_answered": "Jahresfrage beantwortet",
  "workspace.reminder_acknowledged": "Erinnerung erledigt, verschoben oder stumm geschaltet",
  "workspace.notification_sent": "Erinnerung per E-Mail verschickt",
  "workspace.inbox_poll_failed": "Posteingang per E-Mail gestört",
  "workspace.inbox_poll_recovered": "Posteingang per E-Mail läuft wieder",
  "workspace.ai_consent_recorded": "Zustimmung zur KI-Zuordnung privater Konten erfasst",
  "workspace.prices_fetched": "Kurse abgerufen",
  "workspace.valuation_snapshot_written": "Monatsendstand des Vermögens gespeichert",
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
  "account.connection_created": "Bankabruf eingerichtet",
  "account.connection_updated": "Bankabruf geändert",
  "account.sync_started": "Bankabruf gestartet",
  "account.sync_approval_needed": "Bankabruf wartet auf Freigabe",
  "account.sync_completed": "Bankabruf abgeschlossen",
  "account.sync_failed": "Bankabruf fehlgeschlagen",
  "account.statement_fetched": "Kontoauszug (PDF) von der Bank abgerufen",
  "account.bank_parameters_read": "Bankparameter (FinTS) gelesen",
  "account.kind_set": "Kontoart geändert",
  "account.ownership_set": "Kontoinhaber und Anteile festgelegt",
  "account.movements_imported": "Börsen-Export importiert",
  "account.movements_reconciled": "Börsen-Export abgestimmt",
  "account.movements_unreconciled": "Börsen-Export nicht abgestimmt",
  "account.holdings_confirmed": "Bestände bestätigt",
  "account.holdings_reconciled": "Bestände abgestimmt",
  "account.holdings_mismatch": "Bestände weichen ab",
  "asset.created": "Anlage angelegt",
  "asset.updated": "Anlage geändert",
  "asset.price_recorded": "Kurs erfasst",
  "asset.price_source_failed": "Kursquelle gestört",
  "movement.proposed": "Bewegung vorgeschlagen",
  "movement.confirmed": "Bewegung bestätigt",
  "movement.superseded": "Bewegung korrigiert",
  "transfer.linked": "Übertrag verknüpft",
  "transfer.unlinked": "Übertrag getrennt",
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
  lohnsteuerbescheinigung: "Lohnsteuerbescheinigung",
  gehaltsabrechnung: "Gehaltsabrechnung",
  einkommensteuerbescheid: "Einkommensteuerbescheid",
  general: "Titel und Datum",
  management_contract: "Verwaltervertrag",
  payment_proof: "Zahlungsnachweis",
  trade_confirmation: "Wertpapierabrechnung",
  distribution_note: "Ertragsabrechnung",
  depot_report: "Depotbericht",
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

/** account.kind (phase 6 D156). Depot and exchange accounts come with steps 4 and 5, wallets
 * with phase 7; the API refuses them until then. */
export const ACCOUNT_KIND: Record<AccountKind, string> = {
  bank_current: "Girokonto",
  bank_savings: "Sparkonto oder Tagesgeld",
  depot: "Depot",
  exchange: "Börse (Krypto)",
  wallet: "Wallet",
};

/** account.purpose (D156): the rental account feeds the Anlage V, a private one never does. */
export const ACCOUNT_PURPOSE: Record<AccountPurpose, string> = {
  rental: "Mietkonto (für die Immobilie)",
  private: "Privatkonto",
};

export const STATEMENT_FORMAT: Record<StatementFormat, string> = {
  camt: "camt (XML)",
  csv_camt: "CSV-CAMT",
  pdf: "PDF-Kontoauszug",
  dkb_csv: "DKB-CSV",
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
  bank: "vom Bankabruf (Kontostand der Bank)",
};

/** A FinTS sync's state (phase-5.md §4.2). */
export const SYNC_STATUS: Record<SyncStatus, string> = {
  queued: "wartet",
  running: "läuft",
  needs_approval: "wartet auf Freigabe",
  importing: "importiert",
  done: "importiert",
  nothing_new: "nichts Neues",
  failed: "fehlgeschlagen",
};

export const SYNC_TRIGGER: Record<SyncTrigger, string> = {
  manual: "von Hand",
  scheduled: "täglich",
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
  electricity: "Strom",
  repairs: "Erhaltungsaufwand (Reparatur)",
  renovation: "Renovierung oder Modernisierung",
  purchase_price: "Kaufpreiszahlung",
  acquisition_cost: "Anschaffungsnebenkosten",
  professional_fees: "Beratungs- und Rechtskosten",
  bank_fees: "Kontoführung und Bankgebühren",
  other_income: "Sonstige Einnahme",
  other_expense: "Sonstige Ausgabe",
  uncategorized: "Noch offen (nicht zugeordnet)",
  // Phase 6 (D159, D162, D165)
  hh_salary: "Gehalt",
  hh_child_benefit: "Kindergeld",
  hh_tax_refund: "Steuererstattung",
  hh_capital_income: "Zinsen und Dividenden",
  hh_other_income: "Sonstige Einnahme (privat)",
  hh_housing: "Wohnen",
  hh_groceries: "Lebensmittel und Drogerie",
  hh_family_child: "Familie und Kind",
  hh_mobility: "Mobilität",
  hh_insurance: "Versicherungen",
  hh_subscriptions: "Abos und Verträge",
  hh_leisure_travel: "Freizeit und Reisen",
  hh_health: "Gesundheit",
  hh_other: "Sonstiges",
  hh_donation: "Spende",
  hh_household_services: "Haushaltsnahe Dienstleistung oder Handwerker",
  hh_childcare: "Kinderbetreuung",
  hh_property_cost: "Kosten der Immobilie, privat bezahlt",
  hh_tax_payment: "Einkommensteuer (Vorauszahlung oder Nachzahlung)",
  internal_transfer_in: "Übertrag von eigenem Konto",
  internal_transfer_out: "Übertrag auf eigenes Konto",
  hh_cash: "Bargeldabhebung",
  hh_credit_card: "Kreditkartenabrechnung",
  security_purchase: "Wertpapierkauf",
  security_sale: "Wertpapierverkauf",
  distribution: "Ausschüttung oder Dividende",
  capital_income_tax: "Kapitalertragsteuer und Vorabpauschale",
  depot_fee: "Depot- und Verwaltergebühr",
};

/** The household, transfer and depot codes (phase 6 D159): for private accounts only. The
 * server refuses them on a rental line (allocation.category_not_for_account), where they would
 * be on no Anlage V line; mirrors rules/household/taxonomy.PRIVATE_ONLY. */
export const PRIVATE_ONLY_CATEGORIES: ReadonlySet<AllocationCategory> = new Set<AllocationCategory>([
  "hh_salary",
  "hh_child_benefit",
  "hh_tax_refund",
  "hh_capital_income",
  "hh_other_income",
  "hh_housing",
  "hh_groceries",
  "hh_family_child",
  "hh_mobility",
  "hh_insurance",
  "hh_subscriptions",
  "hh_leisure_travel",
  "hh_health",
  "hh_other",
  "hh_donation",
  "hh_household_services",
  "hh_childcare",
  "hh_property_cost",
  "hh_tax_payment",
  "internal_transfer_in",
  "internal_transfer_out",
  "hh_cash",
  "hh_credit_card",
  "security_purchase",
  "security_sale",
  "distribution",
  "capital_income_tax",
  "depot_fee",
]);

/** The household codes in the groups of rules/household/taxonomy.py (phase 6 D159), for the
 * category picker of a private line; the API's allowed set per purpose decides. */
const HOUSEHOLD_GROUPS: { label: string; categories: AllocationCategory[] }[] = [
  { label: "Einnahmen", categories: ["hh_salary", "hh_child_benefit", "hh_tax_refund", "hh_capital_income", "hh_other_income"] },
  {
    label: "Ausgaben",
    categories: [
      "hh_housing",
      "hh_groceries",
      "hh_family_child",
      "hh_mobility",
      "hh_insurance",
      "hh_subscriptions",
      "hh_leisure_travel",
      "hh_health",
      "hh_other",
    ],
  },
  {
    label: "Steuerlich relevant",
    categories: ["hh_donation", "hh_household_services", "hh_childcare", "hh_property_cost", "hh_tax_payment"],
  },
  { label: "Bewegungen", categories: ["internal_transfer_in", "internal_transfer_out", "hh_cash", "hh_credit_card"] },
  { label: "Konto", categories: ["bank_fees", "uncategorized"] },
];

const DEPOT_GROUP: { label: string; categories: AllocationCategory[] } = {
  label: "Depot",
  categories: ["security_purchase", "security_sale", "distribution", "capital_income_tax", "depot_fee"],
};

/** The categories a line's account takes, in the picker's groups (D159, mirrors
 * rules/household/taxonomy.allowed_categories): a rental line every phase 2-4 code; a private
 * one the household and transfer codes, its bank fees and „noch offen“, and on a depot the
 * depot's own codes too. */
export function categoryGroups(
  purpose: AccountPurpose,
  kind: AccountKind,
): { label: string | null; categories: [AllocationCategory, string][] }[] {
  const labelled = (categories: AllocationCategory[]) =>
    categories.map((category): [AllocationCategory, string] => [category, ALLOCATION_CATEGORY[category]]);
  if (purpose === "rental") {
    return [{ label: null, categories: options(ALLOCATION_CATEGORY).filter(([value]) => !PRIVATE_ONLY_CATEGORIES.has(value)) }];
  }
  const groups = kind === "depot" ? [...HOUSEHOLD_GROUPS, DEPOT_GROUP] : HOUSEHOLD_GROUPS;
  return groups.map((group) => ({ label: group.label, categories: labelled(group.categories) }));
}

/** Who proposed an allocation set. */
export const ALLOCATION_METHOD: Record<AllocationMethod, string> = {
  rule: "gelernte Regel",
  matcher_rent: "Mietabgleich",
  matcher_loan: "Tilgungsplan des Darlehens",
  matcher_owner: "Überweisung eines Eigentümers",
  matcher_hausgeld: "Wirtschaftsplan",
  matcher_transfer: "Übertrag zwischen eigenen Konten",
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
  questionnaire_open: "Jahresfragen",
  // phase 5 (§11): grouped under "Automatisch" once steps 1-4 build their cards
  bank_approval_needed: "Freigabe nötig",
  bank_sync_failed: "Bankabruf fehlgeschlagen",
  bank_balance_drift: "Kontostand weicht ab",
  email_received: "Per Mail erhalten",
  email_failed: "Mail nicht übernommen",
  email_inbox_failed: "Posteingang gestört",
  reminder: "Termin",
  // phase 6 (D161, D162)
  transfer_link: "Gegenbuchung wählen",
  transfer_unmatched: "Überweisung ohne Gegenstück",
  property_cost_row: "Privat bezahlte Kosten",
  property_cost_bill: "Rechnung fehlt",
};

/** The Haushalt screen's groups (phase 6 §13, mockup 10; rules/household/taxonomy.py). */
export const HOUSEHOLD_GROUP: Record<HouseholdGroup, string> = {
  income: "Einnahmen",
  housing: "Wohnen",
  groceries: "Lebensmittel und Drogerie",
  family_child: "Familie und Kind",
  mobility: "Mobilität",
  insurance: "Versicherungen",
  subscriptions: "Abos und Verträge",
  leisure_travel: "Freizeit und Reisen",
  health: "Gesundheit",
  other: "Sonstiges",
  tax: "Steuerlich relevant",
  movements: "Bewegungen",
  depot: "Depot",
};

// --- The income tax return's inputs (phase-4b.md §7, §10) --------------------------------

/** What a return input row is (return_input.kind, D97). */
export const RETURN_INPUT_KIND: Record<ReturnInputKind, string> = {
  wage_certificate: "Lohnsteuerbescheinigung",
  payslip: "Gehaltsabrechnung",
  commute: "Arbeitsweg",
  home_office: "Homeoffice-Tage",
  work_room: "Arbeitszimmer",
  work_equipment: "Arbeitsmittel",
  telecom: "Telekommunikation",
  professional_association: "Beiträge zu Berufsverbänden",
  training: "Fortbildung",
  other_expense: "Sonstige Werbungskosten",
  other_insurance: "Weitere Versicherungen",
  replacement_income: "Lohnersatzleistungen",
  vorsorge_override: "Vorsorge manuell",
  child: "Kind",
  childcare_cost: "Kinderbetreuungskosten",
  household_services: "Haushaltsnahe Dienstleistungen (§ 35a)",
  special_expense: "Spende",
};

/** A pre-filled pay document's field (the proposal's field name, backend rules/prefill.py
 * wage_certificate / payslip), as the evidence fold-out names it. The certificate's amounts by
 * their line (Nr. 3 …), the payslip's figures with month or year to date. */
const PAY_FIELD: Record<string, string> = {
  tax_year: "Steuerjahr",
  "value.period_from": "Zeitraum von",
  "value.period_to": "Zeitraum bis",
  "value.period_month": "Abrechnungsmonat",
  "value.employer": "Arbeitgeber",
  "value.tax_class": "Steuerklasse",
  "value.tax_class_factor": "Faktor (Steuerklasse IV)",
  "value.church_tax_marked": "Kirchensteuermerkmal",
  "value.child_allowance_count": "Kinderfreibeträge",
  "value.months_remaining_paid": "Restliche Monate",
  "value.gross": "Nr. 3 Bruttoarbeitslohn",
  "value.income_tax": "Nr. 4 Lohnsteuer",
  "value.soli": "Nr. 5 Solidaritätszuschlag",
  "value.church_tax": "Nr. 6 Kirchensteuer",
  "value.church_tax_spouse": "Nr. 7 Kirchensteuer Ehegatte",
  "value.replacement_benefits": "Nr. 15 Lohnersatzleistungen",
  "value.short_time_allowance": "Nr. 15a Kurzarbeitergeld",
  "value.employer_commute_tax_free": "Nr. 17 Fahrtkostenzuschuss steuerfrei",
  "value.employer_commute_flat_taxed": "Nr. 18 Fahrtkostenzuschuss pauschal versteuert",
  "value.pension_employer": "Nr. 22a Rentenversicherung Arbeitgeber",
  "value.pension_employee": "Nr. 23a Rentenversicherung Arbeitnehmer",
  "value.pension_employee_professional": "Nr. 23b berufsständische Versorgung",
  "value.subsidy_health": "Nr. 24a Zuschuss Krankenversicherung",
  "value.subsidy_private_health": "Nr. 24b Zuschuss private Krankenversicherung",
  "value.subsidy_care": "Nr. 24c Zuschuss Pflegeversicherung",
  "value.health_employee": "Nr. 25 Krankenversicherung",
  "value.care_employee": "Nr. 26 Pflegeversicherung",
  "value.unemployment_employee": "Nr. 27 Arbeitslosenversicherung",
};
const PAYSLIP_FIGURE: Record<string, string> = {
  gross: "Steuer-Brutto",
  income_tax: "Lohnsteuer",
  soli: "Solidaritätszuschlag",
  church_tax: "Kirchensteuer",
  pension_employee: "Rentenversicherung",
  health_employee: "Krankenversicherung",
  care_employee: "Pflegeversicherung",
  unemployment_employee: "Arbeitslosenversicherung",
  tax_free_benefits: "steuerfreie Bezüge",
};

export function payFieldLabel(field: string): string {
  const figure = /^value\.([a-z_]+)\.(month|ytd)$/.exec(field);
  if (figure?.[1] && figure[2]) {
    return `${PAYSLIP_FIGURE[figure[1]] ?? figure[1]} (${figure[2] === "month" ? "Monat" : "Jahr bisher"})`;
  }
  return PAY_FIELD[field] ?? field;
}

/** Where an owner's gross pay comes from (OwnerZve.gross_source, D98); the screen adds the
 * year or the payslip month. */
export const GROSS_SOURCE: Record<GrossSource, string> = {
  certificate: "laut Lohnsteuerbescheinigung",
  payslip: "Hochrechnung aus Gehaltsabrechnung",
  expected: "Schätzung",
};

/** A §35a cost's class (HouseholdServicesFact.service_class, D112). */
export const HOUSEHOLD_SERVICE_CLASS: Record<HouseholdServiceClass, string> = {
  handwerker: "Handwerkerleistung",
  haushaltsnah: "Haushaltsnahe Dienstleistung",
  minijob: "Minijob im Haushalt",
};

/** Where a §35a labour cost was read from (HouseholdServicesFact.source). */
export const HOUSEHOLD_SERVICE_SOURCE: Record<HouseholdServiceSource, string> = {
  own_invoice: "eigene Rechnung",
  nebenkostenabrechnung: "Nebenkostenabrechnung der eigenen Wohnung",
};

/** The section of a Jahresfrage (Question.section, D110). */
export const QUESTION_SECTION: Record<QuestionSection, string> = {
  einkommen: "Einkommen",
  werbungskosten: "Werbungskosten",
  haushalt: "Haushalt",
  kinder: "Kinder",
  immobilie: "Immobilie",
};

/** A Jahresfrage's coded answer (questionnaire_answer.answer, D110). */
export const QUESTIONNAIRE_ANSWER: Record<QuestionnaireAnswerValue, string> = {
  yes: "Ja",
  no: "Nein",
  not_applicable: "Trifft nicht zu",
};

/** A commute route's kind (CommuteFact.kind). */
export const COMMUTE_KIND: Record<CommuteKind, string> = {
  first_workplace: "Erste Tätigkeitsstätte",
  collection_point: "Sammelpunkt oder weiträumiges Tätigkeitsgebiet",
};

/** A work item's class (EquipmentFact.item_class, D108). */
export const ITEM_CLASS: Record<ItemClass, string> = {
  computer: "Computer",
  software: "Software",
  phone: "Telefon",
  furniture: "Möbel",
  tools: "Werkzeug",
  subscription: "Abonnement",
  other: "Sonstiges",
};

/** An other Werbungskosten item's class (OtherExpenseFact.expense_class). */
export const OTHER_EXPENSE_CLASS: Record<OtherExpenseClass, string> = {
  account_fee: "Kontoführungsgebühren",
  application: "Bewerbungskosten",
  ferry_flight: "Fähr- oder Flugkosten zur Arbeit",
  other: "Sonstiges",
};

/** The Arbeitszimmer's choice between the flat amount and the actual costs (D106). */
export const WORK_ROOM_PREFER: Record<WorkRoomPrefer, string> = {
  flat: "Jahrespauschale",
  actual: "Tatsächliche Kosten",
};

/** A privately paid rental cost's class (PrivateCostValue.cost_class, D111; financing D134). */
export const PRIVATE_COST_CLASS: Record<PrivateCostClass, string> = {
  travel: "Fahrten zur Wohnung",
  postage: "Porto",
  advertising: "Anzeigen",
  association: "Vermieterverein",
  legal_insurance: "Rechtsschutzversicherung",
  software: "Software",
  financing: "Geldbeschaffungskosten (z. B. Notar und Grundbuch für die Grundschuld)",
  other: "Sonstiges",
};

// --- The Anlage V draft (phase-3.md §11) ----------------------------------------------

type Params = Record<string, string>;

/** An amount param ("4760.00") as "4.760,00 €"; "–" when the rule sent none. */
const money = (params: Params, key = "amount") => formatMoney(params[key]);

/** A payment_proof.missing bill (phase 3 D143): „die Rechnung Notar“ by the cost row's kind,
 * or „die Rechnung „Dachreparatur““ by the privately paid cost's label. */
export function proofBill(params: Params): string {
  if (params.kind) return `die Rechnung ${ACQUISITION_COST_KIND[params.kind as AcquisitionCostKind] ?? params.kind}`;
  return params.label ? `die Rechnung „${params.label}“` : "die Rechnung";
}

/** The German sentence of a draft warning, its params filled in (amounts as decimal strings,
 * dates ISO, phase-3.md §5). The rules send codes and params only; the text lives here. */
export const WARNING_TEXT: Record<WarningCode, (params: Params) => string> = {
  year_end_straddle: (p) =>
    `Eine Zahlung${p.amount ? ` über ${money(p)}` : ""} ist im einen Jahr gebucht und im anderen wertgestellt; sie zählt im Jahr der Buchung.`,
  "afa.no_split": () => "Keine Kaufpreisaufteilung gewählt: ohne Gebäudeanteil bleibt die AfA leer.",
  "afa.cost_row_unpaid": (p) =>
    `${p.kind ? (ACQUISITION_COST_KIND[p.kind as AcquisitionCostKind] ?? p.kind) : "Nebenkosten"} ${money(p)}: keine Zahlung vom Mietkonto gefunden (vielleicht von einem anderen Konto bezahlt). Sie zählt trotzdem zur AfA-Basis.`,
  "afa.acquisition_cost_without_row": (p) =>
    `Zahlung von Anschaffungsnebenkosten über ${money(p)} ohne passenden Eintrag unter Nebenkosten: sie zählt erst zur AfA-Basis, wenn sie dort erfasst ist.`,
  "afa.purchase_price_mismatch": (p) =>
    `Vom Konto bezahlter Kaufpreis ${money(p, "paid")} weicht vom Kaufpreis ${money(p, "price")} ab.`,
  "afa.useful_life_report": (p) =>
    `AfA nach kürzerer tatsächlicher Nutzungsdauer (${p.years ?? "?"} Jahre ab ${p.from_year ?? "?"}): in Zeile 33 „laut Erläuterung“ angeben; das Finanzamt fragt meist nach dem Gutachten.`,
  "afa.movable_asset_no_evidence": (p) =>
    `Der Wert der Einbauküche (${money(p)}) ist ohne Beleg erfasst. Eine Schätzung ist zulässig; bitte die Grundlage (z. B. einen Ausdruck des Küchenplaners) aufbewahren und als Beleg hinterlegen.`,
  "afa.movable_asset_exceeds_building": (p) =>
    `Die Einbauküche (${money(p, "deducted")} mit Nebenkosten) ist nicht kleiner als der Gebäudeanteil (${money(p, "building")}): die Gebäude-AfA fällt auf 0. Bitte Wert der Küche oder Kaufpreisaufteilung prüfen.`,
  "limit.exceeded": (p) =>
    `Die 15 %-Grenze ist${p.exceeded_in_year ? ` ${p.exceeded_in_year}` : ""} überschritten: die Renovierungskosten des Dreijahresfensters werden Herstellungskosten und laufen über die AfA.`,
  "limit.prior_year_amendment": (p) =>
    `Die 15 %-Grenze wurde ${p.exceeded_in_year ?? "später"} überschritten: ${money(p)} dieses Jahres werden nachträglich Herstellungskosten; der Bescheid für dieses Jahr wird geändert.`,
  "limit.estimated_net": () =>
    "Für mindestens eine Rechnung fehlt der Nettobetrag; die 15 %-Grenze rechnet dort mit geschätzter Umsatzsteuer.",
  "limit.private_cost_undated": (p) =>
    `Reparatur „${p.label ?? ""}“ (${money(p)}), von einem anderen Konto bezahlt, ohne Datum der Arbeit: die 15 %-Grenze zählt sie vorsichtshalber mit, wenn sie in einem der drei Jahre nach dem Kauf bezahlt wurde.`,
  "limit.private_cost_after_window": (p) =>
    `Reparatur „${p.label ?? ""}“ (${money(p)}), von einem anderen Konto bezahlt, datiert am ${formatDate(p.performed_on)} kurz nach dem Dreijahresfenster: wann wurde die Arbeit ausgeführt? Eine Arbeit im Fenster zählt zur 15 %-Grenze.`,
  "anschaffungsnah.performance_date_check": (p) =>
    `Kosten von ${money(p)}, bezahlt am ${formatDate(p.booking_date)} kurz nach dem Dreijahresfenster: wann wurde die Arbeit ausgeführt? Ein Leistungszeitraum im Fenster zählt zur 15 %-Grenze.`,
  "renovation.undecided": (p) =>
    `Renovierung ${money(p)} einordnen: Erhaltungsaufwand oder Herstellungskosten? Bis dahin nicht abgezogen.`,
  "interest.certificate_mismatch": (p) =>
    `Zinsbescheinigung ${money(p, "certified")}, gebucht ${money(p, "booked")}: Unterschied ${money(p, "difference")}. Meist eine Rate, die die Zehn-Tage-Regel ins andere Jahr verschiebt.`,
  "hausgeld.abrechnung_pending": () =>
    "Die Hausgeldabrechnung fehlt noch: was die WEG aus der Rücklage ausgegeben hat, ist vorläufig nicht enthalten.",
  "income.other_review": (p) =>
    p.counted_as_rent === "1"
      ? `Einnahme ${money(p)}${p.counterparty_iban ? ` von ${p.counterparty_iban}` : ""} vor der Übergabe: zählt als Miete, ist aber als Sonstige Einnahme gebucht.`
      : `Sonstige Einnahme ${money(p)}${p.counterparty_iban ? ` von ${p.counterparty_iban}` : ""} prüfen.`,
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
  // Phase 4b (phase-4b.md §9): Anlage N and the whole-return estimate.
  "tax.travel_frequency": () =>
    "Mehr als 50 Fahrten zur Immobilie im Jahr: bei fast täglicher Anwesenheit kann nur die halbe Pauschale gelten. Bitte prüfen.",
  "tax.private_cost_paid_from_account": (p) =>
    `${p.label ? `„${p.label}“ ist` : "Eine Rechnung ist"} als von einem anderen Konto bezahlt eingetragen, wurde aber vom Mietkonto bezahlt: sie zählt nur einmal, mit der Buchung vom Mietkonto. Die Angabe bitte zurückziehen.`,
  // Phase 3 D150: proof_rest when the linked proofs each pay one part of the bill.
  "payment_proof.missing": (p) =>
    p.proof_rest
      ? `Zahlungsnachweis fehlt: ${proofBill(p)} über ${money(p)} zählt mit. Ein Teil ist nachgewiesen; es fehlt noch der Nachweis über ${money(p, "proof_rest")}.`
      : `Zahlungsnachweis fehlt: ${proofBill(p)} über ${money(p)} zählt mit, aber nichts zeigt, dass sie bezahlt wurde${p.proof_amount ? ` (der hochgeladene Nachweis lautet über ${money(p, "proof_amount")}, der Betrag passt nicht)` : ""}.`,
  "anlage_n.certificate_missing": () =>
    "Keine Lohnsteuerbescheinigung für dieses Jahr: die Zahlen beruhen auf Gehaltsabrechnungen oder der Schätzung.",
  "anlage_n.church_mismatch": () =>
    "Die Kirchensteuermerkmale der Bescheinigung passen nicht zur Angabe beim Eigentümer.",
  "anlage_n.tax_class_6": () =>
    "Steuerklasse 6: ein zweites Arbeitsverhältnis. Es steht in der eigenen Spalte für Steuerklasse 6.",
  "anlage_n.periods_overlap": () => "Zwei Bescheinigungen desselben Arbeitgebers überschneiden sich im Zeitraum.",
  "anlage_n.days_implausible": () =>
    "Die Zahl der Arbeitstage passt nicht zu den Tagen pro Woche und dem Zeitraum.",
  "anlage_n.home_office_capped": () => "Die Tagespauschale ist auf den Jahreshöchstbetrag begrenzt.",
  "anlage_n.too_many_routes": () =>
    "Mehr Wege zur Arbeit, als das Formular Zeilen hat: die übrigen bitte von Hand eintragen.",
  "anlage_n.receipt_missing": () => "Für eine Ausgabe ist kein Beleg hinterlegt.",
  "anlage_n.form_provisional": () =>
    "Die Anlage N für dieses Jahr ist noch nicht veröffentlicht; Zeilen und Kennzahlen sind vorläufig.",
  "anlage_n.payslip_gap": (p) => payslipGapText(p),
  "anlage_n.payslip_no_ytd": () =>
    "Die Gehaltsabrechnung hat keine Jahreswerte: die Hochrechnung nutzt nur den Monatswert.",
  "anlage_n.work_room_records": () => "Für das Arbeitszimmer bitte Grundriss und Kosten aufbewahren.",
  "anlage_n.work_room_centre": () =>
    "Ein Arbeitszimmer zählt nur, wenn es der Mittelpunkt der gesamten Tätigkeit ist. Bitte prüfen.",
  "anlage_n.work_room_partial": (p) =>
    `Das Arbeitszimmer zählt für ${p.months ?? "einige"} Monate: die Tagespauschale nur für Tage der übrigen Monate eintragen.`,
  "anlage_n.first_workplace_check": () =>
    "Bitte prüfen, ob dies die erste Tätigkeitsstätte ist (Festlegung durch den Arbeitgeber).",
  "return.progression_provisional": () =>
    "Lohnersatzleistungen sind noch nicht bescheinigt: der Steuersatz ist vorläufig.",
  "return.benefit_possible_double": () =>
    "Eine Lohnersatzleistung steht in der Bescheinigung und als eigene Angabe: bitte nur einmal erfassen.",
  "return.children_mismatch": () =>
    "Die Zahl der Kinderfreibeträge der Bescheinigung passt nicht zu den erfassten Kindern.",
  "return.kindergeld_apply": () => "Für ein Kind ist noch kein Kindergeld erfasst: bitte bei der Familienkasse beantragen.",
  "return.child_over_18": () => "Ein Kind ist volljährig: es zählt nur bei Ausbildung oder Studium.",
  "return.childcare_until_14": () =>
    "Ein Kind wird in diesem Jahr 14: es zählen nur die Kinderbetreuungskosten bis zum 14. Geburtstag.",
  "return.donation_over_cap": () => "Die Spenden übersteigen die Höchstgrenze; der Rest zählt nicht in diesem Jahr.",
  "return.filing_required": () => "Für dieses Jahr besteht eine Pflicht zur Abgabe der Steuererklärung.",
  "return.questionnaire_open": () => "Es sind noch Fragen zum Steuerjahr offen.",
  "return.not_modelled": () => "Ein Teil der Erklärung wird von der App nicht berechnet; die Schätzung ist unvollständig.",
  questionnaire_provisional: () =>
    "Die Fragen für dieses Jahr sind noch nicht festgelegt; es gelten die des Vorjahres.",
  "tax_export.questions_open": (p) =>
    `${p.count ?? "Einige"} ${p.count === "1" ? "Jahresfrage" : "Jahresfragen"} offen: beim Export noch nicht beantwortet, die Angaben können sich noch ändern.`,
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
  scenario: "Prognose-Szenario",
  schedule_entry: "Tilgungsplan",
  income_input: "Einkommensangabe",
  tariff: "Einkommensteuertarif",
  return_input: "Angabe zur Steuererklärung",
  questionnaire_answer: "Antwort auf eine Jahresfrage",
  anlage_n_rule_set: "Regelwerk der Anlage N",
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

// --- Phase 4b (phase-4b.md §10): the Anlage N tab and the Gesamt card ----------------------

type EquipmentClass = components["schemas"]["EquipmentFact"]["item_class"];
type ReplacementBenefit = components["schemas"]["ReplacementFact"]["benefit"];
type AnlageNStatus = components["schemas"]["AnlageNDraft"]["status"];
type ReturnStatus = components["schemas"]["ReturnEstimate"]["status"];

/** A work item's class (D108): it decides the useful life. */
export const EQUIPMENT_CLASS: Record<EquipmentClass, string> = {
  computer: "Computer, Laptop, Tablet",
  software: "Software",
  phone: "Smartphone, Telefon",
  furniture: "Büromöbel",
  tools: "Werkzeug",
  subscription: "Abo (z. B. Software, Fachzeitschrift)",
  other: "Sonstiges",
};

/** A wage-replacement benefit paid by an authority (Progressionsvorbehalt, D102). */
export const REPLACEMENT_BENEFIT: Record<ReplacementBenefit, string> = {
  elterngeld: "Elterngeld",
  mutterschaftsgeld: "Mutterschaftsgeld der Krankenkasse",
  krankengeld: "Krankengeld",
  arbeitslosengeld: "Arbeitslosengeld",
  kurzarbeitergeld: "Kurzarbeitergeld",
  other: "Andere Lohnersatzleistung",
};

/** The Anlage N draft's status: where the pay comes from. */
export const ANLAGE_N_STATUS: Record<AnlageNStatus, string> = {
  certified: "bescheinigt",
  estimate: "Schätzung",
  none: "kein Arbeitslohn",
};

/** The whole-return estimate's status (D101). */
export const RETURN_STATUS: Record<ReturnStatus, string> = {
  estimate: "Schätzung",
  certified: "laut Bescheinigungen",
};

/** What the estimate leaves out (backend services/return_inputs.py NOT_MODELLED, D95). */
const NOT_MODELLED: Record<string, string> = {
  extraordinary_burden: "außergewöhnliche Belastungen",
  riester: "Riester",
  maintenance: "Unterhalt",
  prepayments: "Vorauszahlungen",
  capital_income: "Kapitalerträge (ab Phase 8)",
};

/** anlage_n.payslip_gap with its months: `month` is the payslip whose year-to-date sum does not
 * follow from the one before (`previous`); `missing` lists the months between the two, if any. */
function payslipGapText(p: Params): string {
  if (!p.month) return "Zwischen den Gehaltsabrechnungen fehlt ein Monat.";
  const employer = p.employer ? ` (${p.employer})` : "";
  const missing = (p.missing ?? "").split(",").filter((m) => m !== "");
  if (missing.length > 0) {
    const names = missing.map(formatMonth).join(", ");
    return `Gehaltsabrechnung ${names}${employer} ${missing.length === 1 ? "fehlt" : "fehlen"}: die Jahressumme im ${formatMonth(p.month)} passt nicht zur Summe der Abrechnung davor.`;
  }
  return `Gehaltsabrechnung ${formatMonth(p.month)}${employer} passt nicht zur Vormonatssumme: bitte die Jahreswerte prüfen.`;
}

/** An unknown code is shown as sent. */
export function notModelledLabel(code: string): string {
  return NOT_MODELLED[code] ?? code;
}

/** The Grundsteuerbescheid's payment rhythm (phase-5.md D126). */
export const GRUNDSTEUER_RHYTHM: Record<GrundsteuerRhythm, string> = {
  quarterly: "vierteljährlich",
  half_yearly: "halbjährlich",
  yearly_august: "jährlich zum 15. August",
  yearly_july: "jährlich zum 1. Juli",
};

/** The Grundsteuer reminder's mode (D126): the row's choice, or "automatisch" (inferred from a
 * direct-debit line, never from a transfer). */
export const GRUNDSTEUER_MODE: Record<GrundsteuerMode | "auto", string> = {
  remind: "Erinnern",
  sepa_mandate: "Lastschrift prüfen",
  auto: "automatisch",
};
