"""The review queue and a person's answer to a bank line (docs/phases/phase-2.md §8.1,
§8.3, step 4).

- GET /inbox: every item that needs the person, newest first (proposals, failed or blocked
  readings, imports that do not reconcile, possible duplicates, lines with a proposed set
  or none).
- POST /inbox/ai-retry: send every line listed as unmatched to the model again (D50). 422
  ai.disabled when HAUSBUCH_AI_ENABLED is off.
- POST /inbox/confirm-category: „Alle bestätigen“ (phase 6 D159 amended 13 Nov 2026), the
  listed private lines' proposals of one plain household category confirmed as single
  confirmations are; the count and the lines skipped because they changed. 422
  inbox.category_not_bulk, inbox.line_not_private; 404 for a line of another workspace.
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
    ConfirmCategoryBody,
    ConfirmCategoryResult,
    InboxItem,
    InboxLines,
    TransactionAllocations,
)
from hausbuch.schemas.enums import EntityType
from hausbuch.services import app_secrets, review
from hausbuch.services.context import CommandContext
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.settings import Settings

from ..deps import Cmd, SettingsDep, domain_router, found

router = domain_router()


@router.get("/inbox")
def inbox_list(cmd: Cmd, settings: SettingsDep) -> list[InboxItem]:
    """Plus, from 1 January, last year's open Jahresfragen (phase 4b D110), and the email-in
    cards (phase 5 §5.4), and the wallet cards (phase 7; `settings` sees a missing key)."""
    now = datetime.now(LOCAL_TIME_ZONE)
    return cmd.read(review.inbox, today=now.date(), now=now, settings=settings)


@router.get("/inbox/lines")
def inbox_lines_get(cmd: Cmd, settings: SettingsDep) -> InboxLines:
    """Phase 10 §7.3 (5): the bank lines and statement imports the cards of GET /inbox look up
    (each item's line and import, both lines of every possible duplicate and their imports),
    serialised as /accounts/{id}/transactions and /imports serialise them, so Zu prüfen need
    not load every account's lines."""
    now = datetime.now(LOCAL_TIME_ZONE)
    return cmd.read(review.inbox_lines, today=now.date(), now=now, settings=settings)


@router.post("/inbox/ai-retry")
def inbox_ai_retry(cmd: Cmd, settings: SettingsDep) -> AiRetryResult:
    """Queue the model for every unmatched line; the suggestions arrive as the jobs run."""
    return cmd.run(_ai_retry, settings)


def _ai_retry(ctx: CommandContext, settings: Settings) -> AiRetryResult:
    """Refused unless the workspace has a key that reads (phase 6 D260: typed or the
    container's), since the jobs would otherwise end without asking."""
    ai = app_secrets.ai_available(ctx.conn, settings)
    return review.request_ai_suggestions(ctx, ai_enabled=ai)


@router.post("/inbox/confirm-category")
def inbox_confirm_category(body: ConfirmCategoryBody, cmd: Cmd) -> ConfirmCategoryResult:
    """„Alle bestätigen“: the listed private lines' proposals of one category, in one
    transaction; lines that changed meanwhile come back as skipped."""
    return cmd.run(review.confirm_category, body)


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
