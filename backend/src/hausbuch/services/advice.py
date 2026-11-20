"""The household plan's explanation (docs/phases/phase-9.md §8.6, D252–D257; phase 9 step 4).

- `request(ctx, scenario_id)`, „Neu erklären“ (POST /household-plan/explanation): queues one
  explain_plan job for the scenario, else the default in force, in the caller's transaction.
  While ai_private_accounts is off (D253) nothing is queued and no request is built: the
  screen shows the template text, rendered on read (`explanation`), with reason
  `consent_off`. No row is written for it, so switching consent on later leaves no stale
  template row behind.
- `explanation(conn, today, scenario_id)` (GET): the newest stored row of the workspace's
  current advice mode for the scenario, rendered from its own stored facts and labels
  (D257: a dated snapshot), with `stale` when today's facts differ from them; with none
  stored, the template text rendered on read from today's facts. A stored model text is run
  through the current validator on every read, against its stored facts and mode: one it no
  longer passes is shown as the template of those stored facts, with the row's id and reason
  `rejected`. A scenario named must be in force (404 otherwise, for the GET and the POST).
- `explanation_facts(conn, today, scenario_id)`: the facts of one request, built by
  rules.advice.facts.build from the household's key figures, its plan and the "already
  held" sets of D254 (every account; every asset with a non-zero confirmed position on the
  day in a depot, exchange or wallet account, spam excluded; D245's plans). The labels
  travel beside the facts and are never serialised into a request.
- `store_explanation(ctx, ...)`: the one plan_explanation row of a run and its event
  plan.explanation_stored (status, reason, mode and violation codes; no amount, no label).

A scenario is resolved to its id before anything is stored or looked up: none named means the
default scenario in force (its id), or NULL when the household has none. A default that is
superseded later gets a new id, so its older explanations are no longer shown under it.

`stale`: the facts carry `as_of`, the day they were read, and `facts_sha256` (stored, the
request's hash) includes it, so two days' facts always hash differently. `stale` is therefore
derived, not stored: today's facts are hashed under the stored row's `as_of` and compared with
the stored hash, which amounts to comparing hashes of the facts without their day. Stale means
a figure, a state, a key or the mode differs, never only the date.

The facts object is never logged nor put into an exception message or an event: its repr
holds the labels (`exclude=True` only keeps them out of dumps).
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, func, insert, select

from hausbuch import i18n
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import plan_explanation as explanation_table
from hausbuch.jobs.enqueue import enqueue_explain_plan
from hausbuch.rules.advice import facts as F
from hausbuch.rules.advice import render as R
from hausbuch.rules.advice import template as T
from hausbuch.rules.advice.validator import validate, versions
from hausbuch.schemas.advice import (
    AdviceMode,
    ExplainFacts,
    ExplainLabels,
    ExplanationReason,
    ExplanationStatus,
    Paragraph,
    PlanExplanation,
    PlanExplanationQueued,
    RenderedParagraph,
    Violation,
)
from hausbuch.schemas.enums import AccountKind, EntityType
from hausbuch.schemas.events import EventType, PlanExplanationStored
from hausbuch.schemas.household import AssetKind, MovementStatus, NetWorthClass
from hausbuch.services import advice_texts

from .allocations import user_id
from .context import CommandContext
from .errors import NotFoundError
from .events import append_event
from .health import health
from .household_scenarios import default_scenario, is_in_force
from .plan import advice_mode, household_plan
from .workspace_settings import private_ai_allowed

# D254: the accounts whose positions make an asset "held".
_ASSET_ACCOUNT_KINDS = (AccountKind.DEPOT, AccountKind.EXCHANGE, AccountKind.WALLET)
_ASSET_CLASS = {
    AssetKind.ETF: NetWorthClass.DEPOT,
    AssetKind.STOCK: NetWorthClass.DEPOT,
    AssetKind.CRYPTO: NetWorthClass.CRYPTO,
}  # cash (an exchange's euros) is money, not an instrument: no {a:} slot names it


def resolve_scenario(conn: Connection, scenario_id: UUID | None) -> UUID | None:
    """The scenario an explanation is stored and looked up under: the one named, which must be
    in force (404 for an unknown, superseded or withdrawn one: its explanation would describe
    levers no longer in force, and „Neu erklären“ queues nothing for it), else the default in
    force, else None (the defaults)."""
    if scenario_id is not None:
        if not is_in_force(conn, scenario_id):
            raise NotFoundError(EntityType.HOUSEHOLD_SCENARIO, scenario_id)
        return scenario_id
    row = default_scenario(conn)
    return None if row is None else row.id


# --- The facts --------------------------------------------------------------------------


def _accounts(conn: Connection) -> list[tuple[UUID, F.AccountIn]]:
    acc = account_table.c
    rows = conn.execute(select(acc.id, acc.label, acc.kind).order_by(acc.id))
    return [(r.id, F.AccountIn(label=r.label, kind=AccountKind(r.kind))) for r in rows]


def _assets(conn: Connection, on: date) -> list[F.AssetIn]:
    """D254: every asset with a non-zero confirmed position at the end of `on`, summed over
    the depot, exchange and wallet accounts; spam tokens are left out by assemble()."""
    m, a, acc = movement_table.c, asset_table.c, account_table.c
    end = datetime.combine(on + timedelta(days=1), time.min, UTC)
    rows = conn.execute(
        select(a.id, a.name, a.kind, a.spam, func.sum(m.quantity).label("units"))
        .select_from(
            movement_table.join(asset_table, a.id == m.asset_id).join(
                account_table, acc.id == m.account_id
            )
        )
        .where(
            acc.kind.in_([k.value for k in _ASSET_ACCOUNT_KINDS]),
            m.status == MovementStatus.CONFIRMED.value,
            m.superseded_by.is_(None),
            m.occurred_at < end,
        )
        .group_by(a.id, a.name, a.kind, a.spam)
        .order_by(a.id)
    ).all()
    out = []
    for r in rows:
        cls = _ASSET_CLASS.get(AssetKind(r.kind))
        units = Decimal(r.units or 0)
        if cls is not None and units != 0:
            out.append(F.AssetIn(name=r.name, asset_class=cls, units=abs(units), spam=r.spam))
    return out


def explanation_facts(conn: Connection, today: date, scenario_id: UUID | None) -> ExplainFacts:
    """The facts (with their labels) of the scenario's explanation on `today`, in the
    workspace's current advice mode. `scenario_id` as resolve_scenario returns it."""
    mode = advice_mode(conn)
    figures = health(conn, today, scenario_id)
    # The opaque keys k1, p1, a1 are given in input order (facts.assemble): accounts and
    # assets are read by id and plans sorted by their key, so a household's keys do not move
    # between two reads.
    plan = household_plan(conn, today, scenario_id)
    plan = plan.model_copy(update={"plans": sorted(plan.plans, key=lambda p: p.key)})
    accounts = _accounts(conn)
    by_id = {account_id: a.label for account_id, a in accounts}
    plan_labels = {
        p.key: by_id[p.destination_account_id]
        for p in plan.plans
        if p.destination_account_id is not None and p.destination_account_id in by_id
    }
    return F.build(
        figures,
        plan,
        mode,
        accounts=[a for _, a in accounts],
        assets=_assets(conn, today),
        plan_labels=plan_labels,
    )


# --- Requesting -------------------------------------------------------------------------


def request(ctx: CommandContext, scenario_id: UUID | None) -> PlanExplanationQueued:
    """„Neu erklären“: queue explain_plan, unless ai_private_accounts is off (D253)."""
    resolved = resolve_scenario(ctx.conn, scenario_id)
    if not private_ai_allowed(ctx.conn):
        return PlanExplanationQueued(scenario_id=resolved, queued=False)
    enqueue_explain_plan(
        ctx.conn,
        workspace_id=ctx.workspace_id,
        scenario_id=resolved,
        requested_by=user_id(ctx),
    )
    return PlanExplanationQueued(scenario_id=resolved, queued=True)


def queue_daily(ctx: CommandContext) -> bool:
    """health_daily's half of §11.5: one explain_plan for the default scenario after a run
    stored a snapshot, only while ai_private_accounts is on. Returns whether it queued."""
    if not private_ai_allowed(ctx.conn):
        return False
    enqueue_explain_plan(
        ctx.conn,
        workspace_id=ctx.workspace_id,
        scenario_id=resolve_scenario(ctx.conn, None),
        requested_by=None,
    )
    return True


# --- Storing ----------------------------------------------------------------------------


def store_explanation(
    ctx: CommandContext,
    *,
    scenario_id: UUID | None,
    facts: ExplainFacts,
    status: ExplanationStatus,
    reason: ExplanationReason | None,
    paragraphs: Sequence[Paragraph],
    violations: Sequence[tuple[int, Violation]] = (),
    ai_call_ids: Sequence[UUID] = (),
    prompt_version: str | None = None,
    created_by: UUID | None = None,
) -> UUID:
    """The one plan_explanation row of a run (insert-only) and its event. `paragraphs` are
    the validated slotted text, or the template's; `violations` the (attempt, violation)
    pairs of the rejected attempts: codes and positions, never their text."""
    v = versions()
    row_id = ctx.conn.execute(
        insert(explanation_table)
        .values(
            workspace_id=ctx.workspace_id,
            scenario_id=scenario_id,
            advice_mode=facts.mode.value,
            status=status.value,
            reason=None if reason is None else reason.value,
            facts=facts.model_dump(mode="json"),
            labels=facts.labels.model_dump(mode="json"),
            facts_sha256=bytes.fromhex(F.facts_sha256(facts)),
            paragraphs=[p.model_dump(mode="json") for p in paragraphs],
            violations=[
                {"attempt": attempt, **violation.model_dump(mode="json")}
                for attempt, violation in violations
            ],
            ai_call_ids=list(ai_call_ids),
            prompt_version=prompt_version,
            validator_version=v["validator"],
            lexicon_version=v["lexicon"],
            vocabulary_version=v["vocabulary"],
            created_by=created_by,
            created_at=ctx.now,
        )
        .returning(explanation_table.c.id)
    ).scalar_one()
    codes = list(dict.fromkeys(violation.code for _, violation in violations))
    append_event(
        ctx,
        EventType.PLAN_EXPLANATION_STORED,
        row_id,
        PlanExplanationStored(status=status, reason=reason, mode=facts.mode, violation_codes=codes),
    )
    return row_id


# --- Showing ----------------------------------------------------------------------------


def _stored_facts(row: Any) -> ExplainFacts:
    facts = ExplainFacts.model_validate(row.facts)
    return facts.model_copy(update={"labels": ExplainLabels.model_validate(row.labels)})


def _rendered(paragraphs: Sequence[Paragraph], facts: ExplainFacts) -> list[RenderedParagraph]:
    texts = R.render(paragraphs, facts, facts.labels)
    return [
        RenderedParagraph(topic=p.topic, text=t) for p, t in zip(paragraphs, texts, strict=True)
    ]


def explanation(conn: Connection, today: date, scenario_id: UUID | None) -> PlanExplanation:
    """GET /household-plan/explanation?scenario= (§8.6)."""
    resolved = resolve_scenario(conn, scenario_id)
    mode = advice_mode(conn)
    e = explanation_table.c
    row = conn.execute(
        select(explanation_table)
        .where(e.advice_mode == mode.value, e.scenario_id.is_not_distinct_from(resolved))
        .order_by(e.created_at.desc(), e.id.desc())
        .limit(1)
    ).one_or_none()
    current = explanation_facts(conn, today, resolved)
    if row is None:
        consent = private_ai_allowed(conn)
        return PlanExplanation(
            scenario_id=resolved,
            mode=mode,
            status=ExplanationStatus.TEMPLATE,
            reason=None if consent else ExplanationReason.CONSENT_OFF,
            paragraphs=(
                advice_texts.template(current, mode)
                if i18n.english()
                else _rendered(T.render(current, mode), current)
            ),
        )
    stored = _stored_facts(row)
    stored_mode = AdviceMode(row.advice_mode)
    paragraphs = [Paragraph.model_validate(p) for p in row.paragraphs]
    status = ExplanationStatus(row.status)
    reason = None if row.reason is None else ExplanationReason(row.reason)
    if status is ExplanationStatus.MODEL and not validate(paragraphs, stored, stored_mode).ok:
        # A model text the current validator rejects (its lists tightened since it was
        # stored) is never shown: the template of the same stored facts instead (D257).
        status, reason = ExplanationStatus.TEMPLATE, ExplanationReason.REJECTED
        paragraphs = T.render(stored, stored_mode)
    same_day = current.model_copy(update={"as_of": stored.as_of})
    if i18n.english():
        # Phase 10 Q-O178: the validator and its evals are German, so in English the row is
        # shown as the app's template text of its own stored facts, in English; a model text
        # is never shown translated. Id, day and staleness stay the row's, nothing is stored.
        if status is ExplanationStatus.MODEL:
            status, reason = ExplanationStatus.TEMPLATE, None
        return PlanExplanation(
            id=row.id,
            scenario_id=row.scenario_id,
            mode=stored_mode,
            status=status,
            reason=reason,
            paragraphs=advice_texts.template(stored, stored_mode),
            created_at=row.created_at,
            stale=F.facts_sha256(same_day) != bytes(row.facts_sha256).hex(),
        )
    return PlanExplanation(
        id=row.id,
        scenario_id=row.scenario_id,
        mode=stored_mode,
        status=status,
        reason=reason,
        paragraphs=_rendered(paragraphs, stored),
        created_at=row.created_at,
        stale=F.facts_sha256(same_day) != bytes(row.facts_sha256).hex(),
    )
