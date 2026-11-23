// Labels for the API's enums, German with English beside it (phase 10 D284, read through
// localized()). Typed as Record<Enum, string>, so a value added to the API (and to the generated
// types) fails the typecheck until it has a label here, in both languages.
import type {
  AccountKind,
  AccountPurpose,
  AccountSource,
  AcquisitionCostKind,
  AddressKind,
  AssetKind,
  BalanceSource,
  Chain,
  DepositForm,
  DocumentSubtype,
  DocumentType,
  DuplicateStatus,
  EventType,
  ExtractedValue,
  ExtractionKind,
  ImportStatus,
  LinkTarget,
  MovementKind,
  PriceSourceKind,
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

import { DOCUMENT_SUBTYPE_EN, DOCUMENT_TYPE_EN } from "@/i18n/documents";
import { formatter } from "@/i18n/format";
import { getLocale, localized } from "@/i18n/locale";

const english = () => getLocale() === "en";

/** Money, dates and months in the chosen language. */
const formatMoney = (value: string | null | undefined) => formatter().money(value);
const formatDate = (value: string | null | undefined) => formatter().date(value);
const formatMonth = (value: string | null | undefined) => formatter().month(value);

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

const PROPERTY_KIND_DE: Record<PropertyKind, string> = {
  etw: "Eigentumswohnung (ETW)",
  efh: "Einfamilienhaus (EFH)",
  mfh: "Mehrfamilienhaus (MFH)",
};
const PROPERTY_KIND_EN: Record<PropertyKind, string> = {
  etw: "Flat (ETW)",
  efh: "Detached house (EFH)",
  mfh: "Apartment building (MFH)",
};
export const PROPERTY_KIND: Record<PropertyKind, string> = localized(PROPERTY_KIND_DE, PROPERTY_KIND_EN);

/** Bundesland of an owner, for the church tax rate (phase 4 D90). */
const STATE_CODE_DE: Record<StateCode, string> = {
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
const STATE_CODE_EN: Record<StateCode, string> = {
  BW: "Baden-Württemberg",
  BY: "Bavaria",
  BE: "Berlin",
  BB: "Brandenburg",
  HB: "Bremen",
  HH: "Hamburg",
  HE: "Hesse",
  MV: "Mecklenburg-Western Pomerania",
  NI: "Lower Saxony",
  NW: "North Rhine-Westphalia",
  RP: "Rhineland-Palatinate",
  SL: "Saarland",
  SN: "Saxony",
  ST: "Saxony-Anhalt",
  SH: "Schleswig-Holstein",
  TH: "Thuringia",
};
export const STATE_CODE: Record<StateCode, string> = localized(STATE_CODE_DE, STATE_CODE_EN);

const ACQUISITION_COST_KIND_DE: Record<AcquisitionCostKind, string> = {
  real_estate_transfer_tax: "Grunderwerbsteuer",
  notary: "Notar",
  land_registry: "Grundbuchamt",
  broker: "Makler",
  other: "Sonstige",
};
const ACQUISITION_COST_KIND_EN: Record<AcquisitionCostKind, string> = {
  real_estate_transfer_tax: "Grunderwerbsteuer (property transfer tax)",
  notary: "Notary",
  land_registry: "Land registry (Grundbuchamt)",
  broker: "Estate agent",
  other: "Other",
};
export const ACQUISITION_COST_KIND: Record<AcquisitionCostKind, string> = localized(ACQUISITION_COST_KIND_DE, ACQUISITION_COST_KIND_EN);

const PRICE_SPLIT_SOURCE_DE: Record<PriceSplitSource, string> = {
  purchase_contract: "Kaufvertrag",
  bmf_tool: "BMF-Arbeitshilfe",
  appraisal: "Gutachten",
};
const PRICE_SPLIT_SOURCE_EN: Record<PriceSplitSource, string> = {
  purchase_contract: "Purchase contract",
  bmf_tool: "BMF calculation aid (BMF-Arbeitshilfe)",
  appraisal: "Appraisal",
};
export const PRICE_SPLIT_SOURCE: Record<PriceSplitSource, string> = localized(PRICE_SPLIT_SOURCE_DE, PRICE_SPLIT_SOURCE_EN);

const RENT_MODEL_DE: Record<RentModel, string> = {
  fixed: "Festmiete",
  staffel: "Staffelmiete",
  index: "Indexmiete",
};
const RENT_MODEL_EN: Record<RentModel, string> = {
  fixed: "Fixed rent",
  staffel: "Stepped rent (Staffelmiete)",
  index: "Index-linked rent (Indexmiete)",
};
export const RENT_MODEL: Record<RentModel, string> = localized(RENT_MODEL_DE, RENT_MODEL_EN);

const DEPOSIT_FORM_DE: Record<DepositForm, string> = {
  none: "Keine Kaution",
  cash_account: "Barkaution / Kautionskonto",
  bank_guarantee: "Bankbürgschaft",
  surety: "Bürgschaft",
};
const DEPOSIT_FORM_EN: Record<DepositForm, string> = {
  none: "No deposit",
  cash_account: "Cash deposit / deposit account",
  bank_guarantee: "Bank guarantee",
  surety: "Surety",
};
export const DEPOSIT_FORM: Record<DepositForm, string> = localized(DEPOSIT_FORM_DE, DEPOSIT_FORM_EN);

const TENANCY_ROLE_DE: Record<TenancyRole, string> = {
  primary: "Hauptmieter",
  co_tenant: "Mitmieter",
};
const TENANCY_ROLE_EN: Record<TenancyRole, string> = {
  primary: "Main tenant",
  co_tenant: "Co-tenant",
};
export const TENANCY_ROLE: Record<TenancyRole, string> = localized(TENANCY_ROLE_DE, TENANCY_ROLE_EN);

const RENT_CHANGE_REASON_DE: Record<RentChangeReason, string> = {
  initial: "Anfangsmiete",
  staffel_step: "Staffelstufe",
  index_adjustment: "Indexanpassung",
  rent_increase: "Mieterhöhung",
  modernisation: "Modernisierung",
  agreement: "Vereinbarung",
  correction: "Korrektur",
};
const RENT_CHANGE_REASON_EN: Record<RentChangeReason, string> = {
  initial: "Initial rent",
  staffel_step: "Rent step",
  index_adjustment: "Index adjustment",
  rent_increase: "Rent increase",
  modernisation: "Modernisation",
  agreement: "Agreement",
  correction: "Correction",
};
export const RENT_CHANGE_REASON: Record<RentChangeReason, string> = localized(RENT_CHANGE_REASON_DE, RENT_CHANGE_REASON_EN);

const NOTICE_GIVEN_BY_DE: Record<"tenant" | "landlord", string> = {
  tenant: "Mieter",
  landlord: "Vermieter",
};
const NOTICE_GIVEN_BY_EN: Record<"tenant" | "landlord", string> = {
  tenant: "Tenant",
  landlord: "Landlord",
};
export const NOTICE_GIVEN_BY: Record<"tenant" | "landlord", string> = localized(NOTICE_GIVEN_BY_DE, NOTICE_GIVEN_BY_EN);

const DOCUMENT_TYPE_DE: Record<DocumentType, string> = {
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
export const DOCUMENT_TYPE: Record<DocumentType, string> = localized(DOCUMENT_TYPE_DE, DOCUMENT_TYPE_EN);

// In the picker's order: by the groups of the document catalogue (phase-2.md §6.2.1, D131).
const DOCUMENT_SUBTYPE_DE: Record<DocumentSubtype, string> = {
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
  securities_transfer: "Wertpapierübertrag (Ein- oder Ausbuchung)",
  // Phase 7 (D182 (3)): written by code only, never offered in the type picker
  chain_sync: "Wallet-Abruf (Rohdaten der Chain)",
};
export const DOCUMENT_SUBTYPE: Record<DocumentSubtype, string> = localized(DOCUMENT_SUBTYPE_DE, DOCUMENT_SUBTYPE_EN);

const LINK_TARGET_DE: Record<LinkTarget, string> = {
  owner: "Eigentümer",
  property: "Immobilie",
  unit: "Einheit",
  tenant: "Mieter",
  tenancy: "Mietverhältnis",
  loan: "Darlehen",
  // A payment proof's bill (phase 3 D143)
  document: "Rechnung",
};
const LINK_TARGET_EN: Record<LinkTarget, string> = {
  owner: "Owner",
  property: "Property",
  unit: "Unit",
  tenant: "Tenant",
  tenancy: "Tenancy",
  loan: "Loan",
  document: "Invoice",
};
export const LINK_TARGET: Record<LinkTarget, string> = localized(LINK_TARGET_DE, LINK_TARGET_EN);

const RETENTION_CLASS_DE: Record<RetentionClass, string> = {
  ownership_records: "Eigentumsunterlagen (Besitzdauer + 10 Jahre)",
  tax_records: "Steuerunterlagen (10 Jahre nach dem Steuerjahr)",
  tenancy_records: "Mietunterlagen (Mietende + 4 Jahre)",
  tenant_contact: "Kontaktdaten Mieter (Mietende + 4 Jahre)",
  holding_records: "Anlageunterlagen (solange gehalten + 10 Jahre)",
};
const RETENTION_CLASS_EN: Record<RetentionClass, string> = {
  ownership_records: "Ownership records (while owned + 10 years)",
  tax_records: "Tax records (10 years after the tax year)",
  tenancy_records: "Tenancy records (end of tenancy + 4 years)",
  tenant_contact: "Tenant contact data (end of tenancy + 4 years)",
  holding_records: "Investment records (while held + 10 years)",
};
export const RETENTION_CLASS: Record<RetentionClass, string> = localized(RETENTION_CLASS_DE, RETENTION_CLASS_EN);

const RETENTION_HOLD_DE: Record<RetentionHold, string> = {
  while_owned: "solange die Immobilie im Eigentum ist",
  tenancy_active: "solange das Mietverhältnis läuft",
  untyped: "Dokument ist noch nicht typisiert",
  unlinked: "Dokument ist noch nicht verknüpft",
  while_held: "solange die Anlage gehalten wird",
};
const RETENTION_HOLD_EN: Record<RetentionHold, string> = {
  while_owned: "while the property is owned",
  tenancy_active: "while the tenancy runs",
  untyped: "document has no type yet",
  unlinked: "document is not linked yet",
  while_held: "while the investment is held",
};
export const RETENTION_HOLD: Record<RetentionHold, string> = localized(RETENTION_HOLD_DE, RETENTION_HOLD_EN);

/** History and audit labels per event type (§9.4). */
const EVENT_TYPE_DE: Record<EventType, string> = {
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
  "document.deleted": "Dokument gelöscht",
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
  "workspace.app_secret_set": "Zugangsschlüssel eingetragen",
  "workspace.app_secret_deleted": "Zugangsschlüssel gelöscht",
  "workspace.inbox_poll_failed": "Posteingang per E-Mail gestört",
  "workspace.inbox_poll_recovered": "Posteingang per E-Mail läuft wieder",
  "workspace.ai_consent_recorded": "Zustimmung zur KI-Zuordnung privater Konten erfasst",
  "workspace.prices_fetched": "Kurse abgerufen",
  "workspace.valuation_snapshot_written": "Monatsendstand des Vermögens gespeichert",
  "health.snapshot_stored": "Kennzahlen eines abgeschlossenen Monats festgehalten",
  "household_scenario.created": "Haushaltsszenario gespeichert",
  "household_scenario.withdrawn": "Haushaltsszenario zurückgezogen",
  "plan.explanation_stored": "Erklärung des Haushaltsplans gespeichert",
  "workspace.advice_mode_changed": "Modus des Haushaltsplans geändert",
  "workspace.bank_pin_set": "PIN eines Online-Banking-Zugangs gespeichert (verschlüsselt)",
  "workspace.bank_pin_deleted": "PIN eines Online-Banking-Zugangs gelöscht",
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
  "account.statements_refused": "Bank erlaubt den Abruf der Kontoauszüge (PDF) nicht",
  "account.statements_answered": "Bank beantwortet den Abruf der Kontoauszüge (PDF) wieder",
  "account.bank_parameters_read": "Bankparameter (FinTS) gelesen",
  "account.kind_set": "Kontoart geändert",
  "account.ownership_set": "Kontoinhaber und Anteile festgelegt",
  "account.movements_imported": "Börsen-Export importiert",
  "account.movements_reconciled": "Börsen-Export abgestimmt",
  "account.withdrawal_history_checked": "Auszahlungsverlauf abgeglichen",
  "account.balance_corrected": "Differenz zum Börsenbestand als Korrektur gebucht",
  "account.movement_times_corrected": "Zeiten eines Binance-Exports nach UTC verschoben (Zeitzone aus dem Dateinamen)",
  "account.movements_unreconciled": "Börsen-Export nicht abgestimmt",
  "account.holdings_confirmed": "Bestände bestätigt",
  "account.holdings_reconciled": "Bestände abgestimmt",
  "account.holdings_mismatch": "Bestände weichen ab",
  "account.holding_corrected": "Bestand korrigiert",
  "account.holdings_exported": "Bestandsnachweis erstellt",
  "account.deposit_address_learned": "Einzahlungsadresse der Börse gemerkt",
  "account.deposit_address_forgotten": "Einzahlungsadresse der Börse vergessen",
  "account.withdrawal_pair_learned": "Abhebungen an eine Wallet-Adresse gemerkt",
  "account.withdrawal_pair_forgotten": "Abhebungen an eine Wallet-Adresse vergessen",
  "wallet.address_added": "Wallet-Adresse hinzugefügt",
  "wallet.address_updated": "Wallet-Adresse geändert",
  "wallet.address_revealed": "Wallet-Adresse vollständig kopiert",
  "asset.created": "Anlage angelegt",
  "asset.updated": "Anlage geändert",
  "asset.price_recorded": "Kurs erfasst",
  "asset.price_source_failed": "Kursquelle gestört",
  "movement.proposed": "Bewegung vorgeschlagen",
  "movement.confirmed": "Bewegung bestätigt",
  "movement.superseded": "Bewegung korrigiert",
  "wallet.sync_completed": "Wallet-Adresse abgerufen",
  "wallet.sync_failed": "Wallet-Abruf fehlgeschlagen",
  "wallet.payee_answered": "Bitcoin-Ausgang als Empfänger bestätigt",
  "wallet.deposit_looked_up": "Börsen-Abhebung auf der Adresse gesucht, nichts gutgeschrieben",
  "movement_group.proposed": "Chain-Transaktion wartet auf Prüfung",
  "movement_group.confirmed": "Chain-Transaktion bestätigt",
  "movement_group.trade_recorded": "Börsen-Trade zusammengefasst",
  "transfer.linked": "Übertrag verknüpft",
  "transfer.unlinked": "Übertrag getrennt",
  "statement.imported": "Kontoauszug importiert",
  "statement.reconciled": "Kontoauszug abgestimmt",
  "statement.unreconciled": "Kontoauszug nicht abgestimmt",
  "statement.duplicate_resolved": "Mögliche Doppelbuchung entschieden",
  "transaction.allocated": "Umsatz zugeordnet",
  "transaction.reallocated": "Zuordnung korrigiert",
  "transaction.resort_proposed": "Feinere Kategorie vorgeschlagen",
  "rule.created": "Regel angelegt",
  "rule.disabled": "Regel deaktiviert",
};
const EVENT_TYPE_EN: Record<EventType, string> = {
  "owner.created": "Owner created",
  "owner.updated": "Owner changed",
  "owner.income_input_recorded": "Income details recorded",
  "owner.return_input_recorded": "Tax return detail recorded",
  "owner.anlage_n_exported": "Anlage N exported",
  "property.created": "Property created",
  "property.updated": "Property changed",
  "property.purchase_notarized": "Purchase contract notarised",
  "property.possession_transferred": "Handover of possession, benefits and burdens",
  "property.ownership_set": "Ownership shares set",
  "property.acquisition_cost_recorded": "Acquisition costs recorded",
  "property.acquisition_cost_updated": "Acquisition costs changed",
  "property.acquisition_cost_removed": "Acquisition costs removed",
  "property.price_split_recorded": "Purchase price split recorded",
  "property.price_split_selected": "Purchase price split chosen as AfA basis",
  "property.tax_input_recorded": "Tax detail recorded",
  "property.anlage_v_exported": "Anlage V exported",
  "property.scenario_recorded": "Projection scenario recorded",
  "unit.created": "Unit created",
  "unit.updated": "Unit changed",
  "tenant.created": "Tenant created",
  "tenant.updated": "Tenant details changed",
  "tenant.erased": "Tenant data deleted",
  "tenancy.created": "Lease signed",
  "tenancy.updated": "Tenancy changed",
  "tenancy.parties_set": "Tenants changed",
  "tenancy.started": "Move-in",
  "tenancy.rent_scheduled": "Rent",
  "tenancy.deposit_received": "Deposit received",
  "tenancy.notice_given": "Notice given",
  "tenancy.ended": "Move-out",
  "loan.created": "Loan contract signed",
  "loan.updated": "Loan changed",
  "loan.disbursed": "Loan paid out",
  "document.uploaded": "Document uploaded",
  "document.typed": "Document typed",
  "document.linked": "Document linked",
  "document.unlinked": "Link removed",
  "document.deleted": "Document deleted",
  "document.classified": "Document classified",
  "document.extracted": "Fields read",
  "document.read_failed": "Document could not be read",
  "document.received_by_email": "Received by forwarded email",
  "workspace.settings_changed": "Settings changed",
  "workspace.income_input_recorded": "Household income details recorded",
  "workspace.return_input_recorded": "Household tax return detail recorded",
  "workspace.questionnaire_answered": "Yearly question answered",
  "workspace.reminder_acknowledged": "Reminder done, postponed or muted",
  "workspace.notification_sent": "Reminder sent by email",
  "workspace.app_secret_set": "Access key saved",
  "workspace.app_secret_deleted": "Access key deleted",
  "workspace.inbox_poll_failed": "Email inbox disrupted",
  "workspace.inbox_poll_recovered": "Email inbox working again",
  "workspace.ai_consent_recorded": "Consent to AI categorisation of private accounts recorded",
  "workspace.prices_fetched": "Prices fetched",
  "workspace.valuation_snapshot_written": "Month-end net worth stored",
  "health.snapshot_stored": "Key figures of a closed month stored",
  "household_scenario.created": "Household scenario saved",
  "household_scenario.withdrawn": "Household scenario withdrawn",
  "plan.explanation_stored": "Household plan explanation stored",
  "workspace.advice_mode_changed": "Household plan mode changed",
  "workspace.bank_pin_set": "Online banking PIN saved (encrypted)",
  "workspace.bank_pin_deleted": "Online banking PIN deleted",
  "proposal.created": "Suggestion created",
  "proposal.superseded": "Suggestion replaced",
  "proposal.rejected": "Suggestion rejected",
  "proposal.applied": "Suggestion applied",
  "user.signed_in": "Signed in",
  "user.signed_out": "Signed out",
  "user.sessions_revoked": "Sessions ended",
  "user.passkey_enrolled": "Passkey added",
  "user.passkey_renamed": "Passkey renamed",
  "user.passkey_removed": "Passkey removed",
  "user.invited": "Member invited",
  "user.enrolled": "Member joined",
  "account.created": "Account created",
  "account.updated": "Account changed",
  "account.connection_created": "Bank sync set up",
  "account.connection_updated": "Bank sync changed",
  "account.sync_started": "Bank sync started",
  "account.sync_approval_needed": "Bank sync waiting for approval",
  "account.sync_completed": "Bank sync finished",
  "account.sync_failed": "Bank sync failed",
  "account.statement_fetched": "Statement (PDF) fetched from the bank",
  "account.statements_refused": "Bank does not allow fetching statements (PDF)",
  "account.statements_answered": "Bank answers statement (PDF) requests again",
  "account.bank_parameters_read": "Bank parameters (FinTS) read",
  "account.kind_set": "Account type changed",
  "account.ownership_set": "Account holders and shares set",
  "account.movements_imported": "Exchange export imported",
  "account.movements_reconciled": "Exchange export reconciled",
  "account.withdrawal_history_checked": "Withdrawal history checked",
  "account.balance_corrected": "Difference to the exchange balance booked as a correction",
  "account.movement_times_corrected": "Times of a Binance export moved to UTC (time zone from the file name)",
  "account.movements_unreconciled": "Exchange export not reconciled",
  "account.holdings_confirmed": "Holdings confirmed",
  "account.holdings_reconciled": "Holdings reconciled",
  "account.holdings_mismatch": "Holdings differ",
  "account.holding_corrected": "Holding corrected",
  "account.holdings_exported": "Holdings statement created",
  "account.deposit_address_learned": "Exchange deposit address remembered",
  "account.deposit_address_forgotten": "Exchange deposit address forgotten",
  "account.withdrawal_pair_learned": "Withdrawals to a wallet address remembered",
  "account.withdrawal_pair_forgotten": "Withdrawals to a wallet address forgotten",
  "wallet.address_added": "Wallet address added",
  "wallet.address_updated": "Wallet address changed",
  "wallet.address_revealed": "Full wallet address copied",
  "asset.created": "Investment created",
  "asset.updated": "Investment changed",
  "asset.price_recorded": "Price recorded",
  "asset.price_source_failed": "Price source disrupted",
  "movement.proposed": "Movement suggested",
  "movement.confirmed": "Movement confirmed",
  "movement.superseded": "Movement corrected",
  "wallet.sync_completed": "Wallet address synced",
  "wallet.sync_failed": "Wallet sync failed",
  "wallet.payee_answered": "Bitcoin output confirmed as payee",
  "wallet.deposit_looked_up": "Exchange withdrawal looked up on the address, nothing credited",
  "movement_group.proposed": "Chain transaction waiting for review",
  "movement_group.confirmed": "Chain transaction confirmed",
  "movement_group.trade_recorded": "Exchange trade grouped",
  "transfer.linked": "Transfer linked",
  "transfer.unlinked": "Transfer unlinked",
  "statement.imported": "Statement imported",
  "statement.reconciled": "Statement reconciled",
  "statement.unreconciled": "Statement not reconciled",
  "statement.duplicate_resolved": "Possible duplicate decided",
  "transaction.allocated": "Transaction categorised",
  "transaction.reallocated": "Categorisation corrected",
  "transaction.resort_proposed": "Finer category suggested",
  "rule.created": "Rule created",
  "rule.disabled": "Rule disabled",
};
export const EVENT_TYPE: Record<EventType, string> = localized(EVENT_TYPE_DE, EVENT_TYPE_EN);

// --- pre-fill proposals (Vorschläge, phase-2.md §6.4) ---

const PROPOSAL_STATUS_DE: Record<ProposalStatus, string> = {
  queued: "wartet auf das Auslesen",
  running: "wird ausgelesen",
  ready: "bereit zur Prüfung",
  applied: "übernommen",
  rejected: "verworfen",
  superseded: "ersetzt",
  failed: "konnte nicht gelesen werden",
  blocked_tenant_data: "enthält Mieterdaten, nicht ausgelesen",
};
const PROPOSAL_STATUS_EN: Record<ProposalStatus, string> = {
  queued: "waiting to be read",
  running: "being read",
  ready: "ready to review",
  applied: "applied",
  rejected: "discarded",
  superseded: "replaced",
  failed: "could not be read",
  blocked_tenant_data: "contains tenant data, not read",
};
export const PROPOSAL_STATUS: Record<ProposalStatus, string> = localized(PROPOSAL_STATUS_DE, PROPOSAL_STATUS_EN);

const EXTRACTION_KIND_DE: Record<ExtractionKind, string> = {
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
  securities_transfer: "Wertpapierübertrag",
};
const EXTRACTION_KIND_EN: Record<ExtractionKind, string> = {
  manual: "by hand",
  purchase_contract: "Purchase contract",
  loan_contract: "Loan contract",
  lease: "Lease",
  handover_protocol: "Handover record",
  deposit_record: "Deposit record",
  invoice: "Invoice",
  bescheid: "Notice (Bescheid)",
  grundsteuerbescheid: "Grundsteuer notice",
  wirtschaftsplan: "Wirtschaftsplan",
  hausgeldabrechnung: "Hausgeldabrechnung",
  zinsbescheinigung: "Interest certificate",
  statement: "Statement",
  lohnsteuerbescheinigung: "Lohnsteuerbescheinigung",
  gehaltsabrechnung: "Gehaltsabrechnung",
  einkommensteuerbescheid: "Einkommensteuerbescheid",
  general: "Title and date",
  management_contract: "Property management contract",
  payment_proof: "Proof of payment",
  trade_confirmation: "Trade confirmation",
  distribution_note: "Distribution note",
  depot_report: "Depot report",
  securities_transfer: "Securities transfer",
};
export const EXTRACTION_KIND: Record<ExtractionKind, string> = localized(EXTRACTION_KIND_DE, EXTRACTION_KIND_EN);

/** The confidence dot's text (computed by code from the checks, D15). */
const CONFIDENCE_DE: Record<Confidence, string> = {
  high: "hoch",
  medium: "mittel",
  low: "niedrig",
};
const CONFIDENCE_EN: Record<Confidence, string> = {
  high: "high",
  medium: "medium",
  low: "low",
};
export const CONFIDENCE: Record<Confidence, string> = localized(CONFIDENCE_DE, CONFIDENCE_EN);

/** What code verified about a value (ExtractedValue.checks). */
const CHECK_DE: Record<Check, string> = {
  cited: "Fundstelle im Dokument",
  value_in_quote: "Wert steht im Zitat",
  validates: "Format gültig",
  derived: "berechnet",
};
const CHECK_EN: Record<Check, string> = {
  cited: "Found in the document",
  value_in_quote: "Value is in the quote",
  validates: "Valid format",
  derived: "computed",
};
export const CHECK: Record<Check, string> = localized(CHECK_DE, CHECK_EN);

/** The pill next to a record's title: what applying it does. */
const RECORD_MODE_DE: Record<RecordMode, string> = {
  create: "neu",
  patch: "ändert Bestehendes",
  correction: "Korrektur",
  unchanged: "unverändert",
};
const RECORD_MODE_EN: Record<RecordMode, string> = {
  create: "new",
  patch: "changes existing",
  correction: "Correction",
  unchanged: "unchanged",
};
export const RECORD_MODE: Record<RecordMode, string> = localized(RECORD_MODE_DE, RECORD_MODE_EN);

// --- bank accounts and statement imports (phase 2 step 3) ---

const ACCOUNT_SOURCE_DE: Record<AccountSource, string> = {
  upload: "Upload",
  fints: "FinTS",
  aggregator: "Kontoaggregator",
};
const ACCOUNT_SOURCE_EN: Record<AccountSource, string> = {
  upload: "Upload",
  fints: "FinTS",
  aggregator: "Account aggregator",
};
export const ACCOUNT_SOURCE: Record<AccountSource, string> = localized(ACCOUNT_SOURCE_DE, ACCOUNT_SOURCE_EN);

/** account.kind (phase 6 D156; the depot and the exchange since steps 4 and 5, the wallet since
 * phase 7 step 1). */
const ACCOUNT_KIND_DE: Record<AccountKind, string> = {
  bank_current: "Girokonto",
  bank_savings: "Sparkonto oder Tagesgeld",
  depot: "Depot",
  exchange: "Börse (Krypto)",
  wallet: "Wallet",
  credit_card: "Kreditkarte",
};
const ACCOUNT_KIND_EN: Record<AccountKind, string> = {
  bank_current: "Current account",
  bank_savings: "Savings account",
  depot: "Depot",
  exchange: "Exchange (crypto)",
  wallet: "Wallet",
  credit_card: "Credit card",
};
export const ACCOUNT_KIND: Record<AccountKind, string> = localized(ACCOUNT_KIND_DE, ACCOUNT_KIND_EN);

/** wallet_address.chain (phase 7, the registry's five chains). */
const CHAIN_DE: Record<Chain, string> = {
  bitcoin: "Bitcoin",
  ethereum: "Ethereum",
  arbitrum: "Arbitrum",
  bsc: "BNB Smart Chain",
  solana: "Solana",
};
const CHAIN_EN: Record<Chain, string> = {
  bitcoin: "Bitcoin",
  ethereum: "Ethereum",
  arbitrum: "Arbitrum",
  bsc: "BNB Smart Chain",
  solana: "Solana",
};
export const CHAIN: Record<Chain, string> = localized(CHAIN_DE, CHAIN_EN);

/** wallet_address.kind (phase 7 D185): an address added by hand is a receive address; a change
 * address and a stake account are registered from a card's answer (step 2 on). */
const ADDRESS_KIND_DE: Record<AddressKind, string> = {
  receive: "Empfangsadresse",
  change: "Wechselgeld-Adresse",
  stake: "Stake-Konto",
};
const ADDRESS_KIND_EN: Record<AddressKind, string> = {
  receive: "Receiving address",
  change: "Change address",
  stake: "Stake account",
};
export const ADDRESS_KIND: Record<AddressKind, string> = localized(ADDRESS_KIND_DE, ADDRESS_KIND_EN);

/** price.source (D167, D174): where a stored price came from, shown beside every price. */
const PRICE_SOURCE_DE: Record<PriceSourceKind, string> = {
  trade: "Wertpapierabrechnung",
  statement: "Depotbericht",
  issuer_nav: "NAV der Fondsgesellschaft",
  // D264: an exchange's quote, never called a NAV
  exchange_close: "Börsenkurs Lang & Schwarz",
  coingecko_daily: "CoinGecko",
  kraken_daily: "Kraken",
  ecb_fx: "EZB-Referenzkurs",
  user: "von Hand eingetragen",
  // D291: a price read on demand with „Jetzt aktualisieren“, for today only
  spot: "Aktueller Kurs",
};
const PRICE_SOURCE_EN: Record<PriceSourceKind, string> = {
  trade: "Trade confirmation",
  statement: "Depot report",
  issuer_nav: "Fund company NAV",
  exchange_close: "Lang & Schwarz exchange price",
  coingecko_daily: "CoinGecko",
  kraken_daily: "Kraken",
  ecb_fx: "ECB reference rate",
  user: "entered by hand",
  spot: "Current price",
};
export const PRICE_SOURCE: Record<PriceSourceKind, string> = localized(PRICE_SOURCE_DE, PRICE_SOURCE_EN);

/** asset.kind (D166): the Übersicht's groups. */
const ASSET_KIND_DE: Record<AssetKind, string> = {
  etf: "ETF",
  stock: "Aktien",
  crypto: "Krypto",
  cash: "Guthaben",
};
const ASSET_KIND_EN: Record<AssetKind, string> = {
  etf: "ETF",
  stock: "Shares",
  crypto: "Crypto",
  cash: "Cash",
};
export const ASSET_KIND: Record<AssetKind, string> = localized(ASSET_KIND_DE, ASSET_KIND_EN);

/** account.purpose (D156): the rental account feeds the Anlage V, a private one never does. */
const ACCOUNT_PURPOSE_DE: Record<AccountPurpose, string> = {
  rental: "Mietkonto (für die Immobilie)",
  private: "Privatkonto",
};
const ACCOUNT_PURPOSE_EN: Record<AccountPurpose, string> = {
  rental: "Rental account (for the property)",
  private: "Private account",
};
export const ACCOUNT_PURPOSE: Record<AccountPurpose, string> = localized(ACCOUNT_PURPOSE_DE, ACCOUNT_PURPOSE_EN);

const STATEMENT_FORMAT_DE: Record<StatementFormat, string> = {
  camt: "camt (XML)",
  csv_camt: "CSV-CAMT",
  pdf: "PDF-Kontoauszug",
  dkb_csv: "DKB-CSV",
  card_pdf: "Kreditkartenabrechnung (PDF)",
  depot_pdf: "Depot-Kontoauszug (PDF)",
};
const STATEMENT_FORMAT_EN: Record<StatementFormat, string> = {
  camt: "camt (XML)",
  csv_camt: "CSV-CAMT",
  pdf: "PDF statement",
  dkb_csv: "DKB CSV",
  card_pdf: "Credit card statement (PDF)",
  depot_pdf: "Depot cash statement (PDF)",
};
export const STATEMENT_FORMAT: Record<StatementFormat, string> = localized(STATEMENT_FORMAT_DE, STATEMENT_FORMAT_EN);

/** The pill of an import. `imported` is an import whose closing balance is still missing
 * (the API's "pending balance": a CSV export, which states none). */
const IMPORT_STATUS_DE: Record<ImportStatus, string> = {
  imported: "Saldo fehlt",
  reconciled: "abgestimmt",
  unreconciled: "nicht abgestimmt",
  failed: "fehlgeschlagen",
};
const IMPORT_STATUS_EN: Record<ImportStatus, string> = {
  imported: "Balance missing",
  reconciled: "reconciled",
  unreconciled: "not reconciled",
  failed: "failed",
};
export const IMPORT_STATUS: Record<ImportStatus, string> = localized(IMPORT_STATUS_DE, IMPORT_STATUS_EN);

const BALANCE_SOURCE_DE: Record<BalanceSource, string> = {
  file: "aus der Datei",
  user: "eingegeben (Online-Banking)",
  none: "noch keiner",
  bank: "vom Bankabruf (Kontostand der Bank)",
};
const BALANCE_SOURCE_EN: Record<BalanceSource, string> = {
  file: "from the file",
  user: "entered (online banking)",
  none: "none yet",
  bank: "from the bank sync (bank's balance)",
};
export const BALANCE_SOURCE: Record<BalanceSource, string> = localized(BALANCE_SOURCE_DE, BALANCE_SOURCE_EN);

/** A FinTS sync's state (phase-5.md §4.2). */
const SYNC_STATUS_DE: Record<SyncStatus, string> = {
  queued: "wartet",
  running: "läuft",
  needs_approval: "wartet auf Freigabe",
  importing: "importiert",
  done: "importiert",
  nothing_new: "nichts Neues",
  failed: "fehlgeschlagen",
};
const SYNC_STATUS_EN: Record<SyncStatus, string> = {
  queued: "waiting",
  running: "running",
  needs_approval: "waiting for approval",
  importing: "importing",
  done: "imported",
  nothing_new: "nothing new",
  failed: "failed",
};
export const SYNC_STATUS: Record<SyncStatus, string> = localized(SYNC_STATUS_DE, SYNC_STATUS_EN);

const SYNC_TRIGGER_DE: Record<SyncTrigger, string> = {
  manual: "von Hand",
  scheduled: "täglich",
};
const SYNC_TRIGGER_EN: Record<SyncTrigger, string> = {
  manual: "by hand",
  scheduled: "daily",
};
export const SYNC_TRIGGER: Record<SyncTrigger, string> = localized(SYNC_TRIGGER_DE, SYNC_TRIGGER_EN);

const DUPLICATE_STATUS_DE: Record<DuplicateStatus, string> = {
  open: "offen",
  kept_both: "beide behalten",
  merged: "zusammengeführt",
};
const DUPLICATE_STATUS_EN: Record<DuplicateStatus, string> = {
  open: "open",
  kept_both: "both kept",
  merged: "merged",
};
export const DUPLICATE_STATUS: Record<DuplicateStatus, string> = localized(DUPLICATE_STATUS_DE, DUPLICATE_STATUS_EN);

// --- allocations and the review queue (phase 2 step 4, §8.2, §8.3) ---

/** The fixed taxonomy (§8.2). The tax treatment and the Anlage V line come from the rule set,
 * never from this label, so renaming one changes no figure. */
const ALLOCATION_CATEGORY_DE: Record<AllocationCategory, string> = {
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
  // D159 amended (8) 15 Nov 2026 (migration 0037)
  hh_wage_replacement: "Elterngeld & Lohnersatz",
  hh_tax_refund: "Steuererstattung",
  hh_capital_income: "Zinsen und Dividenden",
  hh_other_income: "Sonstige Einnahme (privat)",
  hh_housing: "Sonstiges Wohnen",
  hh_groceries: "Sonstige Lebensmittel",
  hh_family_child: "Sonstiges Familie und Kind",
  hh_mobility: "Sonstige Mobilität",
  hh_insurance: "Sonstige Versicherungen",
  hh_subscriptions: "Sonstige Abos & Verträge",
  hh_leisure_travel: "Sonstige Freizeit & Hobbys",
  hh_health: "Sonstige Gesundheit",
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
  hh_interest_fees: "Zinsen und Gebühren",
  // D159 amended 13 Nov 2026 (migration 0029)
  hh_personal_care: "Friseur & Körperpflege",
  hh_clothing: "Kleidung & Schuhe",
  hh_dining: "Sonstiges Essen gehen",
  hh_home: "Haushalt & Einrichtung",
  hh_electronics: "Elektronik & Technik",
  hh_online_shopping: "Online-Einkäufe",
  hh_fees: "Behörden & Gebühren",
  hh_gifts: "Geschenke & Feiern",
  hh_family_transfers: "Geld an Familie / ins Ausland",
  hh_telecom: "Sonstiges Telefon & Internet",
  hh_work_education: "Arbeitsmittel & Fortbildung",
  hh_sport: "Sport & Fitness",
  hh_travel: "Sonstiges Reisen",
  hh_investment: "Sonstige Geldanlage",
  // D159 amended (6) 15 Nov 2026 (migration 0036): the Geldanlage subcategories
  hh_investment_property: "Immobilien",
  hh_investment_securities: "Wertpapiere & ETF",
  hh_investment_crypto: "Krypto",
  hh_investment_savings: "Sparen & Tagesgeld",
  hh_refund: "Erstattung",
  // D159 amended 14 Nov 2026 (migration 0031)
  hh_fuel: "Tanken (Auto)",
  // D159 amended 14 Nov 2026 (migration 0032)
  hh_streaming: "Streaming (Video)",
  // D159 amended (4) 14 Nov 2026 (migration 0033)
  hh_pay_tv: "Kabel- & Pay-TV",
  hh_music_audio: "Musik & Hörbücher",
  hh_software: "Software & KI",
  hh_cloud: "Cloud & Server",
  hh_gaming: "Gaming",
  hh_news: "Zeitungen & Zeitschriften",
  hh_memberships: "Mitgliedschaften & Vereine",
  hh_public_transport: "Bahn, Bus & ÖPNV",
  hh_taxi: "Taxi & Fahrdienste",
  hh_parking: "Parken & Maut",
  hh_car_service: "Auto: Werkstatt & Wartung",
  hh_car_tax: "Auto: Kfz-Steuer & Zulassung",
  // D159 amended (7) 15 Nov 2026 (migration 0037)
  hh_car_purchase: "Auto: Kauf & Finanzierung",
  hh_supermarket: "Supermarkt",
  hh_drugstore: "Drogerie & Kosmetik",
  hh_bakery: "Bäckerei, Kiosk & Markt",
  hh_restaurant: "Restaurant",
  hh_cafe: "Café",
  // D159 amended (5) 15 Nov 2026 (migration 0034)
  hh_bar: "Bar & Kneipe",
  hh_sweets: "Eis & Süßes",
  hh_fast_food: "Imbiss & Fast Food",
  hh_delivery: "Lieferdienst",
  hh_rent: "Miete",
  hh_energy: "Strom, Gas & Heizung",
  hh_broadcast_fee: "Rundfunkbeitrag",
  hh_flat_search: "Wohnungssuche & Umzug",
  hh_ins_car: "Kfz-Versicherung",
  hh_ins_liability_contents: "Haftpflicht & Hausrat",
  hh_ins_health: "Kranken & Pflege",
  hh_ins_life: "Leben, Berufsunfähigkeit & Rente",
  hh_pharmacy: "Apotheke",
  hh_doctor: "Arzt & Labor",
  hh_dental_optical: "Zahnarzt & Brille",
  hh_outings: "Ausflüge & Kultur",
  hh_books_media: "Bücher & Medien",
  hh_hobbies: "Hobbys",
  hh_lodging: "Unterkunft",
  hh_flights: "Flüge",
  hh_kids_gear: "Baby- & Kinderausstattung",
  hh_toys: "Spielzeug",
  hh_pregnancy_birth: "Schwangerschaft & Geburt",
  hh_mobile_phone: "Mobilfunk",
  hh_internet: "Internet & Festnetz",
  hh_postage: "Porto & Versand",
  security_purchase: "Wertpapierkauf",
  security_sale: "Wertpapierverkauf",
  distribution: "Ausschüttung oder Dividende",
  capital_income_tax: "Kapitalertragsteuer und Vorabpauschale",
  depot_fee: "Depot- und Verwaltergebühr",
};
const ALLOCATION_CATEGORY_EN: Record<AllocationCategory, string> = {
  rent_cold: "Base rent",
  operating_costs_prepayment: "Service charge prepayment",
  operating_costs_settlement: "Service charge settlement (back-payment or refund)",
  deposit_in: "Kaution received",
  deposit_out: "Kaution repaid",
  owner_transfer_in: "Contribution from an owner",
  owner_transfer_out: "Withdrawal to an owner",
  loan_interest: "Loan interest",
  loan_principal: "Repayment",
  loan_fees: "Loan costs (Geldbeschaffungskosten)",
  loan_disbursement: "Loan payout",
  hausgeld_umlagefaehig: "Hausgeld, rechargeable to tenants",
  hausgeld_verwaltung: "Hausgeld, not rechargeable (administration)",
  hausgeld_ruecklage: "Hausgeld, maintenance reserve",
  property_management: "Rental management (Sondereigentumsverwaltung)",
  grundsteuer: "Grundsteuer",
  insurance: "Insurance",
  electricity: "Electricity",
  repairs: "Maintenance (repair)",
  renovation: "Renovation or modernisation",
  purchase_price: "Purchase price payment",
  acquisition_cost: "Incidental purchase costs",
  professional_fees: "Advice and legal costs",
  bank_fees: "Account and bank fees",
  other_income: "Other income",
  other_expense: "Other expense",
  uncategorized: "Still open (not categorised)",
  hh_salary: "Salary",
  hh_child_benefit: "Child benefit (Kindergeld)",
  hh_wage_replacement: "Parental allowance & wage replacement",
  hh_tax_refund: "Tax refund",
  hh_capital_income: "Interest and dividends",
  hh_other_income: "Other income (private)",
  hh_housing: "Other housing",
  hh_groceries: "Other food",
  hh_family_child: "Other family and children",
  hh_mobility: "Other mobility",
  hh_insurance: "Other insurance",
  hh_subscriptions: "Other subscriptions & contracts",
  hh_leisure_travel: "Other leisure & hobbies",
  hh_health: "Other health",
  hh_other: "Other",
  hh_donation: "Donation",
  hh_household_services: "Household service or craftsman",
  hh_childcare: "Childcare",
  hh_property_cost: "Property cost, paid privately",
  hh_tax_payment: "Income tax (prepayment or back-payment)",
  internal_transfer_in: "Transfer from own account",
  internal_transfer_out: "Transfer to own account",
  hh_cash: "Cash withdrawal",
  hh_credit_card: "Credit card settlement",
  hh_interest_fees: "Interest and fees",
  hh_personal_care: "Hairdresser & personal care",
  hh_clothing: "Clothing & shoes",
  hh_dining: "Other eating out",
  hh_home: "Home & furnishing",
  hh_electronics: "Electronics & tech",
  hh_online_shopping: "Online shopping",
  hh_fees: "Authorities & fees",
  hh_gifts: "Gifts & celebrations",
  hh_family_transfers: "Money to family / abroad",
  hh_telecom: "Other phone & internet",
  hh_work_education: "Work equipment & training",
  hh_sport: "Sport & fitness",
  hh_travel: "Other travel",
  hh_investment: "Other investments",
  hh_investment_property: "Property",
  hh_investment_securities: "Securities & ETFs",
  hh_investment_crypto: "Crypto",
  hh_investment_savings: "Savings & instant access",
  hh_refund: "Refund",
  hh_fuel: "Fuel (car)",
  hh_streaming: "Streaming (video)",
  hh_pay_tv: "Cable & pay TV",
  hh_music_audio: "Music & audiobooks",
  hh_software: "Software & AI",
  hh_cloud: "Cloud & servers",
  hh_gaming: "Gaming",
  hh_news: "Newspapers & magazines",
  hh_memberships: "Memberships & clubs",
  hh_public_transport: "Train, bus & public transport",
  hh_taxi: "Taxi & ride services",
  hh_parking: "Parking & tolls",
  hh_car_service: "Car: garage & servicing",
  hh_car_tax: "Car: vehicle tax & registration",
  hh_car_purchase: "Car: purchase & financing",
  hh_supermarket: "Supermarket",
  hh_drugstore: "Drugstore & cosmetics",
  hh_bakery: "Bakery, kiosk & market",
  hh_restaurant: "Restaurant",
  hh_cafe: "Café",
  hh_bar: "Bar & pub",
  hh_sweets: "Ice cream & sweets",
  hh_fast_food: "Snacks & fast food",
  hh_delivery: "Food delivery",
  hh_rent: "Rent",
  hh_energy: "Electricity, gas & heating",
  hh_broadcast_fee: "Broadcasting fee (Rundfunkbeitrag)",
  hh_flat_search: "Flat search & moving",
  hh_ins_car: "Car insurance",
  hh_ins_liability_contents: "Liability & contents",
  hh_ins_health: "Health & care",
  hh_ins_life: "Life, disability & pension",
  hh_pharmacy: "Pharmacy",
  hh_doctor: "Doctor & lab",
  hh_dental_optical: "Dentist & glasses",
  hh_outings: "Outings & culture",
  hh_books_media: "Books & media",
  hh_hobbies: "Hobbies",
  hh_lodging: "Accommodation",
  hh_flights: "Flights",
  hh_kids_gear: "Baby & children's equipment",
  hh_toys: "Toys",
  hh_pregnancy_birth: "Pregnancy & birth",
  hh_mobile_phone: "Mobile phone",
  hh_internet: "Internet & landline",
  hh_postage: "Postage & shipping",
  security_purchase: "Securities purchase",
  security_sale: "Securities sale",
  distribution: "Distribution or dividend",
  capital_income_tax: "Capital gains tax and Vorabpauschale",
  depot_fee: "Depot and management fee",
};
export const ALLOCATION_CATEGORY: Record<AllocationCategory, string> = localized(ALLOCATION_CATEGORY_DE, ALLOCATION_CATEGORY_EN);

/** The household, transfer and depot codes (phase 6 D159): for private accounts only. The
 * server refuses them on a rental line (allocation.category_not_for_account), where they would
 * be on no Anlage V line; mirrors rules/household/taxonomy.PRIVATE_ONLY: every `hh_*` code (so
 * a new household code can never leak into the rental picker), the two transfer codes and the
 * depot's codes. */
export const PRIVATE_ONLY_CATEGORIES: ReadonlySet<AllocationCategory> = new Set<AllocationCategory>([
  ...(Object.keys(ALLOCATION_CATEGORY) as AllocationCategory[]).filter((category) => category.startsWith("hh_")),
  "internal_transfer_in",
  "internal_transfer_out",
  "security_purchase",
  "security_sale",
  "distribution",
  "capital_income_tax",
  "depot_fee",
]);

/** The tax-relevant codes (relevance tax in rules/household/taxonomy.py, D159, D160): never
 * auto-confirmed and never a learned rule, since each proposes an input a person confirms. */
export const TAX_RELEVANT_CATEGORIES: ReadonlySet<AllocationCategory> = new Set<AllocationCategory>([
  "hh_donation",
  "hh_household_services",
  "hh_childcare",
  "hh_property_cost",
  "hh_tax_payment",
  "capital_income_tax",
]);

/** The Haushalt screen's groups (phase 6 §13, mockup 10; rules/household/taxonomy.py). */
const HOUSEHOLD_GROUP_DE: Record<HouseholdGroup, string> = {
  income: "Einnahmen",
  housing: "Wohnen",
  groceries: "Lebensmittel und Drogerie",
  family_child: "Familie und Kind",
  mobility: "Mobilität",
  insurance: "Versicherungen",
  subscriptions: "Abos und Verträge",
  leisure_travel: "Freizeit & Hobbys",
  health: "Gesundheit",
  other: "Sonstiges",
  tax: "Steuerlich relevant",
  movements: "Bewegungen",
  depot: "Depot",
  // D159 amended 13 Nov 2026: one group per new code
  personal_care: "Friseur & Körperpflege",
  clothing: "Kleidung & Schuhe",
  dining: "Essen gehen & bestellen",
  home: "Haushalt & Einrichtung",
  electronics: "Elektronik & Technik",
  online_shopping: "Online-Einkäufe",
  fees: "Behörden & Gebühren",
  gifts: "Geschenke & Feiern",
  family_transfers: "Geld an Familie / ins Ausland",
  telecom: "Telefon & Internet",
  work_education: "Arbeitsmittel & Fortbildung",
  sport: "Sport & Fitness",
  travel: "Reisen",
  investment: "Geldanlage",
  refund: "Erstattung",
};
const HOUSEHOLD_GROUP_EN: Record<HouseholdGroup, string> = {
  income: "Income",
  housing: "Housing",
  groceries: "Groceries and drugstore",
  family_child: "Family and children",
  mobility: "Mobility",
  insurance: "Insurance",
  subscriptions: "Subscriptions and contracts",
  leisure_travel: "Leisure & hobbies",
  health: "Health",
  other: "Other",
  tax: "Tax-relevant",
  movements: "Movements",
  depot: "Depot",
  personal_care: "Hairdresser & personal care",
  clothing: "Clothing & shoes",
  dining: "Eating out & delivery",
  home: "Home & furnishings",
  electronics: "Electronics & tech",
  online_shopping: "Online shopping",
  fees: "Authorities & fees",
  gifts: "Gifts & celebrations",
  family_transfers: "Money to family / abroad",
  telecom: "Phone & internet",
  work_education: "Work equipment & training",
  sport: "Sport & fitness",
  travel: "Travel",
  investment: "Investments",
  refund: "Refund",
};
export const HOUSEHOLD_GROUP: Record<HouseholdGroup, string> = localized(HOUSEHOLD_GROUP_DE, HOUSEHOLD_GROUP_EN);

/** The household spending groups with subcategories (phase 6 D159 amended 14 Nov 2026,
 * migration 0032; rules/household/taxonomy.FALLBACK): each group's codes, the subcategories
 * first and the group's fallback („Sonstige …“) last. The picker shows one block per group, and
 * the Haushalt screen lists a group's codes under its row in this order. */
export const HOUSEHOLD_SPENDING_GROUPS: { group: HouseholdGroup; categories: AllocationCategory[] }[] = [
  { group: "housing", categories: ["hh_rent", "hh_energy", "hh_broadcast_fee", "hh_flat_search", "hh_housing"] },
  { group: "groceries", categories: ["hh_supermarket", "hh_drugstore", "hh_bakery", "hh_groceries"] },
  { group: "dining", categories: ["hh_restaurant", "hh_cafe", "hh_bar", "hh_sweets", "hh_fast_food", "hh_delivery", "hh_dining"] },
  {
    group: "mobility",
    categories: [
      "hh_fuel",
      "hh_public_transport",
      "hh_taxi",
      "hh_parking",
      "hh_car_service",
      "hh_car_tax",
      "hh_car_purchase",
      "hh_mobility",
    ],
  },
  { group: "insurance", categories: ["hh_ins_car", "hh_ins_liability_contents", "hh_ins_health", "hh_ins_life", "hh_insurance"] },
  {
    group: "subscriptions",
    categories: [
      "hh_streaming",
      "hh_pay_tv",
      "hh_music_audio",
      "hh_software",
      "hh_cloud",
      "hh_gaming",
      "hh_news",
      "hh_memberships",
      "hh_subscriptions",
    ],
  },
  { group: "telecom", categories: ["hh_mobile_phone", "hh_internet", "hh_telecom"] },
  { group: "health", categories: ["hh_pharmacy", "hh_doctor", "hh_dental_optical", "hh_health"] },
  { group: "leisure_travel", categories: ["hh_outings", "hh_books_media", "hh_hobbies", "hh_leisure_travel"] },
  { group: "travel", categories: ["hh_lodging", "hh_flights", "hh_travel"] },
  { group: "family_child", categories: ["hh_kids_gear", "hh_toys", "hh_pregnancy_birth", "hh_family_child"] },
  { group: "other", categories: ["hh_postage", "hh_interest_fees", "hh_other"] },
];

/** The household codes in the groups of rules/household/taxonomy.py (phase 6 D159), for the
 * category picker of a private line; the API's allowed set per purpose decides. Spending is one
 * block per group with subcategories (HOUSEHOLD_SPENDING_GROUPS), and the spending codes that
 * are a group of their own (0029's finer codes) share one block „Weitere Ausgaben“; „Sonstiges“
 * comes last among the spending blocks. */
const HOUSEHOLD_GROUPS: { label: () => string; categories: AllocationCategory[] }[] = [
  {
    label: () => HOUSEHOLD_GROUP.income,
    categories: ["hh_salary", "hh_child_benefit", "hh_wage_replacement", "hh_tax_refund", "hh_capital_income", "hh_other_income"],
  },
  ...HOUSEHOLD_SPENDING_GROUPS.filter((entry) => entry.group !== "other").map((entry) => ({
    label: () => HOUSEHOLD_GROUP[entry.group],
    categories: entry.categories,
  })),
  {
    label: () => (english() ? "Other spending" : "Weitere Ausgaben"),
    categories: [
      "hh_personal_care",
      "hh_clothing",
      "hh_home",
      "hh_electronics",
      "hh_online_shopping",
      "hh_sport",
      "hh_gifts",
      "hh_fees",
      "hh_family_transfers",
      "hh_work_education",
      "hh_refund",
    ],
  },
  { label: () => HOUSEHOLD_GROUP.other, categories: HOUSEHOLD_SPENDING_GROUPS.find((entry) => entry.group === "other")?.categories ?? [] },
  {
    label: () => HOUSEHOLD_GROUP.tax,
    categories: ["hh_donation", "hh_household_services", "hh_childcare", "hh_property_cost", "hh_tax_payment"],
  },
  // D159 amended (6) 15 Nov 2026 (migration 0036): the Geldanlage group (saved, not spent), its
  // fallback „Sonstige Geldanlage“ last.
  {
    label: () => HOUSEHOLD_GROUP.investment,
    categories: ["hh_investment_property", "hh_investment_securities", "hh_investment_crypto", "hh_investment_savings", "hh_investment"],
  },
  { label: () => HOUSEHOLD_GROUP.movements, categories: ["internal_transfer_in", "internal_transfer_out", "hh_cash", "hh_credit_card"] },
  { label: () => (english() ? "Account" : "Konto"), categories: ["bank_fees", "uncategorized"] },
];

const DEPOT_GROUP: { label: () => string; categories: AllocationCategory[] } = {
  label: () => HOUSEHOLD_GROUP.depot,
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
  return groups.map((group) => ({ label: group.label(), categories: labelled(group.categories) }));
}

/** Who proposed an allocation set. */
const ALLOCATION_METHOD_DE: Record<AllocationMethod, string> = {
  rule: "gelernte Regel",
  matcher_rent: "Mietabgleich",
  matcher_loan: "Tilgungsplan des Darlehens",
  matcher_owner: "Überweisung eines Eigentümers",
  matcher_hausgeld: "Wirtschaftsplan",
  matcher_transfer: "Übertrag zwischen eigenen Konten",
  matcher_card: "Kreditkartenabrechnung (Zinsen und Gebühren)",
  matcher_depot: "Depot-Kontoauszug (Buchungstext)",
  document_match: "passender Beleg",
  llm: "KI",
  user: "von Hand",
};
const ALLOCATION_METHOD_EN: Record<AllocationMethod, string> = {
  rule: "learned rule",
  matcher_rent: "rent match",
  matcher_loan: "loan repayment schedule",
  matcher_owner: "transfer from an owner",
  matcher_hausgeld: "Wirtschaftsplan",
  matcher_transfer: "transfer between own accounts",
  matcher_card: "credit card statement (interest and fees)",
  matcher_depot: "depot cash statement (booking text)",
  document_match: "matching receipt",
  llm: "AI",
  user: "by hand",
};
export const ALLOCATION_METHOD: Record<AllocationMethod, string> = localized(ALLOCATION_METHOD_DE, ALLOCATION_METHOD_EN);

/** The pill of a review-queue item. */
const INBOX_ITEM_KIND_DE: Record<InboxItemKind, string> = {
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
  bank_statements_refused: "Kontoauszüge nicht freigeschaltet",
  email_received: "Per Mail erhalten",
  email_failed: "Mail nicht übernommen",
  email_inbox_failed: "Posteingang gestört",
  reminder: "Termin",
  // phase 6 (D161, D162)
  transfer_link: "Gegenbuchung wählen",
  transfer_unmatched: "Überweisung ohne Gegenstück",
  property_cost_row: "Privat bezahlte Kosten",
  property_cost_bill: "Rechnung fehlt",
  property_cost_year: "Jahr der Zahlung",
  property_cost_document: "Beleg prüfen",
  // phase 6 step 4
  holdings_mismatch: "Bestand weicht ab",
  // phase 6 D292
  depot_trade_without_units: "Kauf ohne Stückzahl",
  // phase 6 D292 amended (b)
  depot_statement_trade_differs: "Kontoauszug und Abrechnung weichen ab",
  // phase 6 step 5b (D167, D174)
  price_source_failed: "Kursquelle gestört",
  price_no_source: "Kursquelle fehlt",
  // phase 6 D264 (7)
  price_report_differs: "Kurs weicht vom Depotbericht ab",
  // phase 6 step 5a (D166, D178)
  exchange_pending_balance: "Bestand fehlt",
  exchange_unreconciled: "Bestand weicht ab",
  exchange_withdrawals: "Auszahlungen",
  exchange_movement: "Eingang prüfen",
  // phase 6 D162 amended (7): a returned payment and the debit it brings back
  returned_payment: "Rücküberweisung erkannt",
  // phase 7 wallet screens (D183, D185, D186)
  wallet_groups: "Transaktionen prüfen",
  wallet_change: "Wechselgeld?",
  wallet_question: "Transaktion prüfen",
  wallet_sync: "Abruf",
  wallet_unreconciled: "Saldo weicht ab",
  wallet_coins_left: "Coins abgegangen",
  // phase 6 D260 amended (9)
  app_secret_unreadable: "Schlüssel unlesbar",
};
const INBOX_ITEM_KIND_EN: Record<InboxItemKind, string> = {
  proposal: "Suggestion",
  read_failed: "Not read",
  blocked_tenant_data: "Tenant data",
  import_unreconciled: "Not reconciled",
  import_pending_balance: "Balance missing",
  possible_duplicate: "Possible duplicate",
  allocation_proposed: "Check suggestion",
  unmatched: "Not categorised",
  questionnaire_open: "Yearly questions",
  bank_approval_needed: "Approval needed",
  bank_sync_failed: "Bank sync failed",
  bank_balance_drift: "Balance differs",
  bank_statements_refused: "Statements not enabled",
  email_received: "Received by email",
  email_failed: "Email not taken over",
  email_inbox_failed: "Inbox disrupted",
  reminder: "Due date",
  transfer_link: "Choose matching transaction",
  transfer_unmatched: "Transfer without counterpart",
  property_cost_row: "Privately paid costs",
  property_cost_bill: "Invoice missing",
  property_cost_year: "Year of payment",
  property_cost_document: "Check receipt",
  holdings_mismatch: "Holding differs",
  depot_trade_without_units: "Purchase without units",
  depot_statement_trade_differs: "Statement and confirmation differ",
  price_source_failed: "Price source disrupted",
  price_no_source: "Price source missing",
  price_report_differs: "Price differs from the depot report",
  exchange_pending_balance: "Holding missing",
  exchange_unreconciled: "Holding differs",
  exchange_withdrawals: "Withdrawals",
  exchange_movement: "Check receipt of funds",
  returned_payment: "Returned payment found",
  wallet_groups: "Check transactions",
  wallet_change: "Change?",
  wallet_question: "Check transaction",
  wallet_sync: "Sync",
  wallet_unreconciled: "Balance differs",
  wallet_coins_left: "Coins left",
  app_secret_unreadable: "Key unreadable",
};
export const INBOX_ITEM_KIND: Record<InboxItemKind, string> = localized(INBOX_ITEM_KIND_DE, INBOX_ITEM_KIND_EN);

/** The id of the heading „Zugangsschlüssel (API keys)“ on Einstellungen (phase 6 D260), the
 * target of the links that send a person there. */
export const APP_SECRETS_ANCHOR = "zugangsschluessel";

/** The id of the heading „KI“ on Einstellungen, where „Private Konten mit KI zuordnen“ is
 * switched on (phase 6 D168); the KI panel on Planung › Haushalt links there (phase 9 D253). */
export const AI_SETTINGS_ANCHOR = "ki";

/** asset_movement.kind (phase 6 D165, D166): what a coin or fund movement was, in plain words. */
const MOVEMENT_KIND_DE: Record<MovementKind, string> = {
  acquisition: "Kauf",
  disposal: "Verkauf",
  internal_transfer_in: "Von eigener Wallet",
  internal_transfer_out: "An eigene Wallet",
  fee: "Gebühr",
  staking_reward: "Staking-Belohnung",
  cashback_reward: "Bonus oder Prämie",
  distribution: "Ausschüttung",
  capital_income_tax: "Kapitalertragsteuer",
  uncategorized: "Unbekannt",
  balance_correction: "Korrektur (von Hand)",
  // phase 7 (D183, D187)
  airdrop: "Airdrop",
  spam: "Spam-Token",
  lp_deposit: "Einzahlung in Liquiditätspool",
  lp_withdrawal: "Auszahlung aus Liquiditätspool",
};
const MOVEMENT_KIND_EN: Record<MovementKind, string> = {
  acquisition: "Purchase",
  disposal: "Sale",
  internal_transfer_in: "From own wallet",
  internal_transfer_out: "To own wallet",
  fee: "Fee",
  staking_reward: "Staking reward",
  cashback_reward: "Bonus or reward",
  distribution: "Distribution",
  capital_income_tax: "Capital gains tax",
  uncategorized: "Unknown",
  balance_correction: "Correction (by hand)",
  airdrop: "Airdrop",
  spam: "Spam token",
  lp_deposit: "Deposit into liquidity pool",
  lp_withdrawal: "Withdrawal from liquidity pool",
};
export const MOVEMENT_KIND: Record<MovementKind, string> = localized(MOVEMENT_KIND_DE, MOVEMENT_KIND_EN);


// --- The income tax return's inputs (phase-4b.md §7, §10) --------------------------------

/** What a return input row is (return_input.kind, D97). */
const RETURN_INPUT_KIND_DE: Record<ReturnInputKind, string> = {
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
  no_pay: "Kein Arbeitslohn",
};
const RETURN_INPUT_KIND_EN: Record<ReturnInputKind, string> = {
  wage_certificate: "Lohnsteuerbescheinigung",
  payslip: "Gehaltsabrechnung",
  commute: "Commute",
  home_office: "Home office days",
  work_room: "Home study (Arbeitszimmer)",
  work_equipment: "Work equipment",
  telecom: "Telecommunications",
  professional_association: "Professional association fees",
  training: "Training",
  other_expense: "Other Werbungskosten",
  other_insurance: "Other insurance",
  replacement_income: "Wage-replacement benefits",
  vorsorge_override: "Provident contributions by hand",
  child: "Child",
  childcare_cost: "Childcare costs",
  household_services: "Household services (§ 35a)",
  special_expense: "Donation",
  no_pay: "No pay",
};
export const RETURN_INPUT_KIND: Record<ReturnInputKind, string> = localized(RETURN_INPUT_KIND_DE, RETURN_INPUT_KIND_EN);

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
const PAY_FIELD_EN: Record<string, string> = {
  tax_year: "Tax year",
  "value.period_from": "Period from",
  "value.period_to": "Period to",
  "value.period_month": "Pay month",
  "value.employer": "Employer",
  "value.tax_class": "Tax class",
  "value.tax_class_factor": "Factor (tax class IV)",
  "value.church_tax_marked": "Church tax marker",
  "value.child_allowance_count": "Child allowances",
  "value.months_remaining_paid": "Remaining months",
  "value.gross": "No. 3 gross pay",
  "value.income_tax": "No. 4 wage tax",
  "value.soli": "No. 5 solidarity surcharge",
  "value.church_tax": "No. 6 church tax",
  "value.church_tax_spouse": "No. 7 spouse's church tax",
  "value.replacement_benefits": "No. 15 wage-replacement benefits",
  "value.short_time_allowance": "No. 15a short-time work allowance",
  "value.employer_commute_tax_free": "No. 17 commuting allowance, tax-free",
  "value.employer_commute_flat_taxed": "No. 18 commuting allowance, flat-rate taxed",
  "value.pension_employer": "No. 22a pension insurance, employer",
  "value.pension_employee": "No. 23a pension insurance, employee",
  "value.pension_employee_professional": "No. 23b professional pension scheme",
  "value.subsidy_health": "No. 24a health insurance subsidy",
  "value.subsidy_private_health": "No. 24b private health insurance subsidy",
  "value.subsidy_care": "No. 24c care insurance subsidy",
  "value.health_employee": "No. 25 health insurance",
  "value.care_employee": "No. 26 care insurance",
  "value.unemployment_employee": "No. 27 unemployment insurance",
};
const PAYSLIP_FIGURE_EN: Record<string, string> = {
  gross: "Taxable gross",
  income_tax: "Wage tax",
  soli: "Solidarity surcharge",
  church_tax: "Church tax",
  pension_employee: "Pension insurance",
  health_employee: "Health insurance",
  care_employee: "Care insurance",
  unemployment_employee: "Unemployment insurance",
  tax_free_benefits: "tax-free benefits",
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
    if (english()) return `${PAYSLIP_FIGURE_EN[figure[1]] ?? figure[1]} (${figure[2] === "month" ? "month" : "year to date"})`;
    return `${PAYSLIP_FIGURE[figure[1]] ?? figure[1]} (${figure[2] === "month" ? "Monat" : "Jahr bisher"})`;
  }
  return (english() ? PAY_FIELD_EN : PAY_FIELD)[field] ?? field;
}

/** Where an owner's gross pay comes from (OwnerZve.gross_source, D98); the screen adds the
 * year or the payslip month. */
const GROSS_SOURCE_DE: Record<GrossSource, string> = {
  certificate: "laut Lohnsteuerbescheinigung",
  payslip: "Hochrechnung aus Gehaltsabrechnung",
  expected: "Schätzung",
  declared_none: "kein Arbeitslohn angegeben",
};
const GROSS_SOURCE_EN: Record<GrossSource, string> = {
  certificate: "per Lohnsteuerbescheinigung",
  payslip: "projected from payslip",
  expected: "Estimate",
  declared_none: "no pay stated",
};
export const GROSS_SOURCE: Record<GrossSource, string> = localized(GROSS_SOURCE_DE, GROSS_SOURCE_EN);

/** A §35a cost's class (HouseholdServicesFact.service_class, D112). */
const HOUSEHOLD_SERVICE_CLASS_DE: Record<HouseholdServiceClass, string> = {
  handwerker: "Handwerkerleistung",
  haushaltsnah: "Haushaltsnahe Dienstleistung",
  minijob: "Minijob im Haushalt",
};
const HOUSEHOLD_SERVICE_CLASS_EN: Record<HouseholdServiceClass, string> = {
  handwerker: "Tradesperson's work",
  haushaltsnah: "Household service",
  minijob: "Mini-job in the household",
};
export const HOUSEHOLD_SERVICE_CLASS: Record<HouseholdServiceClass, string> = localized(HOUSEHOLD_SERVICE_CLASS_DE, HOUSEHOLD_SERVICE_CLASS_EN);

/** Where a §35a labour cost was read from (HouseholdServicesFact.source). */
const HOUSEHOLD_SERVICE_SOURCE_DE: Record<HouseholdServiceSource, string> = {
  own_invoice: "eigene Rechnung",
  nebenkostenabrechnung: "Nebenkostenabrechnung der eigenen Wohnung",
};
const HOUSEHOLD_SERVICE_SOURCE_EN: Record<HouseholdServiceSource, string> = {
  own_invoice: "own invoice",
  nebenkostenabrechnung: "service charge statement of your own flat",
};
export const HOUSEHOLD_SERVICE_SOURCE: Record<HouseholdServiceSource, string> = localized(HOUSEHOLD_SERVICE_SOURCE_DE, HOUSEHOLD_SERVICE_SOURCE_EN);

/** The section of a Jahresfrage (Question.section, D110). */
const QUESTION_SECTION_DE: Record<QuestionSection, string> = {
  einkommen: "Einkommen",
  werbungskosten: "Werbungskosten",
  haushalt: "Haushalt",
  kinder: "Kinder",
  immobilie: "Immobilie",
};
const QUESTION_SECTION_EN: Record<QuestionSection, string> = {
  einkommen: "Income",
  werbungskosten: "Werbungskosten",
  haushalt: "Household",
  kinder: "Children",
  immobilie: "Property",
};
export const QUESTION_SECTION: Record<QuestionSection, string> = localized(QUESTION_SECTION_DE, QUESTION_SECTION_EN);

/** A Jahresfrage's coded answer (questionnaire_answer.answer, D110). */
const QUESTIONNAIRE_ANSWER_DE: Record<QuestionnaireAnswerValue, string> = {
  yes: "Ja",
  no: "Nein",
  not_applicable: "Trifft nicht zu",
};
const QUESTIONNAIRE_ANSWER_EN: Record<QuestionnaireAnswerValue, string> = {
  yes: "Yes",
  no: "No",
  not_applicable: "Not applicable",
};
export const QUESTIONNAIRE_ANSWER: Record<QuestionnaireAnswerValue, string> = localized(QUESTIONNAIRE_ANSWER_DE, QUESTIONNAIRE_ANSWER_EN);

/** A commute route's kind (CommuteFact.kind). */
const COMMUTE_KIND_DE: Record<CommuteKind, string> = {
  first_workplace: "Erste Tätigkeitsstätte",
  collection_point: "Sammelpunkt oder weiträumiges Tätigkeitsgebiet",
};
const COMMUTE_KIND_EN: Record<CommuteKind, string> = {
  first_workplace: "First place of work",
  collection_point: "Collection point or wide work area",
};
export const COMMUTE_KIND: Record<CommuteKind, string> = localized(COMMUTE_KIND_DE, COMMUTE_KIND_EN);

/** A work item's class (EquipmentFact.item_class, D108). */
const ITEM_CLASS_DE: Record<ItemClass, string> = {
  computer: "Computer",
  software: "Software",
  phone: "Telefon",
  furniture: "Möbel",
  tools: "Werkzeug",
  subscription: "Abonnement",
  other: "Sonstiges",
};
const ITEM_CLASS_EN: Record<ItemClass, string> = {
  computer: "Computer",
  software: "Software",
  phone: "Phone",
  furniture: "Furniture",
  tools: "Tools",
  subscription: "Subscription",
  other: "Other",
};
export const ITEM_CLASS: Record<ItemClass, string> = localized(ITEM_CLASS_DE, ITEM_CLASS_EN);

/** An other Werbungskosten item's class (OtherExpenseFact.expense_class). */
const OTHER_EXPENSE_CLASS_DE: Record<OtherExpenseClass, string> = {
  account_fee: "Kontoführungsgebühren",
  application: "Bewerbungskosten",
  ferry_flight: "Fähr- oder Flugkosten zur Arbeit",
  other: "Sonstiges",
};
const OTHER_EXPENSE_CLASS_EN: Record<OtherExpenseClass, string> = {
  account_fee: "Account fees",
  application: "Job application costs",
  ferry_flight: "Ferry or flight costs to work",
  other: "Other",
};
export const OTHER_EXPENSE_CLASS: Record<OtherExpenseClass, string> = localized(OTHER_EXPENSE_CLASS_DE, OTHER_EXPENSE_CLASS_EN);

/** The Arbeitszimmer's choice between the flat amount and the actual costs (D106). */
const WORK_ROOM_PREFER_DE: Record<WorkRoomPrefer, string> = {
  flat: "Jahrespauschale",
  actual: "Tatsächliche Kosten",
};
const WORK_ROOM_PREFER_EN: Record<WorkRoomPrefer, string> = {
  flat: "Annual flat rate",
  actual: "Actual costs",
};
export const WORK_ROOM_PREFER: Record<WorkRoomPrefer, string> = localized(WORK_ROOM_PREFER_DE, WORK_ROOM_PREFER_EN);

/** A privately paid rental cost's class (PrivateCostValue.cost_class, D111; financing D134). */
const PRIVATE_COST_CLASS_DE: Record<PrivateCostClass, string> = {
  travel: "Fahrten zur Wohnung",
  postage: "Porto",
  advertising: "Anzeigen",
  association: "Vermieterverein",
  legal_insurance: "Rechtsschutzversicherung",
  software: "Software",
  financing: "Geldbeschaffungskosten (z. B. Notar und Grundbuch für die Grundschuld)",
  other: "Sonstiges",
};
const PRIVATE_COST_CLASS_EN: Record<PrivateCostClass, string> = {
  travel: "Trips to the flat",
  postage: "Postage",
  advertising: "Adverts",
  association: "Landlords' association",
  legal_insurance: "Legal expenses insurance",
  software: "Software",
  financing: "Loan costs (Geldbeschaffungskosten, e.g. notary and Grundbuch for the Grundschuld)",
  other: "Other",
};
export const PRIVATE_COST_CLASS: Record<PrivateCostClass, string> = localized(PRIVATE_COST_CLASS_DE, PRIVATE_COST_CLASS_EN);

// --- The Anlage V draft (phase-3.md §11) ----------------------------------------------

type Params = Record<string, string>;

/** An amount param ("4760.00") as "4.760,00 €"; "–" when the rule sent none. */
const money = (params: Params, key = "amount") => formatMoney(params[key]);

/** A payment_proof.missing bill (phase 3 D143): „die Rechnung Notar“ by the cost row's kind,
 * or „die Rechnung „Dachreparatur““ by the privately paid cost's label. */
export function proofBill(params: Params): string {
  if (english()) {
    if (params.kind) return `the ${ACQUISITION_COST_KIND[params.kind as AcquisitionCostKind] ?? params.kind} invoice`;
    return params.label ? `the invoice “${params.label}”` : "the invoice";
  }
  if (params.kind) return `die Rechnung ${ACQUISITION_COST_KIND[params.kind as AcquisitionCostKind] ?? params.kind}`;
  return params.label ? `die Rechnung „${params.label}“` : "die Rechnung";
}

/** The German sentence of a draft warning, its params filled in (amounts as decimal strings,
 * dates ISO, phase-3.md §5). The rules send codes and params only; the text lives here. */
const WARNING_TEXT_DE: Record<WarningCode, (params: Params) => string> = {
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
  // Phase 4b D195: a pay document of the year beats „Kein Arbeitslohn“.
  "anlage_n.no_pay_overruled": () =>
    "Für dieses Jahr ist „Kein Arbeitslohn“ angegeben, aber eine Lohnsteuerbescheinigung oder Gehaltsabrechnung liegt vor: sie zählt. Die Angabe bitte aufheben.",
  "return.progression_provisional": () =>
    "Lohnersatzleistungen sind noch nicht bescheinigt: der Steuersatz ist vorläufig.",
  "return.benefit_possible_double": () =>
    "Eine Lohnersatzleistung steht in der Bescheinigung und als eigene Angabe: bitte nur einmal erfassen.",
  // Phase 6 D159 amended (8): confirmed wage-replacement payments not yet typed.
  "return.benefit_booked": (p) =>
    `Lohnersatzleistungen ${p.tax_year ?? ""}: ${money(p, "difference")} auf dem Konto gebucht, noch nicht als Lohnersatzleistung eingetragen.`,
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

/** The same sentences in English (phase 10 D284); tax words stay German as glossary.md says. */
const WARNING_TEXT_EN: Record<WarningCode, (params: Params) => string> = {
  year_end_straddle: (p) =>
    `A payment${p.amount ? ` of ${money(p)}` : ""} is booked in one year and valued in the other; it counts in the year it was booked.`,
  "afa.no_split": () => "No purchase price split chosen: without a building share the AfA (depreciation) stays empty.",
  "afa.cost_row_unpaid": (p) =>
    `${p.kind ? (ACQUISITION_COST_KIND[p.kind as AcquisitionCostKind] ?? p.kind) : "Acquisition costs"} ${money(p)}: no payment from the rental account found (perhaps paid from another account). It still counts towards the AfA basis.`,
  "afa.acquisition_cost_without_row": (p) =>
    `A payment of acquisition costs of ${money(p)} has no matching entry under acquisition costs: it counts towards the AfA basis only once it is entered there.`,
  "afa.purchase_price_mismatch": (p) =>
    `The purchase price paid from the account, ${money(p, "paid")}, differs from the purchase price of ${money(p, "price")}.`,
  "afa.useful_life_report": (p) =>
    `AfA over a shorter actual useful life (${p.years ?? "?"} years from ${p.from_year ?? "?"}): state it in Zeile 33 „laut Erläuterung“; the Finanzamt usually asks for the appraisal.`,
  "afa.movable_asset_no_evidence": (p) =>
    `The value of the fitted kitchen (${money(p)}) is entered without a receipt. An estimate is allowed; please keep its basis (e.g. a printout of the kitchen planner) and add it as a receipt.`,
  "afa.movable_asset_exceeds_building": (p) =>
    `The fitted kitchen (${money(p, "deducted")} with acquisition costs) is not smaller than the building share (${money(p, "building")}): the building AfA falls to 0. Please check the kitchen's value or the purchase price split.`,
  "limit.exceeded": (p) =>
    `The 15% limit is exceeded${p.exceeded_in_year ? ` in ${p.exceeded_in_year}` : ""}: the renovation costs of the three-year window become construction costs and run through the AfA.`,
  "limit.prior_year_amendment": (p) =>
    `The 15% limit was exceeded ${p.exceeded_in_year ? `in ${p.exceeded_in_year}` : "later"}: ${money(p)} of this year become construction costs after the fact; the assessment for this year will be amended.`,
  "limit.estimated_net": () =>
    "The net amount is missing for at least one invoice; the 15% limit uses an estimated VAT there.",
  "limit.private_cost_undated": (p) =>
    `Repair “${p.label ?? ""}” (${money(p)}), paid from another account, without a date of the work: to be safe, the 15% limit counts it if it was paid in one of the three years after the purchase.`,
  "limit.private_cost_after_window": (p) =>
    `Repair “${p.label ?? ""}” (${money(p)}), paid from another account, dated ${formatDate(p.performed_on)}, shortly after the three-year window: when was the work done? Work within the window counts towards the 15% limit.`,
  "anschaffungsnah.performance_date_check": (p) =>
    `Costs of ${money(p)}, paid on ${formatDate(p.booking_date)}, shortly after the three-year window: when was the work done? A service period within the window counts towards the 15% limit.`,
  "renovation.undecided": (p) =>
    `Classify the renovation of ${money(p)}: maintenance (Erhaltungsaufwand) or construction costs (Herstellungskosten)? Not deducted until then.`,
  "interest.certificate_mismatch": (p) =>
    `Interest certificate ${money(p, "certified")}, booked ${money(p, "booked")}: difference ${money(p, "difference")}. Usually an instalment that the ten-day rule moves into the other year.`,
  "hausgeld.abrechnung_pending": () =>
    "The Hausgeldabrechnung is still missing: what the owners' association spent from the reserve is not included for now.",
  "income.other_review": (p) =>
    p.counted_as_rent === "1"
      ? `Income of ${money(p)}${p.counterparty_iban ? ` from ${p.counterparty_iban}` : ""} before the handover: counts as rent but is booked as other income.`
      : `Check other income of ${money(p)}${p.counterparty_iban ? ` from ${p.counterparty_iban}` : ""}.`,
  "income.pre_handover": (p) =>
    `${p.count ?? "Some"} ${p.count === "1" ? "payment" : "payments"} received before the handover (${money(p)}): how they count is not decided yet.`,
  "income.living_area_missing": () =>
    "The property's living area is missing; Zeile 13 takes the sum of the units' areas.",
  "umlagen.mixed_terms": () =>
    "Tenancies with service charges included and with prepayments are mixed: the cross in Zeile 24 stays empty.",
  "reduced_rent.no_market_rent": () =>
    "Enter the local comparable rent to check reduced-rent letting (the 66% limit).",
  "reduced_rent.prognosis_needed": () =>
    "The rent is between 50% and 66% of the local comparable rent: the Finanzamt may ask for a surplus forecast.",
  "reduced_rent.partial": (p) =>
    `The rent is below 50% of the local comparable rent: the Werbungskosten are only partly deductible${p.cut_percent ? ` (cut by ${p.cut_percent}%)` : ""}.`,
  "draft.open_lines_excluded": (p) =>
    `${p.count ?? "Some"} ${p.count === "1" ? "line" : "lines"} in To review (${money(p, "sum")}) ${p.count === "1" ? "is" : "are"} not yet in the draft.`,
  "draft.form_provisional": () =>
    "This year's form is not published yet; Zeilen and Kennzahlen are provisional.",
  "draft.allocation_without_property": (p) =>
    `A payment from the account${p.amount ? ` of ${money(p)}` : ""} is not assigned to a property.`,
  "archive.finanzamt_copy_needed": () =>
    "A document does not open in every program (Adobe form or annotations): print a copy for the Finanzamt.",
  "tax.travel_frequency": () =>
    "More than 50 trips to the property in the year: with almost daily presence only half the flat rate may apply. Please check.",
  "tax.private_cost_paid_from_account": (p) =>
    `${p.label ? `“${p.label}” is` : "An invoice is"} entered as paid from another account but was paid from the rental account: it counts only once, with the booking from the rental account. Please withdraw the entry.`,
  "payment_proof.missing": (p) =>
    p.proof_rest
      ? `Proof of payment missing: ${proofBill(p)} of ${money(p)} counts. Part of it is proven; the proof of ${money(p, "proof_rest")} is still missing.`
      : `Proof of payment missing: ${proofBill(p)} of ${money(p)} counts, but nothing shows it was paid${p.proof_amount ? ` (the uploaded proof is for ${money(p, "proof_amount")}, the amount does not match)` : ""}.`,
  "anlage_n.certificate_missing": () =>
    "No Lohnsteuerbescheinigung for this year: the figures rest on payslips or the estimate.",
  "anlage_n.church_mismatch": () => "The certificate's church tax markers do not match the owner's details.",
  "anlage_n.tax_class_6": () =>
    "Tax class 6: a second employment. It is in its own column for tax class 6.",
  "anlage_n.periods_overlap": () => "Two certificates from the same employer overlap in their period.",
  "anlage_n.days_implausible": () =>
    "The number of working days does not fit the days per week and the period.",
  "anlage_n.home_office_capped": () => "The daily flat rate is capped at the yearly maximum.",
  "anlage_n.too_many_routes": () =>
    "More commutes than the form has lines: please enter the rest by hand.",
  "anlage_n.receipt_missing": () => "No receipt is stored for an expense.",
  "anlage_n.form_provisional": () =>
    "This year's Anlage N is not published yet; Zeilen and Kennzahlen are provisional.",
  "anlage_n.payslip_gap": (p) => payslipGapText(p),
  "anlage_n.payslip_no_ytd": () =>
    "The payslip has no year-to-date figures: the projection uses the monthly figure only.",
  "anlage_n.work_room_records": () => "Please keep the floor plan and the costs of the home study.",
  "anlage_n.work_room_centre": () =>
    "A home study counts only if it is the centre of all work. Please check.",
  "anlage_n.work_room_partial": (p) =>
    `The home study counts for ${p.months ?? "some"} months: enter the daily flat rate only for days in the other months.`,
  "anlage_n.first_workplace_check": () =>
    "Please check whether this is the first place of work (as set by the employer).",
  "anlage_n.no_pay_overruled": () =>
    "“No pay” is stated for this year, but a Lohnsteuerbescheinigung or payslip exists: it counts. Please withdraw the statement.",
  "return.progression_provisional": () =>
    "Wage-replacement benefits are not certified yet: the tax rate is provisional.",
  "return.benefit_possible_double": () =>
    "A wage-replacement benefit is on the certificate and entered separately: please enter it only once.",
  "return.benefit_booked": (p) =>
    `Wage-replacement benefits ${p.tax_year ?? ""}: ${money(p, "difference")} booked on the account, not yet entered as a wage-replacement benefit.`,
  "return.children_mismatch": () =>
    "The certificate's number of child allowances does not match the children entered.",
  "return.kindergeld_apply": () =>
    "No child benefit (Kindergeld) is entered for a child yet: please apply at the Familienkasse.",
  "return.child_over_18": () => "A child is of age: they count only while in training or studying.",
  "return.childcare_until_14": () =>
    "A child turns 14 this year: only childcare costs up to the 14th birthday count.",
  "return.donation_over_cap": () => "The donations exceed the cap; the rest does not count this year.",
  "return.filing_required": () => "A tax return must be filed for this year.",
  "return.questionnaire_open": () => "Questions about the tax year are still open.",
  "return.not_modelled": () => "Part of the return is not computed by the app; the estimate is incomplete.",
  questionnaire_provisional: () =>
    "This year's questions are not set yet; last year's apply.",
  "tax_export.questions_open": (p) =>
    `${p.count ?? "Some"} yearly ${p.count === "1" ? "question" : "questions"} open: not answered at export, the details may still change.`,
};

export const WARNING_TEXT: Record<WarningCode, (params: Params) => string> = localized(WARNING_TEXT_DE, WARNING_TEXT_EN);

/** What a line's source is, in the sources drawer. */
const TAX_SOURCE_KIND_DE: Record<TaxSourceKind, string> = {
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
const TAX_SOURCE_KIND_EN: Record<TaxSourceKind, string> = {
  allocation: "Payment",
  transaction: "Bank line",
  document: "Document",
  property: "Purchase price of the property",
  price_split: "Purchase price split",
  acquisition_cost: "Acquisition costs",
  loan: "Loan",
  tax_input: "Tax detail",
  rule: "Rules of the tax year",
  scenario: "Projection scenario",
  schedule_entry: "Repayment schedule",
  income_input: "Income detail",
  tariff: "Income tax tariff",
  return_input: "Tax return detail",
  questionnaire_answer: "Answer to a yearly question",
  anlage_n_rule_set: "Anlage N rules",
};
export const TAX_SOURCE_KIND: Record<TaxSourceKind, string> = localized(TAX_SOURCE_KIND_DE, TAX_SOURCE_KIND_EN);

/** A line note of the draft (the §5.1 notes); an unknown note is shown as sent. */
const TAX_NOTE: Record<string, string> = {
  due_date_missing: "Fälligkeit unbekannt: zählt im Jahr der Zahlung",
  year_end_straddle: "Wertstellung im anderen Jahr",
};
const TAX_NOTE_EN: Record<string, string> = {
  due_date_missing: "Due date unknown: counts in the year paid",
  year_end_straddle: "Value date in the other year",
};

export function taxNote(note: string): string {
  return (english() ? TAX_NOTE_EN : TAX_NOTE)[note] ?? note;
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
const EQUIPMENT_CLASS_DE: Record<EquipmentClass, string> = {
  computer: "Computer, Laptop, Tablet",
  software: "Software",
  phone: "Smartphone, Telefon",
  furniture: "Büromöbel",
  tools: "Werkzeug",
  subscription: "Abo (z. B. Software, Fachzeitschrift)",
  other: "Sonstiges",
};
const EQUIPMENT_CLASS_EN: Record<EquipmentClass, string> = {
  computer: "Computer, laptop, tablet",
  software: "Software",
  phone: "Smartphone, phone",
  furniture: "Office furniture",
  tools: "Tools",
  subscription: "Subscription (e.g. software, trade journal)",
  other: "Other",
};
export const EQUIPMENT_CLASS: Record<EquipmentClass, string> = localized(EQUIPMENT_CLASS_DE, EQUIPMENT_CLASS_EN);

/** A wage-replacement benefit paid by an authority (Progressionsvorbehalt, D102). */
const REPLACEMENT_BENEFIT_DE: Record<ReplacementBenefit, string> = {
  elterngeld: "Elterngeld",
  mutterschaftsgeld: "Mutterschaftsgeld der Krankenkasse",
  krankengeld: "Krankengeld",
  arbeitslosengeld: "Arbeitslosengeld",
  kurzarbeitergeld: "Kurzarbeitergeld",
  other: "Andere Lohnersatzleistung",
};
const REPLACEMENT_BENEFIT_EN: Record<ReplacementBenefit, string> = {
  elterngeld: "Elterngeld (parental allowance)",
  mutterschaftsgeld: "Maternity pay from the health insurer",
  krankengeld: "Sickness benefit",
  arbeitslosengeld: "Unemployment benefit",
  kurzarbeitergeld: "Short-time work allowance",
  other: "Other wage-replacement benefit",
};
export const REPLACEMENT_BENEFIT: Record<ReplacementBenefit, string> = localized(REPLACEMENT_BENEFIT_DE, REPLACEMENT_BENEFIT_EN);

/** The Anlage N draft's status: where the pay comes from. */
const ANLAGE_N_STATUS_DE: Record<AnlageNStatus, string> = {
  certified: "bescheinigt",
  estimate: "Schätzung",
  none: "kein Arbeitslohn",
};
const ANLAGE_N_STATUS_EN: Record<AnlageNStatus, string> = {
  certified: "certified",
  estimate: "Estimate",
  none: "no pay",
};
export const ANLAGE_N_STATUS: Record<AnlageNStatus, string> = localized(ANLAGE_N_STATUS_DE, ANLAGE_N_STATUS_EN);

/** The whole-return estimate's status (D101). */
const RETURN_STATUS_DE: Record<ReturnStatus, string> = {
  estimate: "Schätzung",
  certified: "laut Bescheinigungen",
};
const RETURN_STATUS_EN: Record<ReturnStatus, string> = {
  estimate: "Estimate",
  certified: "per certificates",
};
export const RETURN_STATUS: Record<ReturnStatus, string> = localized(RETURN_STATUS_DE, RETURN_STATUS_EN);

/** What the estimate leaves out (backend services/return_inputs.py NOT_MODELLED, D95). */
const NOT_MODELLED: Record<string, string> = {
  extraordinary_burden: "außergewöhnliche Belastungen",
  riester: "Riester",
  maintenance: "Unterhalt",
  prepayments: "Vorauszahlungen",
  capital_income: "Kapitalerträge (ab Phase 8)",
};
const NOT_MODELLED_EN: Record<string, string> = {
  extraordinary_burden: "extraordinary expenses",
  riester: "Riester",
  maintenance: "maintenance payments",
  prepayments: "tax prepayments",
  capital_income: "capital income (from phase 8)",
};

/** anlage_n.payslip_gap with its months: `month` is the payslip whose year-to-date sum does not
 * follow from the one before (`previous`); `missing` lists the months between the two, if any. */
function payslipGapText(p: Params): string {
  if (english()) return payslipGapTextEn(p);
  if (!p.month) return "Zwischen den Gehaltsabrechnungen fehlt ein Monat.";
  const employer = p.employer ? ` (${p.employer})` : "";
  const missing = (p.missing ?? "").split(",").filter((m) => m !== "");
  if (missing.length > 0) {
    const names = missing.map(formatMonth).join(", ");
    return `Gehaltsabrechnung ${names}${employer} ${missing.length === 1 ? "fehlt" : "fehlen"}: die Jahressumme im ${formatMonth(p.month)} passt nicht zur Summe der Abrechnung davor.`;
  }
  return `Gehaltsabrechnung ${formatMonth(p.month)}${employer} passt nicht zur Vormonatssumme: bitte die Jahreswerte prüfen.`;
}

function payslipGapTextEn(p: Params): string {
  if (!p.month) return "A month is missing between the payslips.";
  const employer = p.employer ? ` (${p.employer})` : "";
  const missing = (p.missing ?? "").split(",").filter((m) => m !== "");
  if (missing.length > 0) {
    const names = missing.map(formatMonth).join(", ");
    return `Payslip ${names}${employer} ${missing.length === 1 ? "is" : "are"} missing: the year-to-date sum in ${formatMonth(p.month)} does not follow from the payslip before.`;
  }
  return `Payslip ${formatMonth(p.month)}${employer} does not follow from the previous month's sum: please check the year-to-date figures.`;
}

/** An unknown code is shown as sent. */
export function notModelledLabel(code: string): string {
  return (english() ? NOT_MODELLED_EN : NOT_MODELLED)[code] ?? code;
}

/** The Grundsteuerbescheid's payment rhythm (phase-5.md D126). */
const GRUNDSTEUER_RHYTHM_DE: Record<GrundsteuerRhythm, string> = {
  quarterly: "vierteljährlich",
  half_yearly: "halbjährlich",
  yearly_august: "jährlich zum 15. August",
  yearly_july: "jährlich zum 1. Juli",
};
const GRUNDSTEUER_RHYTHM_EN: Record<GrundsteuerRhythm, string> = {
  quarterly: "quarterly",
  half_yearly: "half-yearly",
  yearly_august: "yearly on 15 August",
  yearly_july: "yearly on 1 July",
};
export const GRUNDSTEUER_RHYTHM: Record<GrundsteuerRhythm, string> = localized(GRUNDSTEUER_RHYTHM_DE, GRUNDSTEUER_RHYTHM_EN);

/** The Grundsteuer reminder's mode (D126): the row's choice, or "automatisch" (inferred from a
 * direct-debit line, never from a transfer). */
const GRUNDSTEUER_MODE_DE: Record<GrundsteuerMode | "auto", string> = {
  remind: "Erinnern",
  sepa_mandate: "Lastschrift prüfen",
  auto: "automatisch",
};
const GRUNDSTEUER_MODE_EN: Record<GrundsteuerMode | "auto", string> = {
  remind: "Remind",
  sepa_mandate: "Check direct debit",
  auto: "automatic",
};
export const GRUNDSTEUER_MODE: Record<GrundsteuerMode | "auto", string> = localized(GRUNDSTEUER_MODE_DE, GRUNDSTEUER_MODE_EN);
