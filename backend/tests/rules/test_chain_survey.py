"""The Solana history survey's pure part (rules/chain/survey.py): aggregation, the request
estimate (Q-O162) and the report. Invented data only."""

from __future__ import annotations

from hausbuch.rules.chain import solana
from hausbuch.rules.chain import survey as s

JUPITER = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
STRANGE = "Strange1111111111111111111111111111111111111"
MINT_A = "MintA111111111111111111111111111111111111111"
MINT_B = "MintB111111111111111111111111111111111111111"


def _obs(**kw) -> s.Observation:
    base = {"year": 2024, "failed": False}
    base.update(kw)
    return s.Observation(**base)


def test_program_names_only_for_known_ids():
    assert s.program_name(solana.SYSTEM_PROGRAM) == "System Program"
    assert s.program_name(JUPITER) == "Jupiter Aggregator v6"
    assert s.program_name(STRANGE) == s.UNKNOWN_PROGRAM
    # every named id is a base58 string of a program's length
    for program in s.PROGRAM_NAMES:
        assert 32 <= len(program) <= 44


def test_question_types_keep_no_text():
    assert s.question_type(None) is s.QuestionType.NONE
    assert s.question_type(solana.QUESTION_UNREADABLE) is s.QuestionType.UNREADABLE
    assert s.question_type(solana.QUESTION_UNKNOWN) is s.QuestionType.UNKNOWN
    assert s.question_type(solana.QUESTION_PROGRAM + "Abcd…wxyz?") is s.QuestionType.PROGRAM
    assert s.question_type("Ist Abcd…wxyz Ihr eigenes Stake-Konto?") is s.QuestionType.OTHER


def test_listing_facts_counts_years_and_failures():
    facts = s.listing_facts([(2022, False), (2023, True), (2023, False), (None, False)])
    assert facts.total == 4
    assert facts.failed == 1
    assert facts.years == {2022: 1, 2023: 2, None: 1}
    assert (facts.first_year, facts.last_year) == (2022, 2023)


def test_summarise_counts_programs_once_per_transaction():
    observations = [
        _obs(
            top_programs=(JUPITER, JUPITER, solana.COMPUTE_BUDGET_PROGRAM),
            inner_programs=(solana.TOKEN_PROGRAM, JUPITER),
            kind="swap",
            first=s.Outcome.CONFIRM,
            later=s.Outcome.CONFIRM,
            question=s.QuestionType.PROGRAM,
            year=2023,
        ),
        _obs(
            top_programs=(STRANGE,),
            kind="unknown",
            first=s.Outcome.QUESTION,
            later=s.Outcome.QUESTION,
            question=s.QuestionType.UNKNOWN,
            year=2025,
        ),
        _obs(top_programs=(solana.SYSTEM_PROGRAM,), kind="transfer", later=s.Outcome.AUTO),
        _obs(top_programs=(solana.SYSTEM_PROGRAM,)),  # nothing for this address
    ]
    out = s.summarise(observations)
    assert out.observed == 4
    jup = out.programs[JUPITER]
    assert (jup.transactions, jup.top, jup.inner) == (1, 1, 1)
    assert (jup.first_year, jup.last_year) == (2023, 2023)
    assert out.programs[STRANGE].first_year == 2025
    # plain programs never head the list of programs behind a person's work
    assert out.person_programs == {JUPITER: 1, STRANGE: 1}
    assert out.later[("transfer", s.Outcome.AUTO)] == 1
    assert out.later[(s.NO_GROUP, s.Outcome.NOTHING)] == 1
    assert out.questions == {
        s.QuestionType.PROGRAM: 1,
        s.QuestionType.UNKNOWN: 1,
        s.QuestionType.NONE: 1,
    }


def test_summarise_counts_each_mint_once():
    out = s.summarise(
        [
            _obs(mints=(s.MintSeen(MINT_A, True, False), s.MintSeen(MINT_B, False, False))),
            _obs(mints=(s.MintSeen(MINT_B, False, True),)),
        ]
    )
    assert (out.mints, out.mints_listed, out.mints_spam) == (2, 1, 1)


def test_listing_pages_end_on_an_empty_page():
    assert s.listing_pages(0) == 1
    assert s.listing_pages(999) == 1
    assert s.listing_pages(1000) == 2
    assert s.listing_pages(2500) == 3


def test_estimate_small_history_fits_one_run():
    slots = [10, 20, 30]
    listings = [s.Listing(s.ListingKind.ADDRESS, (0, 1, 2))]
    plan = s.estimate(slots, listings, lookups=[1], budget=100)
    # getSlot + 2 token programs + 1 page; 3 transactions, 1 getBlockTime; 3 balance requests
    assert plan.start == 4
    assert plan.total == 4 + 3 + 1 + 3
    assert (plan.days, plan.stalls) == (1, False)


def test_estimate_lists_a_closed_account_when_first_reached():
    slots = [10, 20, 30]
    listings = [
        s.Listing(s.ListingKind.ADDRESS, (0, 2)),
        s.Listing(s.ListingKind.CLOSED, (1, 2)),
    ]
    plan = s.estimate(slots, listings, lookups=[], budget=100)
    assert plan.closed_accounts == 1
    assert plan.total == 4 + 3 + 1 + 3  # the closed account's one listing page


def test_estimate_spreads_a_long_history_over_days():
    slots = list(range(50))
    listings = [s.Listing(s.ListingKind.ADDRESS, tuple(range(50)))]
    plan = s.estimate(slots, listings, lookups=[], budget=24)
    assert not plan.stalls
    assert plan.days == 3  # 20 a day after a start of 4, the balances on the third


def test_estimate_stalls_when_open_accounts_eat_the_budget():
    slots = [1, 2]
    listings = [s.Listing(s.ListingKind.ADDRESS, (0, 1))] + [
        s.Listing(s.ListingKind.OPEN, ()) for _ in range(30)
    ]
    plan = s.estimate(slots, listings, lookups=[], budget=30)
    assert plan.start == 3 + 1 + 30
    assert plan.stalls
    assert plan.days is None
    assert plan.open_accounts == 30


def _report(sample: int | None = None) -> str:
    listing = s.listing_facts([(2023, False), (2024, True)])
    summary = s.summarise(
        [
            _obs(
                top_programs=(JUPITER,),
                kind="swap",
                first=s.Outcome.CONFIRM,
                later=s.Outcome.AUTO,
            ),
            _obs(
                top_programs=(STRANGE,),
                kind="lp",
                first=s.Outcome.ANSWERS,
                later=s.Outcome.NO_ANSWER,
            ),
        ]
    )
    plan = s.estimate([1, 2], [s.Listing(s.ListingKind.ADDRESS, (0, 1))], [], 2400)
    run = s.RunFacts(
        requests=9,
        network=4,
        sample=sample,
        not_read={"HistoryUnavailable": 1},
        rpc_source="the public endpoint",
        context="the public registry and decoders",
    )
    return s.render(listing, summary, plan, run)


def test_report_lists_the_figures():
    text = _report()
    assert "Signatures listed: 2 (failed: 1)" in text
    assert f"| {JUPITER} | Jupiter Aggregator v6 |" in text
    assert f"| {STRANGE} | unknown |" in text
    assert "Need a person: 2 of 2 groups (100 %)" in text  # first sync
    assert "Need a person: 1 of 2 groups (50 %)" in text  # later sync
    assert "HistoryUnavailable 1" in text
    assert "would take about 1 daily run" in text
    assert "sample" not in text.split("## History")[0]


def test_report_says_it_is_a_sample():
    assert "**This is a sample**" in _report(sample=1)
