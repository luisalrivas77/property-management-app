"""The review queue and a person's answer to a bank line (docs/phases/phase-2.md §8.1,
§8.3, step 4).

- GET /inbox: every item that needs the person, newest first (proposals, failed or blocked
  readings, imports that do not reconcile, possible duplicates, lines with a proposed set
  or none).
- POST /inbox/ai-retry: send every line listed as unmatched to the model again (D50). 422
  ai.disabled when HAUSBUCH_AI_ENABLED is off.
- GET /transactions/{id}/allocations: the line, its current allocations, and whether the
  answer may become a rule (`can_learn_rule`, false for a counterparty a matcher owns,
  D38) with the checkbox's default (`learn_rule_default`, off for categories that never
  auto-confirm, review F6).
- PUT /transactions/{id}/allocations: the person's answer. The body's method, confidence and
  provenance are ignored (the answer is the person's); `learn_rule` asks for a rule, null
  takes the default. 422 with the error code: allocation.sum_mismatch, allocation.empty,
  allocation.transaction_mismatch, allocation.import_not_reconciled,
  allocation.line_not_open, allocation.scope_mismatch, rule.not_learnable,
  rule.invalid_pattern.
"""

from datetime import UTC, datetime
from uuid import UUID

from hausbuch.schemas.api import (
    AiRetryResult,
    ConfirmAllocationsBody,
    InboxItem,
    TransactionAllocations,
)
from hausbuch.schemas.enums import EntityType
from hausbuch.services import review
from hausbuch.services.documents import LOCAL_TIME_ZONE

from ..deps import Cmd, SettingsDep, domain_router, found

router = domain_router()


@router.get("/inbox")
def inbox_list(cmd: Cmd) -> list[InboxItem]:
    """Plus, from 1 January, last year's open Jahresfragen (phase 4b D110)."""
    return cmd.read(review.inbox, today=datetime.now(LOCAL_TIME_ZONE).date())


@router.post("/inbox/ai-retry")
def inbox_ai_retry(cmd: Cmd, settings: SettingsDep) -> AiRetryResult:
    """Queue the model for every unmatched line; the suggestions arrive as the jobs run."""
    return cmd.run(review.request_ai_suggestions, ai_enabled=settings.ai_enabled)


@router.get("/transactions/{transaction_id}/allocations")
def transaction_allocations_get(
    transaction_id: UUID, cmd: Cmd, settings: SettingsDep
) -> TransactionAllocations:
    view = cmd.read(
        review.transaction_allocations,
        transaction_id,
        iban_key=settings.iban_hmac_key,
        today=datetime.now(UTC).date(),
    )
    return found(view, EntityType.TRANSACTION, transaction_id)


@router.put("/transactions/{transaction_id}/allocations")
def transaction_allocations_put(
    transaction_id: UUID, body: ConfirmAllocationsBody, cmd: Cmd, settings: SettingsDep
) -> TransactionAllocations:
    return cmd.run(
        review.allocate_transaction, transaction_id, body, iban_key=settings.iban_hmac_key
    )
