// German labels for the API's enums. Typed as Record<Enum, string>, so a value added to the
// API (and to the generated types) fails the typecheck until it has a label here.
import type {
  AcquisitionCostKind,
  DepositForm,
  DocumentSubtype,
  DocumentType,
  EventType,
  LinkTarget,
  PriceSplitSource,
  PropertyKind,
  RentChangeReason,
  RentModel,
  RetentionClass,
  RetentionHold,
  TenancyRole,
} from "@/api/schemas";

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
};

/** The options of a <select> over an enum label map, in declaration order. */
export function options<K extends string>(labels: Record<K, string>): [K, string][] {
  return Object.entries(labels) as [K, string][];
}
