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
