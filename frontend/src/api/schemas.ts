// Named aliases for the API's schema types, so pages never spell out the generated nesting.
// Everything here comes from ./types, which `npm run gen:types` generates from openapi.json
// (the output of `hausbuch openapi`); nothing is typed by hand.
import type { components } from "./types";

type Schemas = components["schemas"];

// errors and auth
export type ErrorResponse = Schemas["ErrorResponse"];
export type ErrorKind = Schemas["ErrorKind"];
export type FieldError = Schemas["FieldError"];
export type Me = Schemas["Me"];
export type AppUser = Schemas["AppUser"];
export type Passkey = Schemas["Passkey"];
export type Invite = Schemas["Invite"];
export type Member = Schemas["Member"];
export type InviteCreate = Schemas["InviteCreate"];

// owners and properties
export type Owner = Schemas["Owner"];
export type OwnerCreate = Schemas["OwnerCreate"];
export type OwnerPatch = Schemas["OwnerPatch"];
export type Property = Schemas["Property"];
export type PropertyCreate = Schemas["PropertyCreate"];
export type PropertyPatch = Schemas["PropertyPatch"];
export type PropertyKind = Schemas["PropertyKind"];
export type Address = Schemas["Address"];
export type Fraction = Schemas["Fraction"];
export type OwnerShare = Schemas["OwnerShare"];
export type PropertyOwnership = Schemas["PropertyOwnership"];
export type AcquisitionCost = Schemas["AcquisitionCost"];
export type AcquisitionCostCreate = Schemas["AcquisitionCostCreate"];
export type AcquisitionCostPatch = Schemas["AcquisitionCostPatch"];
export type AcquisitionCostKind = Schemas["AcquisitionCostKind"];
export type PriceSplit = Schemas["PriceSplit"];
export type PriceSplitCreate = Schemas["PriceSplitCreate"];
export type PriceSplitSource = Schemas["PriceSplitSource"];

// units, tenants, tenancies
export type Unit = Schemas["Unit"];
export type UnitCreate = Schemas["UnitCreate"];
export type UnitPatch = Schemas["UnitPatch"];
export type Tenant = Schemas["Tenant"];
export type TenantCreate = Schemas["TenantCreate"];
export type TenantPatch = Schemas["TenantPatch"];
export type Tenancy = Schemas["Tenancy"];
export type TenancyCreateRequest = Schemas["TenancyCreateRequest"];
export type TenancyParty = Schemas["TenancyParty"];
export type TenancyRole = Schemas["TenancyRole"];
export type RentModel = Schemas["RentModel"];
export type DepositForm = Schemas["DepositForm"];
export type InitialRent = Schemas["InitialRent"];
export type RentScheduleEntry = Schemas["RentScheduleEntry"];
export type RentScheduleCreate = Schemas["RentScheduleCreate"];
export type RentRowCorrection = Schemas["RentRowCorrection"];
export type RentChangeReason = Schemas["RentChangeReason"];
export type NoticeBody = Schemas["NoticeBody"];
export type TenancyPatch = Schemas["TenancyPatch"];
export type DepositReceipt = Schemas["DepositReceipt"];

// loans
export type Loan = Schemas["Loan"];
export type LoanCreateRequest = Schemas["LoanCreateRequest"];
export type LoanPatch = Schemas["LoanPatch"];
export type DisbursementCorrection = Schemas["DisbursementCorrection"];
export type LoanSchedule = Schemas["LoanSchedule"];
export type ScheduleEntry = Schemas["ScheduleEntry"];
export type YearTotals = Schemas["YearTotals"];

// documents
export type Document = Schemas["Document"];
export type DocumentDetail = Schemas["DocumentDetail"];
export type DocumentTyping = Schemas["DocumentTyping"];
export type DocumentType = Schemas["DocumentType"];
export type DocumentSubtype = Schemas["DocumentSubtype"];
export type DocumentLink = Schemas["DocumentLink"];
export type RelatedDocument = Schemas["RelatedDocument"];
export type LinkTargetRef = Schemas["LinkTargetRef"];
export type ManualDocumentFields = Schemas["ManualDocumentFields"];
export type HandFields = Schemas["HandFields"];
export type LinkTarget = Schemas["LinkTarget"];
export type RetentionResult = Schemas["RetentionResult"];
export type RetentionClass = Schemas["RetentionClass"];
export type RetentionHold = Schemas["RetentionHold"];
export type UploadBody = Schemas["Body_document_upload_api_documents_post"];
export type DocumentUploadResult = Schemas["DocumentUploadResult"];

// pre-fill proposals (phase-2.md §4.2, §6)
export type Proposal = Schemas["Proposal"];
export type ProposalRecord = Schemas["ProposalRecord"];
export type ProposalStatus = Schemas["ProposalStatus"];
export type ExtractionKind = Schemas["ExtractionKind"];
export type ExtractedValue = Schemas["ExtractedValue"];
export type SourceRef = Schemas["SourceRef"];
export type FieldDecision = Schemas["FieldDecision"];
export type ProposalApplyBody = Schemas["ProposalApplyBody"];
export type ProposalApplyRecord = Schemas["ProposalApplyRecord"];
export type ProposalApplyResult = Schemas["ProposalApplyResult"];
export type AppliedEntity = Schemas["AppliedEntity"];

// document checklist (spec amendment 3)
export type DocumentChecklist = Schemas["DocumentChecklist"];
export type DocumentChecklistItem = Schemas["DocumentChecklistItem"];
export type ChecklistDocumentRef = Schemas["ChecklistDocumentRef"];
export type ChecklistScope = Schemas["ChecklistScope"];
export type ChecklistScopeType = Schemas["ChecklistScopeType"];
export type ChecklistStatus = Schemas["ChecklistStatus"];
export type RequirementCode = Schemas["RequirementCode"];

// bank accounts and statement imports (phase 2 step 3)
export type Account = Schemas["Account"];
export type AccountCreate = Schemas["AccountCreate"];
export type AccountPatch = Schemas["AccountPatch"];
export type AccountSource = Schemas["AccountSource"];
export type StatementImport = Schemas["StatementImport"];
export type StatementFormat = Schemas["StatementFormat"];
export type ImportStatus = Schemas["ImportStatus"];
export type BalanceSource = Schemas["BalanceSource"];
export type DeclareBalanceBody = Schemas["DeclareBalanceBody"];
export type Transaction = Schemas["Transaction"];
export type PossibleDuplicate = Schemas["PossibleDuplicate"];
export type DuplicateStatus = Schemas["DuplicateStatus"];
export type DuplicateDecisionBody = Schemas["DuplicateDecisionBody"];
export type ImportBody = Schemas["Body_account_import_api_accounts__account_id__imports_post"];

// history and audit
export type TimelineEntry = Schemas["TimelineEntry"];
export type Event = Schemas["Event"];
export type EventType = Schemas["EventType"];
export type EntityType = Schemas["EntityType"];
export type JsonValue = Schemas["JsonValue"];
