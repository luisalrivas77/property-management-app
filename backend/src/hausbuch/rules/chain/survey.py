"""The Solana history survey's pure part (phase 7, 17 Nov 2026, before liquidity pools are
supported; `hausbuch survey-solana`, services/chain/survey.py): what one wallet's history holds
and how the current classifier and routing would treat it, aggregated from observations that
carry no address, signature or token account, and printed as a plain report.

Nothing here reads a chain or a database, and nothing here is a rule the sync applies: the
names of public programs below are a label for the operator's report only. They are kept here,
never in decoders.yaml or chains.yaml (those change only in a spec PR), and only ids checked
against the program's own repository (`declare_id!`) or its maker's published address list are
named; every other program is printed by its id as „unknown“."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from hausbuch.rules.chain import solana

# --- Program names (report labels only) -------------------------------------------------------

# Checked 17 Nov 2026 against the source named beside each (the mainnet id where a lib.rs
# declares one per network). Lifinity v2 is left out: no source of its own names its id.
PROGRAM_NAMES: Mapping[str, str] = {
    # Solana's own programs (rules/chain/solana.py and the Solana program library)
    solana.SYSTEM_PROGRAM: "System Program",
    solana.TOKEN_PROGRAM: "SPL Token",
    solana.TOKEN_2022_PROGRAM: "SPL Token-2022",
    solana.ATA_PROGRAM: "Associated Token Account",
    solana.COMPUTE_BUDGET_PROGRAM: "Compute Budget",
    solana.MEMO_PROGRAM: "SPL Memo v3",  # solana-program/memo interface/src/lib.rs (mod v3)
    solana.STAKE_PROGRAM: "Stake Program",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo": "SPL Memo v1",  # same file (mod v1)
    "Memo4c2pN8afCj432Lb7RMVKi9PbQnnW7ewFFaV3oAH": "SPL Memo v4",  # same file (mod v4)
    # solana-program/address-lookup-table program/src/lib.rs
    "AddressLookupTab1e1111111111111111111111111": "Address Lookup Table",
    # solana-program/stake-pool program/src/lib.rs
    "SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy": "SPL Stake Pool",
    # jup-ag/instruction-parser README.md and src/constants.ts
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter Aggregator v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter Aggregator v4",
    # raydium-io/raydium-amm program/src/lib.rs
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM v4",
    # raydium-io/raydium-clmm programs/amm/src/lib.rs
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    # raydium-io/raydium-cp-swap programs/cp-swap/src/lib.rs
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    # orca-so/whirlpools programs/whirlpool/src/lib.rs
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    # orca-so/typescript-sdk src/public/utils/constants.ts (ORCA_TOKEN_SWAP_ID)
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "Orca Token Swap v2 (legacy)",
    # MeteoraAg/dlmm-sdk ts-client/src/dlmm/constants/index.ts (mainnet-beta)
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    # MeteoraAg/damm-v1-sdk programs/dynamic-amm/src/lib.rs
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora Dynamic AMM (DAMM v1)",
    # MeteoraAg/damm-v2 programs/cp-amm/src/lib.rs
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "Meteora DAMM v2",
    # marinade-finance/liquid-staking-program programs/marinade-finance/src/lib.rs
    "MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD": "Marinade liquid staking",
    # Ellipsis-Labs/phoenix-v1 src/lib.rs
    "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY": "Phoenix v1",
    # openbook-dex/openbook-v2 programs/openbook-v2/src/lib.rs
    "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb": "OpenBook v2",
    # openbook-dex/program README.md (Mainnet Beta)
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX": "OpenBook v1",
    # project-serum/serum-dex README.md (Mainnet Beta)
    "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin": "Serum DEX v3",
    # Kamino-Finance/klend programs/klend/src/lib.rs
    "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD": "Kamino Lending",
    # drift-labs/protocol-v2 programs/drift/src/lib.rs
    "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH": "Drift v2",
    # tensor-foundation/escrow programs/escrow/src/lib.rs (keeps the TensorSwap id)
    "TSWAPaqyCSx2KABk68Shruf4rp7CxcNi8hAsbdwmHbN": "Tensor Swap",
    # me-foundation/m2 programs/m2/src/lib.rs
    "M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K": "Magic Eden v2",
    # jito-foundation/jito-programs mev-programs/programs/tip-payment/src/lib.rs
    "T1pyyaTNZsKv2WcRAB8oVnk93mLJw2XzjtVYqCsaHqt": "Jito tip payment",
}

UNKNOWN_PROGRAM = "unknown"

# Programs every ordinary transaction calls; left out of the list of programs behind the
# transactions that need a person, where they would always come first and say nothing.
PLAIN = solana.PLAIN_PROGRAMS | {
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
    "Memo4c2pN8afCj432Lb7RMVKi9PbQnnW7ewFFaV3oAH",
}


def program_name(program: str) -> str:
    return PROGRAM_NAMES.get(program, UNKNOWN_PROGRAM)


# --- What happens to one transaction ----------------------------------------------------------


class Outcome(StrEnum):
    """What the sync would do with one transaction, by its routing and its card."""

    AUTO = "auto"  # booked by code, no card
    CONFIRM = "confirm"  # a card whose „Bestätigen“ books it as proposed (one click)
    ANSWERS = "answers"  # a card with answer buttons (payment; gift, reward, spam)
    DUST_SHAPE = "dust_shape"  # one small-looking receipt: dust (D262) if worth < 0.01 € that day
    NO_ANSWER = "no_answer"  # legs both ways and no fitting button (Q-O163)
    QUESTION = "question"  # a card with a question and no amounts
    UNREADABLE = "unreadable"  # wallet.record_unreadable: the person names the tokens
    UNSTORABLE = "unstorable"  # routing.chain_unstorable: no movements can be written
    SPAM_DROPPED = "spam_dropped"  # only spam legs no quantity fits: nothing is written
    NOTHING = "nothing"  # nothing moved for this address (no group)
    NOT_READ = "not_read"  # the record or the classifier failed: a real sync would stop here


OUTCOME_LABELS: Mapping[Outcome, str] = {
    Outcome.AUTO: "booked by code",
    Outcome.CONFIRM: "card, one click Bestätigen",
    Outcome.ANSWERS: "card with answer buttons",
    Outcome.DUST_SHAPE: "card, dust-shaped receipt",
    Outcome.NO_ANSWER: "card, no fitting answer",
    Outcome.QUESTION: "card with a question",
    Outcome.UNREADABLE: "card, unreadable",
    Outcome.UNSTORABLE: "card, unstorable",
    Outcome.SPAM_DROPPED: "spam, nothing written",
    Outcome.NOTHING: "nothing for this address",
    Outcome.NOT_READ: "not read (sync would stop)",
}

# The outcomes a person has to act on (a card in Zu prüfen).
NEEDS_PERSON = frozenset(
    {
        Outcome.CONFIRM,
        Outcome.ANSWERS,
        Outcome.DUST_SHAPE,
        Outcome.NO_ANSWER,
        Outcome.QUESTION,
        Outcome.UNREADABLE,
        Outcome.UNSTORABLE,
    }
)


class QuestionType(StrEnum):
    """The kind of a group's question; the question's text is never kept (it may hold a
    masked program or stake account)."""

    NONE = "none"
    PROGRAM = "program"
    UNKNOWN = "unknown"
    UNREADABLE = "unreadable"
    OTHER = "other"


def question_type(question: str | None) -> QuestionType:
    if question is None:
        return QuestionType.NONE
    if question == solana.QUESTION_UNREADABLE:
        return QuestionType.UNREADABLE
    if question == solana.QUESTION_UNKNOWN:
        return QuestionType.UNKNOWN
    if solana.is_program_question(question):
        return QuestionType.PROGRAM
    return QuestionType.OTHER


@dataclass(frozen=True, slots=True)
class MintSeen:
    """A token a transaction moved: its mint (in memory only, never printed), whether the
    public registry lists it, and whether the classifier called it spam (D187)."""

    mint: str
    listed: bool
    spam: bool


@dataclass(frozen=True, slots=True)
class Observation:
    """One transaction read in full. No hash, no address, no token account: what the report
    may print is all there is."""

    year: int | None
    failed: bool
    top_programs: tuple[str, ...] = ()
    inner_programs: tuple[str, ...] = ()
    kind: str | None = None  # the group's GroupKind value; None without a group
    first: Outcome = Outcome.NOTHING  # on the address's first sync (everything waits)
    later: Outcome = Outcome.NOTHING  # on a later sync of the same transaction
    question: QuestionType = QuestionType.NONE
    mints: tuple[MintSeen, ...] = ()
    block_time_lookup: bool = False  # the record carried no blockTime: one more request


# --- The listing and the request estimate (Q-O162) --------------------------------------------


class ListingKind(StrEnum):
    ADDRESS = "address"
    OPEN = "open"  # a token account getTokenAccountsByOwner gives today
    CLOSED = "closed"  # a token account found only through a record that names it


@dataclass(frozen=True, slots=True)
class Listing:
    """One listed account: its kind and its transactions as indexes into the survey's merged
    listing (never a signature)."""

    kind: ListingKind
    members: tuple[int, ...]


SIGNATURE_PAGE = 1000  # SolanaRpc.signature_page
START_FIXED = 3  # getSlot, and getTokenAccountsByOwner for both token programs
BALANCE_REQUESTS = 3  # getBalance and both token programs again (SolanaRpc.balances)
MAX_DAYS = 3650


def listing_pages(count: int) -> int:
    """Requests to list `count` signatures: full pages, then the empty one that ends it."""
    return count // SIGNATURE_PAGE + 1


@dataclass(frozen=True, slots=True)
class Estimate:
    """The first sync's requests against one run's budget (Q-O162)."""

    budget: int
    start: int  # the first run's start: height, the address's and open accounts' listings
    total: int  # every request the whole first read takes, runs repeated from the cursor
    days: int | None  # daily runs until the history is read; None: it stalls
    stalls: bool
    open_accounts: int
    closed_accounts: int


def estimate(
    slots: Sequence[int],
    listings: Sequence[Listing],
    lookups: Iterable[int],
    budget: int,
) -> Estimate:
    """Simulate the daily first read: each run pays the start (the height, both token
    programs, the address's and each open token account's listing of what is still above the
    cursor), then one getTransaction per transaction oldest first (plus getBlockTime where the
    record has no time), listing a closed token account in full when its first transaction
    is reached; the balances once all is read. A run that cannot serve one transaction after
    its start stalls (Q-O162: the next day's run is the same run). An approximation of
    SolanaSource: a run's cursor is the last slot it served."""
    lookup = set(lookups)
    order = sorted(range(len(slots)), key=lambda i: (slots[i], i))
    owners: dict[int, list[int]] = {}
    for n, listing in enumerate(listings):
        for member in listing.members:
            owners.setdefault(member, []).append(n)
    started = [n for n, x in enumerate(listings) if x.kind is not ListingKind.CLOSED]
    opened = sum(1 for x in listings if x.kind is ListingKind.OPEN)
    closed = sum(1 for x in listings if x.kind is ListingKind.CLOSED)
    listed_closed: set[int] = set()
    position, total, days, first_start = 0, 0, 0, None
    while True:
        days += 1
        cursor = slots[order[position]] if position < len(order) else None
        start = START_FIXED + sum(
            listing_pages(
                sum(1 for m in listings[n].members if cursor is None or slots[m] >= cursor)
            )
            for n in started
        )
        first_start = start if first_start is None else first_start
        spent = start
        served = 0
        while position < len(order):
            tx = order[position]
            cost = 1 + (1 if tx in lookup else 0)
            for n in owners.get(tx, ()):
                if listings[n].kind is ListingKind.CLOSED and n not in listed_closed:
                    cost += listing_pages(len(listings[n].members))
            if spent + cost > budget:
                break
            spent += cost
            for n in owners.get(tx, ()):
                if listings[n].kind is ListingKind.CLOSED:
                    listed_closed.add(n)
            position += 1
            served += 1
        if position >= len(order) and spent + BALANCE_REQUESTS <= budget:
            total += spent + BALANCE_REQUESTS
            return Estimate(budget, first_start, total, days, False, opened, closed)
        total += spent
        if served == 0 or days >= MAX_DAYS:
            return Estimate(budget, first_start, total, None, True, opened, closed)


# --- The report -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListingFacts:
    """What the signature listing says of the whole history (free of getTransaction)."""

    total: int
    failed: int
    years: Mapping[int | None, int]
    first_year: int | None
    last_year: int | None


def listing_facts(entries: Iterable[tuple[int | None, bool]]) -> ListingFacts:
    """From (year or None, failed) per listed signature."""
    years: Counter[int | None] = Counter()
    failed = total = 0
    for year, bad in entries:
        total += 1
        failed += int(bad)
        years[year] += 1
    known = [y for y in years if y is not None]
    return ListingFacts(
        total, failed, dict(years), min(known, default=None), max(known, default=None)
    )


@dataclass(slots=True)
class ProgramRow:
    program: str
    transactions: int = 0
    top: int = 0
    inner: int = 0
    first_year: int | None = None
    last_year: int | None = None

    def see(self, year: int | None, top: bool, inner: bool) -> None:
        self.transactions += 1
        self.top += int(top)
        self.inner += int(inner)
        if year is not None:
            self.first_year = year if self.first_year is None else min(self.first_year, year)
            self.last_year = year if self.last_year is None else max(self.last_year, year)


@dataclass(slots=True)
class Summary:
    observed: int = 0
    programs: dict[str, ProgramRow] = field(default_factory=dict)
    first: Counter[tuple[str, Outcome]] = field(default_factory=Counter)
    later: Counter[tuple[str, Outcome]] = field(default_factory=Counter)
    person_programs: Counter[str] = field(default_factory=Counter)
    questions: Counter[QuestionType] = field(default_factory=Counter)
    mints: int = 0
    mints_listed: int = 0
    mints_spam: int = 0
    lookups: int = 0


NO_GROUP = "(no group)"


def summarise(observations: Iterable[Observation]) -> Summary:
    """Counts per program (a program counted once per transaction, top level or inner), per
    group kind and outcome in both views, and the programs behind the transactions that need
    a person on a later sync (PLAIN programs left out)."""
    out = Summary()
    mints: dict[str, tuple[bool, bool]] = {}
    for o in observations:
        out.observed += 1
        out.lookups += int(o.block_time_lookup)
        top, inner = set(o.top_programs), set(o.inner_programs)
        for program in sorted(top | inner):
            row = out.programs.setdefault(program, ProgramRow(program))
            row.see(o.year, program in top, program in inner)
        kind = o.kind or NO_GROUP
        out.first[(kind, o.first)] += 1
        out.later[(kind, o.later)] += 1
        if o.kind is not None:
            out.questions[o.question] += 1
        if o.later in NEEDS_PERSON:
            for program in sorted((top | inner) - PLAIN):
                out.person_programs[program] += 1
        for seen in o.mints:
            listed, spam = mints.get(seen.mint, (seen.listed, False))
            mints[seen.mint] = (listed, spam or seen.spam)
    out.mints = len(mints)
    out.mints_listed = sum(1 for listed, _ in mints.values() if listed)
    out.mints_spam = sum(1 for _, spam in mints.values() if spam)
    return out


@dataclass(frozen=True, slots=True)
class RunFacts:
    """How the survey ran: requests asked (as the sync would count them) and sent."""

    requests: int
    network: int
    sample: int | None  # --max-tx, None: every transaction
    not_read: Mapping[str, int]  # error class -> count
    rpc_source: str  # where the RPC address came from, never the address
    context: str  # what the classifier knew (the public registry, or a workspace's rows)


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f} %" if whole else "–"


def _year(value: int | None) -> str:
    return "–" if value is None else str(value)


def _outcome_table(title: str, counts: Counter[tuple[str, Outcome]]) -> list[str]:
    outcomes = [o for o in Outcome if any(oc is o for _, oc in counts)]
    kinds = sorted({k for k, _ in counts}, key=lambda k: (k == NO_GROUP, k))
    lines = [f"### {title}", ""]
    if not counts:
        return [*lines, "(no transaction read)", ""]
    lines.append("| kind | " + " | ".join(OUTCOME_LABELS[o] for o in outcomes) + " | total |")
    lines.append("|---" * (len(outcomes) + 2) + "|")
    for kind in kinds:
        row = [counts.get((kind, o), 0) for o in outcomes]
        lines.append(f"| {kind} | " + " | ".join(map(str, row)) + f" | {sum(row)} |")
    totals = [sum(n for (_, oc), n in counts.items() if oc is o) for o in outcomes]
    lines.append("| all | " + " | ".join(map(str, totals)) + f" | {sum(totals)} |")
    groups = sum(n for (k, _), n in counts.items() if k != NO_GROUP)
    person = sum(n for (k, oc), n in counts.items() if k != NO_GROUP and oc in NEEDS_PERSON)
    lines += ["", f"Need a person: {person} of {groups} groups ({_pct(person, groups)}).", ""]
    return lines


def render(
    listing: ListingFacts,
    summary: Summary,
    plan: Estimate,
    run: RunFacts,
) -> str:
    """The report as Markdown: counts, program ids (public programs) and labels only."""
    lines = ["# Solana history survey", ""]
    if run.sample is not None:
        lines += [
            f"**This is a sample**: {summary.observed} of {listing.total} transactions were "
            f"read (--max-tx {run.sample}, spread evenly over the history). The listing "
            "figures cover the whole history; the program, outcome and token figures cover "
            "the sample only, and closed token accounts are found only through the sampled "
            "records, so some may be missing.",
            "",
        ]
    lines += [
        f"RPC: {run.rpc_source}. Classifier context: {run.context}.",
        "",
        "## History",
        "",
        f"- Signatures listed: {listing.total} (failed: {listing.failed})",
        f"- Years: {_year(listing.first_year)} to {_year(listing.last_year)}",
    ]
    for year in sorted(listing.years, key=lambda y: (y is None, y or 0)):
        lines.append(f"  - {_year(year)}: {listing.years[year]}")
    lines.append(f"- Transactions read in full: {summary.observed}")
    if run.not_read:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(run.not_read.items()))
        lines.append(f"- Not read (a real sync would stop there): {parts}")
    lines += [
        "",
        "## Programs",
        "",
        "Counted per transaction (a program called several times in one transaction counts "
        "once); names only for ids checked against the program's own source.",
        "",
        "| program id | name | transactions | top level | inner | first | last |",
        "|---|---|---|---|---|---|---|",
    ]
    rows = sorted(summary.programs.values(), key=lambda r: (-r.transactions, r.program))
    for r in rows:
        lines.append(
            f"| {r.program} | {program_name(r.program)} | {r.transactions} | {r.top} | "
            f"{r.inner} | {_year(r.first_year)} | {_year(r.last_year)} |"
        )
    lines += ["", "## What the sync would do", ""]
    lines += [
        "Group kind by outcome. On the address's first sync every group except a failed "
        "transaction, spam or a fee alone waits for a person (routing.chain_first_sync); the "
        "second table is the same transactions on a later sync. A transfer is booked there "
        "only once linked to another own account, which this survey does not try "
        "(no transfer counts as linked). „dust-shaped“ is a single receipt from a stranger: "
        "it is dust (D262) only if worth less than 0.01 € that day, which needs a price.",
        "",
    ]
    lines += _outcome_table("First sync", summary.first)
    lines += _outcome_table("Later sync", summary.later)
    if summary.questions:
        parts = ", ".join(f"{q.value} {n}" for q, n in sorted(summary.questions.items()))
        lines += [f"Questions asked (by type): {parts}.", ""]
    lines += [
        "## Programs behind the transactions that need a person (later sync, top 15)",
        "",
        "Without the plain programs (System, Token, Compute Budget, ATA, Memo).",
        "",
    ]
    top = sorted(summary.person_programs.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    if not top:
        lines += ["(none)", ""]
    else:
        lines += ["| program id | name | transactions |", "|---|---|---|"]
        lines += [f"| {p} | {program_name(p)} | {n} |" for p, n in top]
        lines.append("")
    lines += [
        "## Tokens",
        "",
        f"- Mints moved: {summary.mints}",
        f"- Listed in the public registry: {summary.mints_listed}",
        f"- Called spam by the classifier (D187): {summary.mints_spam}",
        "",
        "## Requests (Q-O162)",
        "",
        f"- This survey: {run.requests} requests asked, {run.network} sent "
        "(the rest came from its cache)",
        f"- The sync's budget per run: {plan.budget} requests (one run a day, shared by every "
        "Solana address of the household)",
        f"- Token accounts: {plan.open_accounts} open, {plan.closed_accounts} closed found",
        f"- First run's start (height, listings of the address and open token accounts): "
        f"{plan.start} requests",
        f"- The first read in total: about {plan.total} requests",
    ]
    if run.sample is not None:
        lines.append(
            "  (from a sample: getBlockTime and closed token accounts counted for the "
            "sampled records only)"
        )
    if plan.stalls:
        lines.append(
            "- **The first read would stall**: a run's start leaves no room for the next "
            "transaction, so every daily run repeats the same start (Q-O162 must be fixed "
            "first)."
        )
    else:
        lines.append(f"- The first read would take about {plan.days} daily run(s).")
    lines.append("")
    return "\n".join(lines)
