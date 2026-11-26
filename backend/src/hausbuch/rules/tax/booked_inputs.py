"""Bookings that propose a tax input (phase 10 §7.8 (a), D293; phase 6 D159 amended (8),
15 Nov 2026; phase 4b D102, D112). Pure, no I/O, Decimal throughout; nothing is written here.

The household's confirmed private lines in a code whose taxonomy row `proposes` a return input
(rules/household/taxonomy.py) are evidence of that input: Elterngeld & Lohnersatz the
Lohnersatzleistungen (`replacement_income`), donations `special_expense`, Kita und Tagesmutter
`childcare_cost`, Handwerker und Haushaltsdienste `household_services`. Per tax year (a line's
booking date, the year paid, §11 EStG) and code the rule sums the lines, compares them with the
current rows of the proposed kind and proposes only the difference. Principle 3: a proposal is
a suggestion the person confirms through `POST /return-inputs`; a row typed by hand covers the
bookings exactly as a confirmed proposal does, so nothing is counted twice.

Signs. A benefit is a credit, so its group sums the lines as they are; a donation, a childcare
or a §35a payment is a debit, so its group sums them negated. Either way a payment back has the
other sign and lowers its group, and a group is clamped at zero, so a payment back larger than
the payments never counts as typed.

**Lohnersatzleistungen** (`booked_benefit_warnings`, also the whole-return estimate's to-do
`return.benefit_booked`):

- Kurzarbeitergeld is never counted, on either side: the employer pays it with the pay and it is
  the wage certificate's Nr. 15, which already enters the Progressionsvorbehalt (D102), so a
  booked line whose text names it (or a typed `kurzarbeitergeld` row) would count it twice.
- The spouses are assessed jointly, so the gate is the household's: a proposal only while
  Σ booked > Σ typed over every owner of the year, whichever owner the lines or the rows are on.
  The proposals' differences sum to exactly that gap.
- The lines are grouped by owner: a line's owner is its account's only current owner; a line on
  an account with several owners (or none recorded) is in the one group without an owner. An
  owner's group falls short by max(0, booked − the owner's typed rows); the group without an
  owner by its booked sum.
- The gap is handed to the groups that fall short, owners first (in `owner_facts` order, then
  owners without facts), then the group without an owner, each capped by its shortfall. The
  owner is only a suggestion (the person may save it on the other spouse); the group without an
  owner asks the person to pick.
- The benefit kind is read by code from the lines' text: a kind only when every line of the
  group names exactly one kind and all name the same; otherwise none, and the person picks
  (Mutterschaftsgeld and Krankengeld both come from the Krankenkasse, so a line naming
  neither is never guessed). „Arbeitslosengeld II“ (also „-II“, „2“) is not a
  Lohnersatzleistung and reads as no kind.
- The warning's params are strings: `tax_year`, `booked`, `typed` (booked − difference, what
  counts as covered for the group), `difference` (cents) and, when known, `owner_id` and
  `benefit`; never the lines' text. Its refs are the group's allocations with their amounts and
  the typed rows that cover it (the owner's own, or every owner's when other rows count).

**Donations, childcare and §35a** (household kinds; one proposal per payee, the payee read by
rules/matching.payee from the counterparty name, so two spellings of one payee are one group):

- Donations (`special_expense`, expense class donation): the gate is the kind's, as for the
  benefits: Σ differences = max(0, Σ booked − Σ typed donation rows of the year). A typed row
  first covers the payee its `recipient_label` names (rules/matching.same_payee), what is left of
  it the other payees in order (first booking date, then payee). The proposal carries the payee
  as `recipient_label` and the difference as its amount; a payment above 300 € needs its
  Zuwendungsbestätigung (§50 Abs. 4 EStDV). A line without a payee name asks for the
  recipient. A donation is an ordinary §10b donation; a party donation (§34g) is not modelled
  and, since 26 Nov 2026, not asked about either (Luis: no feature for it).
- Childcare (`childcare_cost`): the same gate over the year's childcare rows. A row that names
  its payee (`payee_label`, which the proposal pre-sets) covers that payee first, as a donation
  row does; a row without one (typed by hand, or saved before the label) covers the payees in
  order. So a Kita lowered for meals keeps its rest on its own card and never leaves a
  remainder under the Tagesmutter taken in full. The child is pre-selected when exactly one
  current child was under `childcare_max_age` when the year began (the rule the write refuses
  otherwise, `return.childcare_age`), else the card picks from those children.
- §35a (`household_services`): only the labour share of an invoice counts (§35a Abs. 5 S. 2),
  and the bank line holds the whole bill, so no amount is ever taken from it: the card asks
  for the labour cost and the kind of service. The labour share cannot be compared with the
  bill, so each current row of the year paid to an account from an own invoice covers one
  payee: a row naming its payee (`payee_label`, pre-set by the proposal) that payee's card, so
  cards taken out of date order close the right one; a row naming a payee with no open card
  covers nothing; a row without a label the earliest payee still open (first booking date).
  A Nebenkostenabrechnung's row covers no payment of the household's own, labelled or not.

These three are not `blocking`: an amount the person lowers (meals at the Kita) or a labour
share below its bill would otherwise stay open for ever, since the rule reads only
return_input rows and nothing stores a dismissal. A benefit is blocking, as its
to-do was.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.household.taxonomy import TAXONOMY, household_code
from hausbuch.rules.matching import payee_key, same_payee
from hausbuch.rules.tax._common import warning
from hausbuch.rules.tax.anlage_n._base import ZERO, cents, current, in_year, row_sources
from hausbuch.schemas.booked_inputs import (
    BookedGroup,
    BookedLine,
    BookedNeed,
    BookedProposal,
    ChildChoice,
)
from hausbuch.schemas.employment import (
    BookedBenefit,
    ChildcareFact,
    ChildFact,
    HouseholdServicesFact,
    OwnerReturnFacts,
    ReturnInputFact,
    SpecialExpenseFact,
)
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.household import ProposedInput
from hausbuch.schemas.tax import DraftWarning, SourceRef, WarningCode

K = ReturnInputKind

# The group of each proposed input, and the return input kind it proposes.
GROUP_OF_INPUT: dict[ProposedInput, BookedGroup] = {
    ProposedInput.RETURN_REPLACEMENT_INCOME: "benefit",
    ProposedInput.RETURN_SPECIAL_EXPENSE: "donation",
    ProposedInput.RETURN_CHILDCARE_COST: "childcare",
    ProposedInput.RETURN_HOUSEHOLD_SERVICES: "household_services",
}
KIND_OF_GROUP: dict[BookedGroup, ReturnInputKind] = {
    "benefit": K.REPLACEMENT_INCOME,
    "donation": K.SPECIAL_EXPENSE,
    "childcare": K.CHILDCARE_COST,
    "household_services": K.HOUSEHOLD_SERVICES,
}
GROUP_ORDER: tuple[BookedGroup, ...] = ("benefit", "donation", "childcare", "household_services")
# The household codes whose lines propose a return input (services read only these).
PROPOSING_CODES = frozenset(
    code for code, row in TAXONOMY.items() if row.proposes in GROUP_OF_INPUT
)

# §50 Abs. 4 Satz 1 Nr. 2 EStDV: a donation up to this amount is proven by the bank line.
DONATION_RECEIPT_LIMIT = Decimal("300.00")
RECIPIENT_MAX = 120  # SpecialExpenseFact.recipient_label
CHILDCARE_MAX_AGE = 14  # §10 Abs. 1 Nr. 5 EStG, as the tariff's childcare.max_age defaults

# The benefit codes of ReplacementFact.benefit and the words that name them in a purpose text.
_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("elterngeld", re.compile(r"elterngeld")),
    ("mutterschaftsgeld", re.compile(r"mutterschaftsgeld")),
    ("krankengeld", re.compile(r"krankengeld")),
    ("arbeitslosengeld", re.compile(r"arbeitslosengeld(?!\s*-?\s*(?:ii|2)\b)")),
)

# Kurzarbeitergeld (also „Kurzarbeit“, „KUG“): on the wage certificate's Nr. 15, never counted here.
_SHORT_TIME = re.compile(r"kurzarbeit|\bkug\b")
_SHORT_TIME_BENEFIT = "kurzarbeitergeld"


def group_of(line: BookedBenefit) -> BookedGroup | None:
    """The group a line's code proposes (None: a code that proposes no return input). A
    BookedBenefit without a category is a benefit line."""
    category = getattr(line, "category", None)
    if category is None:
        return "benefit"
    proposes = household_code(category).proposes
    return GROUP_OF_INPUT.get(proposes) if proposes is not None else None


# --- Lohnersatzleistungen ---------------------------------------------------------------------


def is_short_time_allowance(text: str) -> bool:
    """Whether `text` names Kurzarbeitergeld, which the wage certificate already carries."""
    return _SHORT_TIME.search(text.lower()) is not None


def kind_of_text(text: str) -> str | None:
    """The one benefit kind `text` names, else None (none or several)."""
    lowered = text.lower()
    found = {code for code, pattern in _KINDS if pattern.search(lowered)}
    return found.pop() if len(found) == 1 else None


def benefit_kind(lines: Sequence[BookedBenefit]) -> str | None:
    """The kind every line names, else None (the person picks)."""
    kinds = {kind_of_text(line.text) for line in lines}
    if len(kinds) != 1:
        return None
    return kinds.pop()


def _typed(facts: OwnerReturnFacts, tax_year: int) -> list[ReturnInputFact]:
    return [
        r
        for r in in_year(facts.inputs, tax_year)
        if r.tax_year == tax_year
        and r.kind is ReturnInputKind.REPLACEMENT_INCOME
        and r.value.benefit != _SHORT_TIME_BENEFIT
    ]


def _refs(lines: Sequence[BookedBenefit], typed: Sequence[ReturnInputFact]) -> list[SourceRef]:
    refs = [SourceRef(kind="allocation", id=x.allocation_id, amount=x.amount) for x in lines]
    return refs + row_sources(typed)


@dataclass(frozen=True)
class _BenefitGroup:
    owner_id: UUID | None
    lines: list[BookedBenefit]
    rows: list[ReturnInputFact]  # the rows that cover it (its refs)
    booked: Decimal
    difference: Decimal


def _benefit_groups(
    tax_year: int,
    owner_facts: Sequence[OwnerReturnFacts],
    booked: Sequence[BookedBenefit],
) -> list[_BenefitGroup]:
    """The groups that fall short (the module docstring), their differences summing to the
    household's gap."""
    of_year = [
        x
        for x in booked
        if x.booking_date.year == tax_year
        and group_of(x) == "benefit"
        and not is_short_time_allowance(x.text)
    ]
    if not of_year:
        return []
    by_owner: dict[UUID, list[BookedBenefit]] = {}
    unowned: list[BookedBenefit] = []
    for line in of_year:
        if len(set(line.owner_ids)) == 1:
            by_owner.setdefault(line.owner_ids[0], []).append(line)
        else:
            unowned.append(line)

    typed_rows = {f.owner_id: _typed(f, tax_year) for f in owner_facts}
    all_rows = [r for rows in typed_rows.values() for r in rows]
    order = [f.owner_id for f in owner_facts]
    order += [o for o in by_owner if o not in typed_rows]

    # (owner, lines, own rows, booked, shortfall) per group, the group without an owner last.
    groups: list[tuple[UUID | None, list[BookedBenefit], list[ReturnInputFact], Decimal, Decimal]]
    groups = []
    for owner_id in order:
        lines = by_owner.get(owner_id, [])
        rows = typed_rows.get(owner_id, [])
        booked_sum = max(ZERO, sum((x.amount for x in lines), ZERO))
        typed_sum = sum((r.value.amount for r in rows), ZERO)
        groups.append((owner_id, lines, rows, booked_sum, max(ZERO, booked_sum - typed_sum)))
    if unowned:
        booked_sum = max(ZERO, sum((x.amount for x in unowned), ZERO))
        groups.append((None, unowned, [], booked_sum, booked_sum))

    booked_total = sum((g[3] for g in groups), ZERO)
    typed_total = sum((r.value.amount for r in all_rows), ZERO)
    gap = booked_total - typed_total
    out: list[_BenefitGroup] = []
    for owner_id, lines, rows, booked_sum, shortfall in groups:
        if gap <= ZERO:
            break
        difference = min(gap, shortfall)
        if difference <= ZERO:
            continue
        gap -= difference
        own_rows_cover = owner_id is not None and difference == shortfall
        out.append(
            _BenefitGroup(
                owner_id, lines, rows if own_rows_cover else all_rows, booked_sum, difference
            )
        )
    return out


def booked_benefit_warnings(
    tax_year: int,
    owner_facts: Sequence[OwnerReturnFacts],
    booked: Sequence[BookedBenefit],
) -> list[DraftWarning]:
    """The `return.benefit_booked` to-dos of `tax_year` (the module docstring): while the
    household's booked lines exceed its typed rows, one per group that falls short, owners
    first (in `owner_facts` order, then owners without facts), then the lines without an
    owner; their differences sum to the household's gap. The same groups as the benefit
    proposals of `booked_proposals`."""
    out: list[DraftWarning] = []
    for g in _benefit_groups(tax_year, owner_facts, booked):
        params = {
            "tax_year": str(tax_year),
            "booked": str(cents(g.booked)),
            "typed": str(cents(g.booked - g.difference)),
            "difference": str(cents(g.difference)),
        }
        if g.owner_id is not None:
            params["owner_id"] = str(g.owner_id)
        kind = benefit_kind(g.lines)
        if kind is not None:
            params["benefit"] = kind
        out.append(warning(WarningCode.RETURN_BENEFIT_BOOKED, _refs(g.lines, g.rows), **params))
    return out


def _benefit_proposal(tax_year: int, g: _BenefitGroup) -> BookedProposal:
    kind = benefit_kind(g.lines)
    needs: list[BookedNeed] = []
    if g.owner_id is None:
        needs.append("owner")
    if kind is None:
        needs.append("benefit")
    return BookedProposal(
        group="benefit",
        kind=K.REPLACEMENT_INCOME,
        tax_year=tax_year,
        owner_id=g.owner_id,
        booked=cents(g.booked),
        typed=cents(g.booked - g.difference),
        difference=cents(g.difference),
        payments=len(g.lines),
        allocation_ids=[x.allocation_id for x in g.lines],
        payload={
            "owner_id": None if g.owner_id is None else str(g.owner_id),
            "kind": K.REPLACEMENT_INCOME.value,
            "tax_year": tax_year,
            "value": {"benefit": kind, "amount": str(cents(g.difference))},
        },
        needs=needs,
        benefit=kind,
        blocking=True,
    )


# --- Donations, childcare and §35a: one group per payee ----------------------------------------


@dataclass
class _Payee:
    key: str  # rules.matching.payee_key: "" for a line without a payee
    label: str | None  # the first line's counterparty name, as printed
    purpose: str | None
    first: date
    lines: list[BookedLine] = field(default_factory=list)

    @property
    def booked(self) -> Decimal:
        """The year's payments, positive, a payment back subtracted; never below zero."""
        return max(ZERO, -sum((x.amount for x in self.lines), ZERO))


def _payees(lines: Sequence[BookedLine]) -> list[_Payee]:
    groups: dict[str, _Payee] = {}
    for line in sorted(lines, key=lambda x: (x.booking_date, str(x.allocation_id))):
        key = payee_key(line.payee, line.purpose)
        group = groups.get(key)
        if group is None:
            label = (line.payee or "").strip()[:RECIPIENT_MAX].strip() or None
            group = groups[key] = _Payee(
                key, label if key else None, line.purpose, line.booking_date
            )
        group.lines.append(line)
    return sorted((g for g in groups.values() if g.booked > ZERO), key=lambda g: (g.first, g.key))


def _covered_by_amount(
    groups: Sequence[_Payee], rows: Sequence[tuple[Decimal, str | None]]
) -> list[Decimal]:
    """What the typed rows (amount, label) cover of each group: a row first covers the payee
    its label names, what is left of it pools with the rest and covers the groups in order.
    Σ covered = min(Σ typed, Σ booked), so Σ (booked − covered) is the kind's gap."""
    covered = [ZERO] * len(groups)
    pool = ZERO
    for amount, label in rows:
        left = amount
        if label:
            for i, g in enumerate(groups):
                if left <= ZERO:
                    break
                if g.label and same_payee(label, g.label, None, g.purpose):
                    take = min(left, g.booked - covered[i])
                    covered[i] += take
                    left -= take
        pool += left
    for i, g in enumerate(groups):
        if pool <= ZERO:
            break
        take = min(pool, g.booked - covered[i])
        covered[i] += take
        pool -= take
    return covered


def _label(g: _Payee) -> dict[str, str]:
    """The payee's name for a childcare or §35a row (`payee_label`), when the lines name one."""
    return {"payee_label": g.label} if g.label else {}


def _of_kind(rows: Sequence[ReturnInputFact], kind: ReturnInputKind, tax_year: int):
    return [r for r in current(rows) if r.kind is kind and r.tax_year == tax_year]


def _eligible_children(
    rows: Sequence[ReturnInputFact], tax_year: int, max_age: int
) -> list[ChildChoice]:
    """The current children born by the year's end who were under `max_age` when it began
    (services/return_inputs.py refuses a childcare row for any other)."""
    out = []
    for r in current(rows):
        if r.kind is not K.CHILD or r.tax_year > tax_year:
            continue
        assert isinstance(r.value, ChildFact)
        born = r.value.born_on
        if born <= date(tax_year, 12, 31) and born > date(tax_year - max_age, 1, 1):
            out.append(ChildChoice(id=r.id, born_on=born))
    return sorted(out, key=lambda c: (c.born_on, str(c.id)))


def _household_proposal(
    group: BookedGroup,
    tax_year: int,
    g: _Payee,
    typed: Decimal,
    value: dict,
    needs: list[BookedNeed],
    **extra,
) -> BookedProposal:
    difference = g.booked - typed
    return BookedProposal(
        group=group,
        kind=KIND_OF_GROUP[group],
        tax_year=tax_year,
        payee=g.label,
        booked=cents(g.booked),
        typed=cents(typed),
        difference=cents(difference),
        payments=len(g.lines),
        allocation_ids=[x.allocation_id for x in g.lines],
        payload={
            "owner_id": None,
            "kind": KIND_OF_GROUP[group].value,
            "tax_year": tax_year,
            "value": value,
        },
        needs=needs,
        **extra,
    )


def _donations(tax_year, rows, lines) -> list[BookedProposal]:
    groups = _payees(lines)
    typed = [
        (r.value.amount, r.value.recipient_label)
        for r in _of_kind(rows, K.SPECIAL_EXPENSE, tax_year)
        if isinstance(r.value, SpecialExpenseFact) and r.value.expense_class == "donation"
    ]
    out = []
    for g, covered in zip(groups, _covered_by_amount(groups, typed), strict=True):
        if covered >= g.booked:
            continue
        amount = str(cents(g.booked - covered))
        value = {"expense_class": "donation", "amount": amount, "recipient_label": g.label}
        out.append(
            _household_proposal(
                "donation",
                tax_year,
                g,
                covered,
                value,
                [] if g.label else ["recipient"],
                receipt_needed=any(-x.amount > DONATION_RECEIPT_LIMIT for x in g.lines),
            )
        )
    return out


def _childcare(tax_year, rows, lines, max_age) -> list[BookedProposal]:
    groups = _payees(lines)
    typed = [
        (r.value.amount, r.value.payee_label)
        for r in _of_kind(rows, K.CHILDCARE_COST, tax_year)
        if isinstance(r.value, ChildcareFact)
    ]
    children = _eligible_children(rows, tax_year, max_age)
    child = str(children[0].id) if len(children) == 1 else None
    out = []
    for g, covered in zip(groups, _covered_by_amount(groups, typed), strict=True):
        if covered >= g.booked:
            continue
        value = {
            "child_id": child,
            "amount": str(cents(g.booked - covered)),
            "paid_to_account": True,
            **_label(g),
        }
        out.append(
            _household_proposal(
                "childcare",
                tax_year,
                g,
                covered,
                value,
                [] if child else ["child"],
                child_choices=[] if child else children,
            )
        )
    return out


def _services(tax_year, rows, lines) -> list[BookedProposal]:
    groups = _payees(lines)
    own = [
        r
        for r in _of_kind(rows, K.HOUSEHOLD_SERVICES, tax_year)
        if isinstance(r.value, HouseholdServicesFact)
        and r.value.paid_to_account
        and r.value.source == "own_invoice"
    ]
    open_ = list(groups)
    unlabelled = 0
    for r in own:
        label = r.value.payee_label
        if label is None:
            unlabelled += 1
            continue
        for g in open_:
            if g.label and same_payee(label, g.label, None, g.purpose):
                open_.remove(g)
                break
    # A row without a label closes the earliest payee still open, as before the label.
    open_ = open_[unlabelled:]
    return [
        _household_proposal(
            "household_services",
            tax_year,
            g,
            ZERO,
            {"paid_to_account": True, "source": "own_invoice", **_label(g)},
            ["labour_cost", "service_class"],
        )
        for g in open_
    ]


def booked_proposals(
    tax_year: int,
    owner_facts: Sequence[OwnerReturnFacts],
    household_rows: Sequence[ReturnInputFact],
    lines: Sequence[BookedLine],
    *,
    childcare_max_age: int = CHILDCARE_MAX_AGE,
) -> list[BookedProposal]:
    """Every proposal of `tax_year` (the module docstring): the benefits by owner, then the
    donations, the childcare and the §35a payments by payee. `household_rows` are the
    household kinds' rows (any year; the rule picks the current rows of the year), `lines` the
    confirmed private lines of the proposing codes (any year)."""
    of_year: dict[BookedGroup, list[BookedLine]] = {g: [] for g in GROUP_ORDER}
    for line in lines:
        group = group_of(line)
        if group is not None and line.booking_date.year == tax_year:
            of_year[group].append(line)
    out = [
        _benefit_proposal(tax_year, g)
        for g in _benefit_groups(tax_year, owner_facts, of_year["benefit"])
    ]
    out += _donations(tax_year, household_rows, of_year["donation"])
    out += _childcare(tax_year, household_rows, of_year["childcare"], childcare_max_age)
    out += _services(tax_year, household_rows, of_year["household_services"])
    return out
