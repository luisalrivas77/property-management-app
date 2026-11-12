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
export type WorkspaceSettings = Schemas["WorkspaceSettings"];
export type WorkspaceSettingsPatch = Schemas["WorkspaceSettingsPatch"];

// owners and properties
export type Owner = Schemas["Owner"];
export type OwnerCreate = Schemas["OwnerCreate"];
export type OwnerPatch = Schemas["OwnerPatch"];
export type StateCode = Schemas["StateCode"];
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
// Two backend models are named SourceRef (schemas/ai.py, schemas/tax.py), so the OpenAPI
// document qualifies both by module; TaxSourceRef below is the other one.
export type SourceRef = Schemas["hausbuch__schemas__ai__SourceRef"];
export type FieldDecision = Schemas["FieldDecision"];
export type ProposalApplyBody = Schemas["ProposalApplyBody"];
export type ProposalApplyRecord = Schemas["ProposalApplyRecord"];
export type ProposalApplyResult = Schemas["ProposalApplyResult"];
export type AppliedEntity = Schemas["AppliedEntity"];

// document checklist (spec amendment 3)
export type DocumentChecklist = Schemas["DocumentChecklist"];
export type DocumentChecklistItem = Schemas["DocumentChecklistItem"];
export type ChecklistDocumentRef = Schemas["ChecklistDocumentRef"];
export type ChecklistBankLine = Schemas["ChecklistBankLine"];
export type ChecklistDepositCandidate = Schemas["ChecklistDepositCandidate"];
export type ChecklistDepositOffer = Schemas["ChecklistDepositOffer"];
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

// the FinTS sync of an account (phase 5 step 1, §10)
export type BankConnection = Schemas["BankConnection"];
export type BankConnectionCreate = Schemas["BankConnectionCreate"];
export type BankConnectionPatch = Schemas["BankConnectionPatch"];
export type BankSync = Schemas["BankSync"];
export type BankStatement = Schemas["BankStatement"];
export type SyncStatus = Schemas["SyncStatus"];
export type SyncTrigger = Schemas["SyncTrigger"];
export type TransactionAllocationStatus = Schemas["TransactionAllocationStatus"];
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

// the tax year (phase 3): the Anlage V draft
export type AnlageVDraft = Schemas["AnlageVDraft"];
export type DraftLine = Schemas["DraftLine"];
export type DraftWarning = Schemas["DraftWarning"];
export type WarningCode = Schemas["WarningCode"];
export type TaxSourceRef = Schemas["hausbuch__schemas__tax__SourceRef"];
export type TaxSourceKind = TaxSourceRef["kind"];
export type UnitRow = Schemas["UnitRow"];
export type TrackedAmount = Schemas["TrackedAmount"];
export type MovedPayment = Schemas["MovedPayment"];
export type LimitStatus = Schemas["LimitStatus"];
export type AfaLine = Schemas["AfaLine"];
export type AllocationFact = Schemas["AllocationFact"];
export type TaxExport = Schemas["TaxExport"];
export type TaxExportKind = Schemas["TaxExportKind"];
export type TaxExportState = Schemas["TaxExportState"];

// household income and the tax effect (phase 4 step 1, phase-4.md §8)
export type IncomeInput = Schemas["IncomeInput"];
export type IncomeInputCreate = Schemas["IncomeInputCreate"];
export type IncomeInputKind = Schemas["IncomeInputKind"];
export type TaxRateResult = Schemas["TaxRateResult"];
export type TaxEffect = Schemas["TaxEffect"];
export type ZveEstimate = Schemas["ZveEstimate"];
export type OwnerZve = Schemas["OwnerZve"];

// performance: actuals, the projection to a sale and its scenarios (phase 4 step 2, §7, §8)
export type Actuals = Schemas["Actuals"];
export type Projection = Schemas["Projection"];
export type YearFigures = Schemas["YearFigures"];
export type YearKind = Schemas["YearKind"];
export type YearFlag = Schemas["YearFlag"];
export type SaleResult = Schemas["SaleResult"];
export type BridgeRow = Schemas["BridgeRow"];
export type Assumption = Schemas["Assumption"];
export type Levers = Schemas["Levers"];
export type Scenario = Schemas["Scenario"];
export type ScenarioCreate = Schemas["ScenarioCreate"];

// the Nebenkostenpauschale rise test (phase 4 step 2c, D155)
export type FlatRateCheck = Schemas["FlatRateCheck"];
export type TenancyFlatRate = Schemas["TenancyFlatRate"];
export type CostLevel = Schemas["CostLevel"];
export type CostRate = Schemas["CostRate"];
export type CategoryChange = Schemas["CategoryChange"];
export type SettlementHint = Schemas["SettlementHint"];
export type FlatRateVerdict = Schemas["FlatRateVerdict"];
export type RateBasis = Schemas["RateBasis"];
