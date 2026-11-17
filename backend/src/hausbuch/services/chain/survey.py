"""`hausbuch survey-solana` (phase 7, 17 Nov 2026; Luis: „Yes let's start with step 1“): count
one Solana wallet's history before it is added, so the decoders worth adding and whether
Q-O162 must be fixed first are decided from figures, not guesses. Read-only: nothing is
written to the database and nothing is booked.

**What it reads.** What SolanaSource (services/chain/solana_rpc.py) would list on the
address's first sync: the finalized height, the address's signatures, each token account
getTokenAccountsByOwner gives today and each closed token account a record names as the
address's, every one in full; then each transaction (or an even sample with `max_tx`).

**How it classifies.** Each record is kept as the sync's source keeps it
(solana.with_token_accounts, every own token account found) and read by the same classifier
(rules/chain/classify.classify) with the address as the wallet's own, then routed by
rules/review.route_group on a first sync and on a later one, and sorted into the card a person
would see (services/wallet_groups.card_kind and confirmable). A transfer is never linked here
and a price is never read, so dust (D262) is counted by its shape only.

**What it keeps.** Every answer the RPC gave, under the cache directory (one file per request,
named by a hash of the request, so a signature is never a file name), so a second run sends no
request it already has. The address, a signature, a token account and the RPC URL are never
printed, logged or put into the report; an error names its class only."""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from hausbuch.rules.chain import solana
from hausbuch.rules.chain import survey as rules
from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, ClassifyContext, classify
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.chain.spam import listed
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.rules.review import route_group
from hausbuch.schemas.allocation import RoutingDecision
from hausbuch.schemas.chain import GroupKind, GroupRoutingInput
from hausbuch.schemas.household import MovementKind
from hausbuch.schemas.wallet_cards import WalletCardKind
from hausbuch.services.chain.base import ChainSourceError
from hausbuch.services.chain.solana_rpc import (
    HistoryUnavailable,
    Post,
    SignatureInfo,
    SolanaRpc,
    SolanaRpcError,
    owned_token_accounts,
    urllib_post,
)
from hausbuch.services.chain.sync import _storable
from hausbuch.services.wallet_groups import card_kind, confirmable

JsonRecord = dict[str, object]

_DIRS = {"getTransaction": "tx", "getSignaturesForAddress": "sig"}
_ATTEMPTS = 6
_BACKOFF_S = 2.0


def default_post() -> Post:
    """The real transport (a test replaces this)."""
    return urllib_post


class SurveyError(Exception):
    """The survey cannot go on; the message names no address, signature or URL."""


def write_report(path: Path, report: str) -> None:
    """report.md beside the cache, readable by the operator only."""
    _write(path, report.encode("utf-8"))


# --- The transport: cache, throttle, retry ---------------------------------------------------


@dataclass
class CachedPost:
    """A SolanaRpc `post` that answers from the cache first; otherwise it sends at most `rps`
    requests a second (`sleep` is the throttling hook), retries a 429, a 5xx, a JSON-RPC 429
    or a transport failure with a growing pause, and stores a good answer (HTTP 200, a
    `result`, and for getTransaction a record) under `root` before handing it on."""

    root: Path
    post: Post
    rps: float = 2.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    attempts: int = _ATTEMPTS
    network: int = 0  # requests actually sent
    _last: float | None = field(default=None, repr=False)

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float, max_bytes: int
    ) -> tuple[int, bytes]:
        request = json.loads(body)
        method = str(request["method"])
        path = self.path(method, request["params"])
        if path.is_file():
            return 200, path.read_bytes()
        for attempt in range(self.attempts):
            last = attempt == self.attempts - 1
            self._throttle()
            self.network += 1
            try:
                status, answer = self.post(url, headers, body, timeout, max_bytes)
            except Exception:  # noqa: BLE001 - SolanaRpc names the class only
                if last:
                    raise
                self.sleep(_BACKOFF_S * 2**attempt)
                continue
            if (status == 429 or status >= 500 or _throttled(answer)) and not last:
                self.sleep(_BACKOFF_S * 2**attempt)
                continue
            if status == 200 and _cacheable(method, answer):
                _write(path, answer)
            return status, answer
        raise AssertionError("unreachable")  # pragma: no cover

    def path(self, method: str, params: object) -> Path:
        text = json.dumps([method, params], sort_keys=True, separators=(",", ":"))
        name = hashlib.sha256(text.encode()).hexdigest()
        return self.root / _DIRS.get(method, "rpc") / f"{name}.json"

    def _throttle(self) -> None:
        now = self.clock()
        if self._last is not None:
            wait = 1 / self.rps - (now - self._last)
            if wait > 0:
                self.sleep(wait)
                now = self.clock()
        self._last = now


def _answer(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _throttled(raw: bytes) -> bool:
    data = _answer(raw)
    error = data.get("error") if data else None
    return isinstance(error, dict) and error.get("code") == 429


def _cacheable(method: str, raw: bytes) -> bool:
    data = _answer(raw)
    if data is None or data.get("error") is not None or "result" not in data:
        return False
    return not (method == "getTransaction" and data["result"] is None)


def _write(path: Path, data: bytes) -> None:
    """Atomically, so an interrupted run never leaves a half file for the next one to trust;
    readable by the operator only."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


# --- The listing -------------------------------------------------------------------------------


@dataclass
class _Listed:
    infos: dict[str, SignatureInfo] = field(default_factory=dict)  # signature -> info
    members: dict[str, list[str]] = field(default_factory=dict)  # account -> its signatures
    kinds: dict[str, rules.ListingKind] = field(default_factory=dict)

    def add(self, account: str, kind: rules.ListingKind, infos: list[SignatureInfo], height: int):
        self.kinds[account] = kind
        mine = self.members.setdefault(account, [])
        for info in infos:
            if info.slot > height:
                continue
            mine.append(info.signature)
            self.infos.setdefault(info.signature, info)


def _sample(order: list[str], max_tx: int | None) -> list[str]:
    if max_tx is None or max_tx >= len(order):
        return order
    if max_tx <= 0:
        return []
    return [order[i * len(order) // max_tx] for i in range(max_tx)]


# --- One transaction -------------------------------------------------------------------------


def _decimals(leg: ChainLeg) -> int:
    """The decimals the sync's new asset row would get (sync._new_asset)."""
    key = leg.asset_key
    if key.contract is None:
        return registry().chain(key.chain).native_decimals
    token = asset_registry().token(key.chain, key.contract)
    if token is not None:
        return token.decimals
    return leg.decimals or 0


def _collides(group: ChainGroup, address: str) -> bool:
    """sync._plan: two assets on one movement place make the transaction unstorable."""
    seen: dict[tuple[str, str, str], object] = {}
    for leg in group.legs:
        place = (leg.address or address, leg.index, leg.role)
        if place in seen and seen[place] != leg.asset_key:
            return True
        seen.setdefault(place, leg.asset_key)
    return False


def _dust_shape(group: ChainGroup, legs: list[ChainLeg]) -> bool:
    """wallet_groups._dust_candidate without its price: one uncategorized receipt."""
    return (
        group.kind is GroupKind.TRANSFER
        and group.question is None
        and len(legs) == 1
        and legs[0].kind is MovementKind.UNCATEGORIZED
        and legs[0].role == "in"
    )


def _card(group: ChainGroup, legs: list[ChainLeg], first_sync: bool) -> rules.Outcome:
    every_listed = all(listed(leg.asset_key) for leg in legs)
    fee_only = bool(legs) and all(leg.role == "fee" for leg in legs)
    routing = route_group(
        GroupRoutingInput(
            kind=group.kind,
            fee_only=fee_only,
            decoder_listed=group.decoder_listed,
            priced=every_listed,
            registry_assets=every_listed,
            first_sync=first_sync,
            linked=group.linked,
            pair_learned=group.pair_learned,
            unregistered_output=group.unregistered_output,
            question=group.question is not None,
        )
    )
    if routing.decision is RoutingDecision.AUTO_CONFIRM:
        return rules.Outcome.AUTO
    kind = card_kind(group.question, bool(legs))
    if kind is WalletCardKind.UNREADABLE:
        return rules.Outcome.UNREADABLE
    if kind is WalletCardKind.UNSTORABLE:
        return rules.Outcome.UNSTORABLE
    if kind is not WalletCardKind.PROPOSED:
        return rules.Outcome.QUESTION
    if _dust_shape(group, legs):
        return rules.Outcome.DUST_SHAPE
    if confirmable(group.question, [leg.kind for leg in legs]):
        return rules.Outcome.CONFIRM
    moving = [leg for leg in legs if leg.role != "fee"]
    if moving and (
        all(leg.role == "out" for leg in moving) or all(leg.role == "in" for leg in moving)
    ):
        return rules.Outcome.ANSWERS
    return rules.Outcome.NO_ANSWER


def outcomes(group: ChainGroup, address: str) -> tuple[rules.Outcome, rules.Outcome]:
    """(first sync, later sync) for one group, after the sync's storage checks
    (sync._plan and _write_group): a leg collision or a quantity numeric(38,18) cannot hold
    makes it unstorable, a spam leg that does not fit is dropped."""
    if _collides(group, address):
        return rules.Outcome.UNSTORABLE, rules.Outcome.UNSTORABLE
    legs: list[ChainLeg] = []
    dropped = 0
    for leg in group.legs:
        if _storable(leg.signed, _decimals(leg)):
            legs.append(leg)
        elif leg.spam:
            dropped += 1
        else:
            return rules.Outcome.UNSTORABLE, rules.Outcome.UNSTORABLE
    if dropped and not legs and group.question is None:
        return rules.Outcome.SPAM_DROPPED, rules.Outcome.SPAM_DROPPED
    return _card(group, legs, True), _card(group, legs, False)


def observe(
    record: Mapping[str, object],
    info: SignatureInfo,
    address: str,
    accounts: set[str],
    ctx: ClassifyContext,
    when: int | None,
) -> rules.Observation:
    """One record as the sync would read it, reduced to what the report may print."""
    kept = solana.with_token_accounts(record, accounts, when)  # type: ignore[arg-type]
    try:
        top, inner = solana.instructions(kept)
        programs = (tuple(i.program for i in top), tuple(i.program for i in inner))
    except Exception:  # noqa: BLE001 - an old record without meta: no inner calls to read
        programs = ((), ())
    tx = SolanaRpc.chain_tx(kept, {address}, accounts, strict=False)
    year = tx.occurred_at.year if tx.occurred_at is not None else None
    lookup = not isinstance(record.get("blockTime"), int) and info.block_time is None
    group = classify(tx, ctx)
    if group is None or not (group.legs or group.question):
        return rules.Observation(year, info.failed, *programs, block_time_lookup=lookup)
    first, later = outcomes(group, address)
    spam_group = group.kind is GroupKind.SPAM
    mints = tuple(
        rules.MintSeen(leg.asset_key.contract, listed(leg.asset_key), leg.spam or spam_group)
        for leg in group.legs
        if leg.asset_key.contract is not None
    )
    return rules.Observation(
        year,
        info.failed,
        *programs,
        kind=group.kind.value,
        first=first,
        later=later,
        question=rules.question_type(group.question),
        mints=mints,
        block_time_lookup=lookup,
    )


# --- The survey ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Survey:
    report: str
    summary: rules.Summary
    listing: rules.ListingFacts
    estimate: rules.Estimate
    run: rules.RunFacts


def _fatal(exc: Exception) -> SurveyError:
    if isinstance(exc, ChainSourceError):
        status = f" (HTTP {exc.status})" if exc.status else ""
        return SurveyError(
            f"the RPC answered {exc.code}{status}; run again later, the survey resumes from "
            "its cache"
        )
    if isinstance(exc, SolanaRpcError):
        return SurveyError(f"the RPC failed ({exc}); run again, the survey resumes from its cache")
    return SurveyError(f"the survey failed ({type(exc).__name__})")


def _fetch(
    rpc: SolanaRpc, sig: str, slot: int, not_read: Counter[str]
) -> Mapping[str, object] | None:
    """One transaction's record, or None (counted by class) when the RPC serves none or
    answers it with a JSON-RPC error or bad JSON; a refusal, throttling past the retries or a
    transport failure ends the survey."""
    try:
        return rpc.transaction(sig, slot)
    except HistoryUnavailable:
        not_read["HistoryUnavailable"] += 1
    except SolanaRpcError as exc:
        if exc.kind not in ("rpc", "parse"):
            raise _fatal(exc) from None
        not_read[f"SolanaRpcError {exc.kind}"] += 1
    except ChainSourceError as exc:
        raise _fatal(exc) from None
    return None


def run_survey(
    address: str,
    *,
    rpc: SolanaRpc,
    transport: CachedPost,
    ctx: ClassifyContext,
    sync_budget: int,
    max_tx: int | None = None,
    rpc_source: str = "",
    context_label: str = "",
    progress: Callable[[str], None] | None = None,
) -> Survey:
    """Read, classify and count (see the module docstring); `progress` gets plain counts."""
    say = progress or (lambda _text: None)
    listed_ = _Listed()
    try:
        height = rpc.finalized_slot()
        accounts_now, _ = rpc.token_accounts(address)
        opened = sorted({a.pubkey for a in accounts_now} - {address})
        listed_.add(address, rules.ListingKind.ADDRESS, rpc.signatures(address), height)
        for pubkey in opened:
            listed_.add(pubkey, rules.ListingKind.OPEN, rpc.signatures(pubkey), height)
    except (ChainSourceError, SolanaRpcError) as exc:
        raise _fatal(exc) from None
    say(
        f"listed {len(listed_.infos)} signature(s) over the address and "
        f"{len(opened)} open token account(s)"
    )

    def order_of(sig: str) -> tuple[int, str]:
        return listed_.infos[sig].slot, sig

    wanted = _sample(sorted(listed_.infos, key=order_of), max_tx)
    queue = [order_of(s) for s in wanted]
    heapq.heapify(queue)
    queued = set(wanted)
    # Pass 1 keeps only the signatures it read (the records are in the cache, so pass 2
    # reads each again from there without a request: a long history never sits in memory).
    read: list[str] = []
    no_time: set[str] = set()  # neither the record nor the listing has a block time
    not_read: Counter[str] = Counter()
    while queue:
        _, sig = heapq.heappop(queue)
        info = listed_.infos[sig]
        record = _fetch(rpc, sig, info.slot, not_read)
        if record is None:
            continue
        read.append(sig)
        if not isinstance(record.get("blockTime"), int) and info.block_time is None:
            no_time.add(sig)
        if len(read) % 500 == 0:
            say(f"read {len(read)} transaction(s)")
        try:
            owned = owned_token_accounts(record, address) - {address}  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - a malformed record names no token account
            owned = set()
        del record
        for pubkey in sorted(owned):
            if pubkey in listed_.kinds:
                continue
            try:
                found = rpc.signatures(pubkey)
            except (ChainSourceError, SolanaRpcError) as exc:
                raise _fatal(exc) from None
            listed_.add(pubkey, rules.ListingKind.CLOSED, found, height)
            if max_tx is None:
                for new in listed_.members[pubkey]:
                    if new not in queued:
                        queued.add(new)
                        heapq.heappush(queue, order_of(new))
    say(f"read {len(read)} transaction(s); classifying")

    accounts = set(listed_.kinds) - {address}
    observations: list[rules.Observation] = []
    rereads = 0
    for sig in sorted(read, key=order_of):
        info = listed_.infos[sig]
        before = rpc.requests
        record = _fetch(rpc, sig, info.slot, not_read)
        rereads += rpc.requests - before
        if record is None:
            continue
        try:
            when = None
            if not isinstance(record.get("blockTime"), int):
                stamp = info.block_time or rpc.block_time(info.slot)
                when = int(stamp.timestamp())
            observations.append(observe(record, info, address, accounts, ctx, when))
        except (ChainSourceError, SolanaRpcError) as exc:
            if isinstance(exc, SolanaRpcError) and exc.kind in ("history", "rpc", "parse"):
                not_read[type(exc).__name__] += 1
                continue
            raise _fatal(exc) from None
        except Exception as exc:  # noqa: BLE001 - counted by class: one record never ends it
            not_read[type(exc).__name__] += 1

    order = sorted(listed_.infos, key=order_of)
    index = {sig: i for i, sig in enumerate(order)}
    slots = [listed_.infos[s].slot for s in order]
    listings = [
        rules.Listing(listed_.kinds[a], tuple(index[s] for s in listed_.members[a]))
        for a in sorted(listed_.kinds, key=lambda a: (a != address, listed_.kinds[a], a))
    ]
    lookups = [index[s] for s in no_time]
    facts = rules.listing_facts(
        (
            i.block_time.year if i.block_time is not None else None,
            i.failed,
        )
        for i in listed_.infos.values()
    )
    summary = rules.summarise(observations)
    plan = rules.estimate(slots, listings, lookups, sync_budget)
    run = rules.RunFacts(
        requests=rpc.requests - rereads,  # pass 2's reads of what pass 1 read are not new
        network=transport.network,
        sample=None if max_tx is None or max_tx >= len(order) else max_tx,
        not_read=dict(not_read),
        rpc_source=rpc_source,
        context=context_label,
    )
    report = rules.render(facts, summary, plan, run)
    return Survey(report, summary, facts, plan, run)
