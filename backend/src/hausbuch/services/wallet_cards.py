"""The wallet page's state and the wallet cards (docs/phases/phase-7.md §13, §14, D183, D185,
D186, D187; the wallet screens).

Reads the sync's own rows and never the chain: an address's state is its last finished
`wallet_sync` row and its chain's source as configured now (`base.source_for` builds the
source without a request, so a missing key is seen before any run); its holdings are the
balances the last run stored beside what was booked then (balance − difference, D186 (2)).

Privacy (D179): no answer carries an address in full, a transaction hash, a URL or a key.
An address is its masked form; a source's setting is named by its container variable only.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, func, select

from hausbuch import i18n
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.errors import (
    WALLET_KEY_MISSING,
    WALLET_KEY_UNREADABLE,
    WALLET_SYNC_ERROR,
)
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.chain import Chain, ChainSourceKind, WalletSyncStatus
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.wallet_cards import (
    WalletAddressState,
    WalletAddressStatus,
    WalletCardKind,
    WalletHolding,
    WalletState,
)
from hausbuch.services.chain.base import ChainSourceError, reconcile_scope, source_for
from hausbuch.settings import Settings

S = WalletAddressState

CHAIN_NAMES = {
    Chain.BITCOIN: "Bitcoin",
    Chain.ETHEREUM: "Ethereum",
    Chain.ARBITRUM: "Arbitrum",
    Chain.BSC: "BNB Smart Chain",
    Chain.SOLANA: "Solana",
}

# The container variable that configures each source kind (runbook, D181 (e)): the one a
# person sets or checks in the Scaleway console. Never its value.
SOURCE_SETTING = {
    ChainSourceKind.EVM_ETHERSCAN: "HAUSBUCH_ETHERSCAN_KEY",
    ChainSourceKind.SOLANA_RPC: "HAUSBUCH_SOLANA_RPC_URL",
    ChainSourceKind.EVM_RPC_BALANCE: "HAUSBUCH_BSC_RPC_URL",
    ChainSourceKind.BITCOIN_ESPLORA: "HAUSBUCH_ESPLORA_URL",
}

# The texts of the wallet cards and address states, German beside English (phase 10 D284);
# chosen when the page or Zu prüfen is read. D260: a key or URL a person can type in the app
# is named there first, else the console.
TEXTS = i18n.Texts(
    "wallet_cards",
    {
        "where.app.enter": (
            "In Hausbuch unter Einstellungen › Zugangsschlüssel den Eintrag eintragen (oder in der"
            " Scaleway-Konsole die Variable {var})",
            "In Hausbuch under Settings › Access keys, enter the entry (or the variable {var} in"
            " the Scaleway console)",
        ),
        "where.console.enter": (
            "In der Scaleway-Konsole beim API-Container (Einstellungen › Umgebungsvariablen) die"
            " Variable {var} eintragen",
            "In the Scaleway console, on the API container (Settings › Environment variables),"
            " enter the variable {var}",
        ),
        "where.app.check": (
            "In Hausbuch unter Einstellungen › Zugangsschlüssel den Eintrag prüfen und korrigieren"
            " (oder in der Scaleway-Konsole die Variable {var})",
            "In Hausbuch under Settings › Access keys, check and correct the entry (or the"
            " variable {var} in the Scaleway console)",
        ),
        "where.console.check": (
            "In der Scaleway-Konsole beim API-Container (Einstellungen › Umgebungsvariablen) die"
            " Variable {var} prüfen und korrigieren",
            "In the Scaleway console, on the API container (Settings › Environment variables),"
            " check and correct the variable {var}",
        ),
        "where.app.change": (
            "In Hausbuch unter Einstellungen › Zugangsschlüssel den Eintrag auf einen anderen"
            " Anbieter ändern (oder in der Scaleway-Konsole die Variable {var})",
            "In Hausbuch under Settings › Access keys, change the entry to another provider (or"
            " the variable {var} in the Scaleway console)",
        ),
        "where.console.change": (
            "In der Scaleway-Konsole beim API-Container (Einstellungen › Umgebungsvariablen) die"
            " Variable {var} auf einen anderen Anbieter ändern",
            "In the Scaleway console, on the API container (Settings › Environment variables),"
            " change the variable {var} to another provider",
        ),
        "retired": (
            "Stillgelegt: diese Adresse wird nicht mehr abgerufen.",
            "Retired: this address is no longer fetched.",
        ),
        "key_missing": (
            "Nicht abgerufen: für {name} fehlt Hausbuch der Zugang zum Datenanbieter.",
            "Not fetched: Hausbuch lacks access to the data provider for {name}.",
        ),
        "key_missing.todo": (
            "{where}. Der nächste Abruf liest die Adresse dann; bis dahin bleibt sie gespeichert.",
            "{where}. The next fetch then reads the address; until then it stays stored.",
        ),
        "key_unreadable": (
            "Nicht abgerufen: der für {name} in Hausbuch gespeicherte Zugang kann nicht gelesen"
            " werden, weil der Schlüssel des Servers gewechselt wurde.",
            "Not fetched: the access stored in Hausbuch for {name} cannot be read because the"
            " server's key was changed.",
        ),
        "key_unreadable.todo": (
            "In Hausbuch unter Einstellungen › Zugangsschlüssel den Eintrag ändern und neu"
            " eintragen; der nächste Abruf liest die Adresse dann.",
            "In Hausbuch under Settings › Access keys, change the entry and enter it again; the"
            " next fetch then reads the address.",
        ),
        "source_unavailable": (
            "{name} liest Hausbuch noch nicht: die Adresse ist gespeichert und wird gelesen,"
            " sobald es das kann.",
            "Hausbuch cannot read {name} yet: the address is stored and will be read as soon as it"
            " can.",
        ),
        "nothing": (
            "Nichts zu tun.",
            "Nothing to do.",
        ),
        "not_synced": (
            "Noch nicht abgerufen.",
            "Not fetched yet.",
        ),
        "not_synced.todo": (
            "„Jetzt abrufen“ drücken oder den täglichen Abruf abwarten.",
            "Press “Fetch now” or wait for the daily fetch.",
        ),
        "sync_error": (
            "Abruf abgebrochen: ein Fehler in Hausbuch beim Einlesen. Nichts davon ist"
            " gespeichert.",
            "Fetch stopped: an error in Hausbuch while reading. None of it is stored.",
        ),
        "sync_error.todo": (
            "Der nächste Abruf liest dieselben Transaktionen noch einmal. Bleibt der Fehler,"
            " braucht es eine Korrektur in Hausbuch, keinen Klick hier.",
            "The next fetch reads the same transactions again. If the error stays, Hausbuch needs"
            " a fix, not a click here.",
        ),
        "failed": (
            "Abruf fehlgeschlagen: der Datenanbieter hat nicht geantwortet oder eine unlesbare"
            " Antwort geschickt.",
            "Fetch failed: the data provider did not answer or sent an unreadable answer.",
        ),
        "failed.todo": (
            "Der nächste Abruf versucht es erneut; „Jetzt abrufen“ versucht es sofort.",
            "The next fetch tries again; “Fetch now” tries at once.",
        ),
        "throttled": (
            "Der Datenanbieter hat zu viele Anfragen gemeldet und den Abruf gebremst.",
            "The data provider reported too many requests and slowed the fetch down.",
        ),
        "throttled.todo": (
            "Nichts zu tun: der Abruf setzt am nächsten Tag fort, wo er stand. Kommt das jeden Tag"
            " vor, {where}.",
            "Nothing to do: the fetch continues the next day where it stopped. If this happens"
            " every day, {where}.",
        ),
        "refused": (
            "Der Datenanbieter hat den Abruf abgelehnt: der Zugang ist ungültig oder gesperrt.",
            "The data provider refused the fetch: the access is invalid or blocked.",
        ),
        "refused.todo": (
            "{where}, dann „Jetzt abrufen“.",
            "{where}, then “Fetch now”.",
        ),
        "deferred": (
            "Die Geschichte dieser Adresse wird noch gelesen: der letzte Abruf hat sein"
            " Tageskontingent aufgebraucht.",
            "This address's history is still being read: the last fetch used up its daily quota.",
        ),
        "deferred.todo": (
            "Nichts zu tun: der nächste Abruf liest weiter, wo er stand.",
            "Nothing to do: the next fetch reads on where it stopped.",
        ),
        "reconciled.balance_only": (
            "Saldo gelesen. Auf {name} liest Hausbuch nur den Saldo, keine Bewegungen; er ist"
            " nicht kleiner als die verknüpften Einzahlungen.",
            "Balance read. On {name} Hausbuch reads only the balance, no movements; it is not"
            " smaller than the linked deposits.",
        ),
        "reconciled": (
            "Stimmt: die gebuchten Bewegungen ergeben den Saldo auf der Blockchain.",
            "Reconciled: the booked movements add up to the balance on the blockchain.",
        ),
        "waiting": (
            "Wartet auf Antwort: eine offene Frage zu einer Transaktion erklärt den Unterschied"
            " zum Saldo.",
            "Waiting for an answer: an open question about a transaction explains the difference"
            " from the balance.",
        ),
        "waiting.todo": (
            "Die Fragen unten oder unter „Zu prüfen“ beantworten.",
            "Answer the questions below or under “To review”.",
        ),
        "pending": (
            "Der Saldo wird beim nächsten Abruf geprüft (zusammen mit den anderen Adressen dieser"
            " Wallet, oder weil eine Antwort seit dem Abruf vorliegt).",
            "The balance is checked at the next fetch (together with this wallet's other"
            " addresses, or because an answer has arrived since the fetch).",
        ),
        "pending.todo": (
            "„Jetzt abrufen“ prüft ihn sofort.",
            "“Fetch now” checks it at once.",
        ),
        "unreconciled": (
            "Weicht ab: die gebuchten Bewegungen ergeben nicht den Saldo auf der Blockchain."
            " Hausbuch bucht keinen Ausgleich.",
            "Does not reconcile: the booked movements do not add up to the balance on the"
            " blockchain. Hausbuch books no balancing entry.",
        ),
        "unreconciled.todo": (
            "Die offenen Bewegungen unten prüfen; fehlt eine Transaktion, „Jetzt abrufen“.",
            "Check the open movements below; if a transaction is missing, “Fetch now”.",
        ),
        "coins_left": (
            "Coins haben die Wallet auf {name} verlassen: der Saldo ist kleiner als die"
            " verknüpften Einzahlungen. Bewegungen liest Hausbuch auf dieser Chain nicht.",
            "Coins have left the wallet on {name}: the balance is smaller than the linked"
            " deposits. Hausbuch does not read movements on this chain.",
        ),
        "coins_left.todo": (
            "„Jetzt abrufen“ prüft den Saldo erneut. Wohin die Coins gingen, lässt sich hier noch"
            " nicht beantworten.",
            "“Fetch now” checks the balance again. Where the coins went cannot be answered here"
            " yet.",
        ),
        "groups.one": (
            "„{label}“: {n} Transaktion der Blockchain warten auf Bestätigung oder darauf, dass"
            " Sie sagen, was sie waren.",
            "“{label}”: {n} blockchain transaction is waiting for confirmation or for you to say"
            " what it was.",
        ),
        "groups.many": (
            "„{label}“: {n} Transaktionen der Blockchain warten auf Bestätigung oder darauf, dass"
            " Sie sagen, was sie waren.",
            "“{label}”: {n} blockchain transactions are waiting for confirmation or for you to say"
            " what they were.",
        ),
        "groups.question": (
            "Auf der Seite der Wallet bestätigen oder beantworten.",
            "Confirm or answer on the wallet's page.",
        ),
        "change.question": (
            "Für jede Adresse „Ja, eigene Adresse“ oder „Nein, Empfänger“ wählen.",
            "For each address choose “Yes, own address” or “No, recipient”.",
        ),
        "group.question": (
            "Auf der Seite der Wallet ansehen.",
            "View on the wallet's page.",
        ),
        "labelled": (
            "„{label}“: {text}",
            "“{label}”: {text}",
        ),
        "labelled.addresses": (
            "„{label}“ ({masked}): {text}",
            "“{label}” ({masked}): {text}",
        ),
    },
)
_TYPED = {"HAUSBUCH_ETHERSCAN_KEY", "HAUSBUCH_SOLANA_RPC_URL", "HAUSBUCH_BSC_RPC_URL"}


def _where(var: str, verb: str) -> str:
    """Where to enter, check or change a source's setting (`verb`): the app first when it
    can be typed there (D260), else the console."""
    return TEXTS(f"where.{'app' if var in _TYPED else 'console'}.{verb}", var=var)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


@dataclass(frozen=True)
class _Probe:
    """The chain's source as configured now: None when it builds, else the state it shows."""

    state: WalletAddressState | None
    scope: str  # "address" or "account" (D186 (2), Bitcoin reconciles per account)


_PROBE_STATES = {WALLET_KEY_MISSING: S.KEY_MISSING, WALLET_KEY_UNREADABLE: S.KEY_UNREADABLE}


def _probe(chain: Chain, settings: Settings, conn: Connection) -> _Probe:
    """Build the chain's source (no request is made) to see a missing key or a source not
    built yet before any run, with the workspace's keys typed in the app (D260); the
    exception's text is never passed on."""
    try:
        source = source_for(chain, settings, conn=conn)
    except ChainSourceError as exc:
        state = _PROBE_STATES.get(exc.code, S.SOURCE_UNAVAILABLE)
        return _Probe(state, "address")
    except NotImplementedError:
        return _Probe(S.SOURCE_UNAVAILABLE, "address")
    return _Probe(None, reconcile_scope(source))


def _balance_only(chain: Chain) -> bool:
    return registry().chain(chain).source is ChainSourceKind.EVM_RPC_BALANCE


def _setting(chain: Chain) -> str:
    return SOURCE_SETTING[registry().chain(chain).source]


def _texts(state: WalletAddressState, chain: Chain, error_code: str | None) -> tuple:
    """(text, todo, setting) in plain words for an address's state (principle 7)."""
    name = CHAIN_NAMES[chain]
    var = _setting(chain)
    match state:
        case S.RETIRED:
            return (TEXTS("retired"), None, None)
        case S.KEY_MISSING:
            return (
                TEXTS("key_missing", name=name),
                TEXTS("key_missing.todo", where=_where(var, "enter")),
                var,
            )
        case S.KEY_UNREADABLE:
            return (TEXTS("key_unreadable", name=name), TEXTS("key_unreadable.todo"), var)
        case S.SOURCE_UNAVAILABLE:
            return (TEXTS("source_unavailable", name=name), TEXTS("nothing"), None)
        case S.NOT_SYNCED:
            return (TEXTS("not_synced"), TEXTS("not_synced.todo"), None)
        case S.FAILED if error_code == WALLET_SYNC_ERROR:
            return (TEXTS("sync_error"), TEXTS("sync_error.todo"), None)
        case S.FAILED:
            return (TEXTS("failed"), TEXTS("failed.todo"), None)
        case S.THROTTLED:
            return (
                TEXTS("throttled"),
                TEXTS("throttled.todo", where=_lower_first(_where(var, "change"))),
                var,
            )
        case S.REFUSED:
            return (TEXTS("refused"), TEXTS("refused.todo", where=_where(var, "check")), var)
        case S.DEFERRED:
            return (TEXTS("deferred"), TEXTS("deferred.todo"), None)
        case S.RECONCILED if _balance_only(chain):
            return (TEXTS("reconciled.balance_only", name=name), None, None)
        case S.RECONCILED:
            return (TEXTS("reconciled"), None, None)
        case S.WAITING:
            return (TEXTS("waiting"), TEXTS("waiting.todo"), None)
        case S.PENDING:
            return (TEXTS("pending"), TEXTS("pending.todo"), None)
        case S.UNRECONCILED:
            return (TEXTS("unreconciled"), TEXTS("unreconciled.todo"), None)
        case S.COINS_LEFT:
            return (TEXTS("coins_left", name=name), TEXTS("coins_left.todo"), None)
    raise AssertionError(state)


def _last_runs(conn: Connection, account_id: UUID) -> dict[UUID, object]:
    """Each address's newest finished wallet_sync row (any status, a deferred run included)."""
    w = sync_table.c
    return {
        r.address_id: r
        for r in conn.execute(
            select(sync_table)
            .distinct(w.address_id)
            .where(w.account_id == account_id, w.finished_at.is_not(None))
            .order_by(w.address_id, w.started_at.desc(), w.created_at.desc())
        )
    }


def _last_balances(conn: Connection, account_id: UUID) -> dict[UUID, object]:
    """Each address's newest run that stored balances (its holdings at that block)."""
    w = sync_table.c
    return {
        r.address_id: r
        for r in conn.execute(
            select(sync_table)
            .distinct(w.address_id)
            .where(
                w.account_id == account_id,
                w.status == WalletSyncStatus.SUCCEEDED.value,
                w.balances.is_not(None),
            )
            .order_by(w.address_id, w.started_at.desc(), w.created_at.desc())
        )
    }


def open_groups(conn: Connection, account_id: UUID) -> dict[Chain, tuple[int, int]]:
    """Per chain, (open groups: proposed or holding a proposed leg, those with a question)."""
    from .wallet_groups import open_clause  # it reads this module's neighbours

    g = group_table.c
    rows = conn.execute(
        select(
            g.chain,
            func.count(),
            func.count().filter(g.question.is_not(None)),
        )
        .where(g.account_id == account_id, open_clause())
        .group_by(g.chain)
    ).all()
    return {Chain(chain): (n, asked) for chain, n, asked in rows}


def wallet_state(conn: Connection, account_id: UUID, settings: Settings) -> WalletState:
    """GET /wallets/{id}/state: each address's state in plain words with what to do, the
    holdings per asset (the chain's balance at the last read beside what was booked), and
    the open groups per chain. The caller checked the account is a wallet."""
    a = address_table.c
    addresses = conn.execute(
        select(a.id, a.chain, a.address_masked, a.active)
        .where(a.account_id == account_id)
        .order_by(a.created_at, a.id)
    ).all()
    runs = _last_runs(conn, account_id)
    with_balances = _last_balances(conn, account_id)
    opened = open_groups(conn, account_id)
    probes = {chain: _probe(chain, settings, conn) for chain in {Chain(r.chain) for r in addresses}}
    chain_of = {r.id: Chain(r.chain) for r in addresses}
    masked_of = {r.id: r.address_masked for r in addresses}

    # An account-scope chain's verdict is its newest run with balances over its addresses:
    # every such run carries the account's balances (D186 (2), sync._balances).
    account_runs: dict[Chain, object] = {}
    for address_id, run in with_balances.items():
        chain = chain_of.get(address_id)
        if chain is None or probes[chain].scope != "account":
            continue
        current = account_runs.get(chain)
        if current is None or (run.started_at, run.created_at) > (
            current.started_at,
            current.created_at,
        ):
            account_runs[chain] = run

    statuses = []
    for r in addresses:
        chain = Chain(r.chain)
        probe = probes[chain]
        run = runs.get(r.id)
        account_scope = probe.scope == "account"
        waiting = opened.get(chain, (0, 0))[1] > 0
        if not r.active:
            state = S.RETIRED
        elif probe.state is not None:
            state = probe.state
        elif run is None:
            state = S.NOT_SYNCED
        elif run.status == WalletSyncStatus.FAILED.value:
            state = S.FAILED
        elif run.status == WalletSyncStatus.THROTTLED.value:
            state = S.THROTTLED
        elif run.status == WalletSyncStatus.REFUSED.value:
            state = S.REFUSED
        elif run.balances is None:
            state = S.DEFERRED
        else:
            decides = account_runs.get(chain, run) if account_scope else run
            state = _verdict_of(decides, chain, waiting=waiting)
        text, todo, setting = _texts(state, chain, run.error_code if run is not None else None)
        statuses.append(
            WalletAddressStatus(
                address_id=r.id,
                chain=chain,
                address_masked=r.address_masked,
                state=state,
                text=text,
                todo=todo,
                setting=setting,
                error_code=run.error_code if run is not None else None,
                finished_at=run.finished_at if run is not None else None,
                block=run.block if run is not None else None,
                movements_new=run.movements_new if run is not None else None,
            )
        )

    holdings: list[WalletHolding] = []
    assets = _assets(conn, {chain_of[i] for i in with_balances if i in chain_of})
    seen_account: set[Chain] = set()
    for r in addresses:
        run = with_balances.get(r.id)
        chain = Chain(r.chain)
        if run is None or not r.active:
            continue
        account_scope = probes[chain].scope == "account"
        if account_scope:
            if chain in seen_account:
                continue
            seen_account.add(chain)
            run = account_runs[chain]
        state = _verdict_of(run, chain, waiting=opened.get(chain, (0, 0))[1] > 0)
        holdings.extend(
            _holdings(
                run,
                chain,
                None if account_scope else r.id,
                None if account_scope else masked_of[r.id],
                state,
                assets,
            )
        )
    return WalletState(
        statuses=statuses,
        holdings=holdings,
        open_groups=sum(n for n, _ in opened.values()),
        open_questions=sum(asked for _, asked in opened.values()),
    )


def _verdict_of(run, chain: Chain, *, waiting: bool) -> WalletAddressState:
    """A run with balances: its reconciliation as a state (D186 (2)). A run that stored no
    verdict either waited for an answer (an open question names the difference) or left it
    to the account's last address; once the answer is given, the next run decides."""
    if run.reconciled is True:
        return S.RECONCILED
    if run.reconciled is False:
        return S.COINS_LEFT if _balance_only(chain) else S.UNRECONCILED
    return S.WAITING if waiting else S.PENDING


@dataclass(frozen=True)
class _Asset:
    id: UUID | None
    label: str
    decimals: int


def _assets(conn: Connection, chains: set[Chain]) -> dict[tuple[Chain, str | None], _Asset]:
    if not chains:
        return {}
    t = asset_table.c
    return {
        (Chain(r.chain), r.contract_address): _Asset(r.id, r.symbol or r.name, r.decimals)
        for r in conn.execute(
            select(t.id, t.chain, t.contract_address, t.symbol, t.name, t.decimals).where(
                t.chain.in_([c.value for c in chains])
            )
        )
    }


def _asset_of(assets: dict, chain: Chain, key: str) -> _Asset:
    contract = None if key == "native" else key
    found = assets.get((chain, contract))
    if found is not None:
        return found
    if contract is None:
        row = registry().chain(chain)
        return _Asset(None, row.native_symbol, row.native_decimals)
    token = asset_registry().token(chain, contract)
    if token is not None:
        return _Asset(None, token.symbol, token.decimals)
    return _Asset(None, f"Token {contract[:6]}…{contract[-4:]}", 0)


def _units(base: int, decimals: int) -> Decimal:
    return Decimal(base).scaleb(-decimals)


def _holdings(
    run,
    chain: Chain,
    address_id: UUID | None,
    masked: str | None,
    state: WalletAddressState,
    assets: dict,
) -> list[WalletHolding]:
    """One row per asset the run read a balance of or found a difference in. Booked = the
    balance less the stored difference (difference = balance − Σ movements); a run that
    waited for an answer stored none, so booked stays empty. A balance-only address books
    only its linked deposits, so its booked figure is shown only on a shortfall."""
    balances = {k: int(v) for k, v in (run.balances or {}).items()}
    difference = {k: int(v) for k, v in (run.difference or {}).items()}
    balance_only = _balance_only(chain)
    out = []
    for key in sorted(set(balances) | set(difference), key=lambda k: (k != "native", k)):
        asset = _asset_of(assets, chain, key)
        have = balances.get(key, 0)
        if state in (S.WAITING, S.PENDING) or (balance_only and key not in difference):
            booked = None
        else:
            booked = _units(have - difference.get(key, 0), asset.decimals)
        out.append(
            WalletHolding(
                chain=chain,
                address_id=address_id,
                address_masked=masked,
                asset_id=asset.id,
                symbol=asset.label,
                chain_units=_units(have, asset.decimals),
                booked_units=booked,
                state=state
                if key in difference or state not in (S.UNRECONCILED, S.COINS_LEFT)
                else S.RECONCILED,
                at_block=run.block,
                read_at=run.finished_at,
            )
        )
    return out


__all__ = [
    "CHAIN_NAMES",
    "SOURCE_SETTING",
    "open_groups",
    "wallet_state",
]


# --- Zu prüfen ----------------------------------------------------------------------------

# The address states a person must act on, and the card each one becomes. Deferred, waiting,
# pending, not yet synced and a source Hausbuch cannot read yet are shown on the page only; so
# are a throttled run and a run that failed inside Hausbuch (wallet.sync_error): the next run
# retries both, and no click of the person changes either (review of PR #398, finding 6).
_SYNC_CARDS = {
    S.KEY_MISSING: InboxItemKind.WALLET_SYNC,
    S.KEY_UNREADABLE: InboxItemKind.WALLET_SYNC,
    S.FAILED: InboxItemKind.WALLET_SYNC,
    S.REFUSED: InboxItemKind.WALLET_SYNC,
    S.UNRECONCILED: InboxItemKind.WALLET_UNRECONCILED,
    S.COINS_LEFT: InboxItemKind.WALLET_COINS_LEFT,
}


def wallet_inbox_items(conn: Connection, settings: Settings, now: datetime) -> list[InboxItem]:
    """The wallet cards of Zu prüfen (review.inbox's `wallets` section): per wallet, one card
    for its proposed groups without a question, one card per change card and per other group
    a person must look at (a transaction Hausbuch cannot store is shown on the page only), and
    one card per address state that needs a person, the addresses of one state on one card.
    No full address or transaction hash (D179). `now` is not read: a state card is dated by
    its addresses' latest finished run, an address without one (a key missing before any run)
    by the time it was added, a stored time, so the answer's bytes do not change with the
    clock (phase 10 §7.5 (b))."""
    from .wallet_groups import proposed_counts, wallet_group_cards  # it imports this module

    items: list[InboxItem] = []
    labels = dict(
        conn.execute(
            select(account_table.c.id, account_table.c.label).where(
                account_table.c.kind == AccountKind.WALLET.value
            )
        ).all()
    )
    for account_id, (n, newest) in proposed_counts(conn).items():
        label = labels.get(account_id, "Wallet")
        items.append(
            InboxItem(
                kind=InboxItemKind.WALLET_GROUPS,
                created_at=newest,
                reasoning=TEXTS("groups.one" if n == 1 else "groups.many", label=label, n=n),
                question=TEXTS("groups.question"),
                account_id=account_id,
            )
        )
    for card in wallet_group_cards(conn):
        if card.card is WalletCardKind.UNSTORABLE:
            continue
        change = card.card is WalletCardKind.CHANGE
        items.append(
            InboxItem(
                kind=InboxItemKind.WALLET_CHANGE if change else InboxItemKind.WALLET_QUESTION,
                created_at=card.occurred_at,
                reasoning=TEXTS(
                    "labelled", label=labels.get(card.account_id, "Wallet"), text=card.text
                ),
                question=TEXTS("change.question")
                if change
                else (card.question or TEXTS("group.question")),
                account_id=card.account_id,
                group_id=card.id,
            )
        )
    a = address_table.c
    added = dict(
        conn.execute(select(a.id, a.created_at).where(a.account_id.in_(list(labels)))).all()
    )
    for account_id, label in labels.items():
        state = wallet_state(conn, account_id, settings)
        by_state: dict[WalletAddressState, list[WalletAddressStatus]] = {}
        for status in state.statuses:
            if status.state is S.FAILED and status.error_code == WALLET_SYNC_ERROR:
                continue
            if status.state in _SYNC_CARDS:
                by_state.setdefault(status.state, []).append(status)
        for address_state, statuses in by_state.items():
            first = statuses[0]
            masked = ", ".join(f"{CHAIN_NAMES[s.chain]} {s.address_masked}" for s in statuses)
            items.append(
                InboxItem(
                    kind=_SYNC_CARDS[address_state],
                    created_at=max(s.finished_at or added[s.address_id] for s in statuses),
                    reasoning=TEXTS(
                        "labelled.addresses", label=label, masked=masked, text=first.text
                    ),
                    question=first.todo,
                    account_id=account_id,
                    error_code=address_state.value,
                )
            )
    return items
