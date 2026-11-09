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
TAX_EXPORT_FAILED = "tax_export.failed"
TAX_EXPORT_TOO_LARGE = "tax_export.too_large"  # an archive over 1 GiB
