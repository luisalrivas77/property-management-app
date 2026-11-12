"""The one exception type rules raise. Its code is a stable API contract."""


class RuleError(Exception):
    """A rule could not produce a result for valid-looking input.

    code: stable error code, e.g. "iban.bad_checksum" (docs/phases/phase-1.md).
    at_number: for loan schedule errors, the 1-based payment number where it fails.
    """

    def __init__(self, code: str, message: str | None = None, *, at_number: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.at_number = at_number


# --- Phase 2 codes (docs/phases/phase-2.md §12) ---------------------------------------
# Stable strings: the API returns them as `code`, proposal.error_code stores the ai.* ones,
# and the frontend maps each to its German text.

PROPOSAL_ALREADY_DECIDED = "proposal.already_decided"  # applied or rejected before
PROPOSAL_NOT_READY = "proposal.not_ready"  # queued, running, failed, blocked or superseded

AI_DISABLED = "ai.disabled"  # HAUSBUCH_AI_ENABLED is false (§5.5 step 1)
AI_BLOCKED_TENANT_DATA = "ai.blocked_tenant_data"  # the privacy gate (D16, §5.5 steps 2-3)
AI_UNPARSABLE = "ai.unparsable"  # no parsable line, twice (§5.3)
AI_REFUSED = "ai.refused"  # stop_reason refusal or max_tokens, twice (§5.3)
AI_TOO_MANY_PAGES = "ai.too_many_pages"  # past the API's page limit (§5.2)
AI_UNSUPPORTED_MEDIA_TYPE = "ai.unsupported_media_type"  # HEIC and others (D30)
# Used by the document_read job beyond §12:
AI_API_ERROR = "ai.api_error"  # the API answered with an error that retries did not clear
AI_UNAVAILABLE = "ai.unavailable"  # the API could not be reached (timeouts, overload)
AI_UNREADABLE = "ai.unreadable"  # the original could not be prepared for the model
AI_INTERNAL_ERROR = "ai.internal_error"  # a bug in the job; the dead letter names it
# D43: an Adobe-only (dynamic XFA) form; failed before any model call, never retried.
DOCUMENT_ADOBE_FORM = "document.adobe_form"

AI_ERROR_CODES = frozenset(
    {
        AI_DISABLED,
        AI_BLOCKED_TENANT_DATA,
        AI_UNPARSABLE,
        AI_REFUSED,
        AI_TOO_MANY_PAGES,
        AI_UNSUPPORTED_MEDIA_TYPE,
        AI_API_ERROR,
        AI_UNAVAILABLE,
        AI_UNREADABLE,
        AI_INTERNAL_ERROR,
        DOCUMENT_ADOBE_FORM,
    }
)


# --- Phase 3 codes (docs/phases/phase-3.md §10, §13) -----------------------------------

TAX_NO_RULE_SET = "tax.no_rule_set"  # no rulesets/<year>.yaml
TAX_YEAR_NOT_AVAILABLE = "tax.year_not_available"  # outside the checklist's available_years
TAX_DUE_DATE_UNRESOLVED = "tax.due_date_unresolved"  # a loan line without a schedule entry
FORM_MAPPING_INCOMPLETE = "form_mapping.incomplete"  # a category or draft line unmapped
TAX_INPUT_SCOPE_INVALID = "tax_input.scope_invalid"  # the kind's scope is not met
TAX_INPUT_NOT_CURRENT = "tax_input.not_current"  # supersedes a superseded or withdrawn row
TAX_INPUT_ALLOCATION_NOT_CONFIRMED = "tax_input.allocation_not_confirmed"
# D78: an Erweiterung is excluded from the 15 % test only on a renovation elected herstellung.
TAX_INPUT_EXTENSION_NEEDS_HERSTELLUNG = "tax_input.extension_needs_herstellung"
# D79: a useful_life must be shorter than the statutory life for the build year.
TAX_INPUT_USEFUL_LIFE_NOT_SHORTER = "tax_input.useful_life_not_shorter"
# D80: a movable asset's first year: not before the purchase year; the purchase year itself
# when it came with the flat (in_purchase_price).
TAX_INPUT_MOVABLE_ASSET_YEAR = "tax_input.movable_asset_year"
# D80: a movable asset bought with the flat must stay below the building's basis (its basis,
# with its Nebenkosten share, comes out of the building's); that needs a selected price split.
TAX_INPUT_MOVABLE_ASSET_EXCEEDS_BUILDING = "tax_input.movable_asset_exceeds_building"
TAX_INPUT_MOVABLE_ASSET_NO_SPLIT = "tax_input.movable_asset_no_split"
# D80 review: a kitchen bought separately needs its own acquisition date, which the
# value does not carry yet (its first year would count from the handover month).
TAX_INPUT_MOVABLE_ASSET_SEPARATE_UNSUPPORTED = "tax_input.movable_asset_separate_unsupported"
# D80 review: without the handover date there is no first year and no AfA start.
TAX_INPUT_MOVABLE_ASSET_NO_PURCHASE_DATE = "tax_input.movable_asset_no_purchase_date"
TAX_EXPORT_FAILED = "tax_export.failed"
TAX_EXPORT_TOO_LARGE = "tax_export.too_large"  # an archive over 100 MiB
# phase 4b D104: an Anlage N sheet is exported per owner, every other kind per property
TAX_EXPORT_KIND_INVALID = "tax_export.kind_invalid"


# --- Phase 4 codes (docs/phases/phase-4.md §8) -----------------------------------------

TARIFF_NO_FILE = "tariff.no_file"  # no tariffs/<year>.yaml for the year or any earlier year
TARIFF_INVALID = "tariff.invalid"  # a tariff file that does not validate (schemas/performance)
INCOME_MISSING = "income.missing"  # a flag on a year's figures, not an error (§5.2)
INCOME_INPUT_SCOPE_INVALID = "income_input.scope_invalid"  # the kind's scope is not met
INCOME_INPUT_NOT_CURRENT = "income_input.not_current"  # supersedes a superseded/withdrawn row
# GET /api/tax-rate: a Bescheid of an earlier year applies, and its own year's result (which
# the assessed zvE contains) needs the property to compute (phase 4 step 1).
INCOME_ASSESSED_RESULT_NEEDED = "income.assessed_result_needed"
SCENARIO_NOT_CURRENT = "scenario.not_current"  # supersedes a superseded or withdrawn scenario
SCENARIO_SALE_YEAR_BEFORE_TODAY = "scenario.sale_year_before_today"
# The body's property is not the path's, a loan lever names another property's loan, or a
# correction supersedes another property's scenario (step 2).
SCENARIO_SCOPE_INVALID = "scenario.scope_invalid"
PROJECTION_NO_RULE_SET = "projection.no_rule_set"  # no rule set file for the year or earlier
# D155 (7): a letter only for a flat_rate tenancy in force whose verdict today is a rise above
# 10 € a month or a fall; any other tenancy has nothing to tell.
FLAT_RATE_NO_LETTER = "flat_rate.no_letter"


# --- Phase 4b codes (docs/phases/phase-4b.md §9) ---------------------------------------

RETURN_INPUT_SCOPE_INVALID = "return_input.scope_invalid"  # the kind's scope is not met
RETURN_INPUT_NOT_CURRENT = "return_input.not_current"  # supersedes a superseded/withdrawn row
RETURN_INPUT_WORK_ROOM_CONFLICT = "return_input.work_room_conflict"  # D106, 12-month room
RETURN_INPUT_CARRIED_FROM_INVALID = "return_input.carried_from_invalid"  # D110
QUESTIONNAIRE_SCOPE_INVALID = "questionnaire.scope_invalid"
QUESTIONNAIRE_UNKNOWN_KEY = "questionnaire.unknown_key"  # not in the year's file
QUESTIONNAIRE_NOT_CURRENT = "questionnaire.not_current"
ANLAGE_N_RULE_SET_INVALID = "anlage_n.rule_set_invalid"  # an Anlage N file that does not load
ANLAGE_N_USEFUL_LIFE_MISSING = "anlage_n.useful_life_missing"  # an `other` item without life
ANLAGE_N_WORK_ROOM_EXCLUDES_DAY_RATE = "anlage_n.work_room_excludes_day_rate"  # D106
ANLAGE_N_NO_PAY = "anlage_n.no_pay"  # no certificate, payslip or expected pay
RETURN_CHILDCARE_AGE = "return.childcare_age"  # a child aged 14 or more (§10 Abs. 1 Nr. 5)
RETURN_HOUSEHOLD_SERVICES_CASH = "return.household_services_cash"  # §35a Abs. 5 S. 3


# --- Phase 5 codes (docs/phases/phase-5.md §4.4, §12) ----------------------------------

BANK_NOT_CONFIGURED = "bank.not_configured"  # no PIN or product id in the environment
BANK_TAN_NOT_DECOUPLED = "bank.tan_not_decoupled"  # chipTAN, smsTAN: not supported (D118)
BANK_APPROVAL_TIMEOUT = "bank.approval_timeout"  # no pushTAN approval in SYNC_APPROVAL_TIMEOUT_S
BANK_SYNC_RUNNING = "bank.sync_running"  # one sync per account at a time
BANK_RATE_LIMITED = "bank.rate_limited"  # more than 4 manual starts per account and hour
BANK_ACCOUNT_NOT_FOUND = "bank.account_not_found"  # the login does not list the account's IBAN
BANK_STATE_REJECTED = "bank.state_rejected"  # the stored client state's MAC does not verify
BANK_UNREACHABLE = "bank.unreachable"  # network or HTTP 5xx; the only retried failure
BANK_REFUSED = "bank.refused"  # a bank code starting 9; only the four digits are kept
BANK_NOTHING_NEW = "bank.nothing_new"  # an info code on the row, not a failure (D127)
BANK_BALANCE_DRIFT = "bank.balance_drift"  # info card: HKSAL differs from the last closing
BANK_UNSUPPORTED_FORMAT = "bank.unsupported_format"  # HICAZS lists no camt format we read
BANK_WINDOW_INVALID = "bank.window_invalid"  # the request window would start after it ends
BANK_URL_NOT_HTTPS = "bank.url_not_https"  # also raised by schemas/bank_sync.py
# Step 1: a sync that failed inside the app (neither the bank's refusal nor the network);
# the log line names the exception class, the row this code.
BANK_FAILED = "bank.failed"
EMAIL_ACCEPTED = "email.accepted"  # MailDecision.code of an accepted mail (step 0 choice)
EMAIL_SENDER_NOT_ALLOWED = "email.sender_not_allowed"
EMAIL_AUTH_FAILED = "email.auth_failed"  # Gmail's own header says neither dkim nor spf pass
EMAIL_AUTH_MISSING = "email.auth_missing"  # no Authentication-Results from the trusted authserv
EMAIL_RECIPIENT_MISMATCH = "email.recipient_mismatch"  # the token address is not a recipient
EMAIL_NO_ATTACHMENT = "email.no_attachment"
EMAIL_TOO_LARGE = "email.too_large"  # more than 20 attachments, or one above 25 MiB
EMAIL_NO_MESSAGE_ID = "email.no_message_id"
EMAIL_STATEMENT_UNMATCHED = "email.statement_unmatched"  # a statement for no or several accounts
EMAIL_FAILED = "email.failed"  # storage or processing failed; retried up to 3 attempts
EMAIL_AUTH_REJECTED = "email.auth_rejected"  # Gmail refused the app password (revoked)
EMAIL_LABEL_MISSING = "email.label_missing"  # the label folder does not exist
EMAIL_UNREACHABLE = "email.unreachable"  # the IMAP server not reached three polls in a row
REMINDER_UNKNOWN = "reminder.unknown"
REMINDER_SCOPE_INVALID = "reminder.scope_invalid"
NOTIFICATION_FAILED = "notification.failed"
TAX_INPUT_GRUNDSTEUER_SCHEDULE_INVALID = "tax_input.grundsteuer_schedule_invalid"  # D126


# --- Phase 6 codes (docs/phases/phase-6.md D156-D175, §12) -----------------------------
# Step 0 names them; the steps that build each rule raise them.

ACCOUNT_KIND_NOT_AVAILABLE = "account.kind_not_available"  # wallet until phase 7 (D156)
OWNERSHIP_SUM_NOT_ONE = "ownership.sum_not_one"  # an account's shares do not sum to 1 (D157)
OWNERSHIP_OWNER_MISSING = "ownership.owner_missing"  # a share names no owner of the workspace
ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT = "allocation.category_not_for_account"  # D159
ROUTING_AI_DISABLED = "routing.ai_disabled"  # a private line with ai_private_accounts off (D168)
RULE_CONFLICTS = "rule.conflicts"  # a name pattern hits a line confirmed otherwise (D160)
RULE_TOO_BROAD = "rule.too_broad"  # one name confirmed under two categories (D160, PayPal)
TRANSFER_AMBIGUOUS = "transfer.ambiguous"  # several candidate legs (D162)
TRANSFER_UNMATCHED_LEG = "transfer.unmatched_leg"  # no counterpart after the window (D162)
STATEMENT_ACCOUNT_MISMATCH = "statement.account_mismatch"  # the file names another IBAN (D164)
STATEMENT_IBAN_UNREAD = "statement.iban_unread"  # a PDF whose extraction has no IBAN (D164)
DEPOT_HOLDINGS_MISMATCH = "depot.holdings_mismatch"  # report units != Σ movements (D165)
EXCHANGE_UNRECONCILED = "exchange.unreconciled"  # typed balance != Σ movements (D166)
EXCHANGE_DEPOSIT_QUESTION = "exchange.deposit_question"  # a crypto Deposit: a bonus? (D166)
PRICE_NO_SOURCE = "price.no_source"  # an asset not in the registry (D167)
PRICE_SOURCE_FAILED = "price.source_failed"  # an issuer's NAV missing seven days (D174)
VALUATION_STALE_PRICE = "valuation.stale_price"  # the price is older than 7 days (D167)
VALUATION_NOT_VALUED = "valuation.not_valued"  # past the asset's last reconciled date
BANK_PIN_MISSING = "bank.pin_missing"  # the connection's slot has no secret (D163)
# Phase 6 step 3 (FinTS connections per login, DKB)
BANK_HISTORY_MISSING = "bank.history_missing"  # a private account without a reconciled import
BANK_AUTO_SYNC_WAITS = "bank.auto_sync_waits"  # DKB: two manual syncs without approval first
