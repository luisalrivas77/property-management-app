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

AI_ERROR_CODES = frozenset(
    {
        AI_DISABLED,
        AI_BLOCKED_TENANT_DATA,
        AI_UNPARSABLE,
        AI_REFUSED,
        AI_TOO_MANY_PAGES,
        AI_UNSUPPORTED_MEDIA_TYPE,
    }
)
