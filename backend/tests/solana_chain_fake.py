"""A fake Solana chain behind the JSON-RPC transport (services/chain/solana_rpc.Post), for the
SolanaSource tests and the Solana sync test.

It holds recorded `getTransaction` records (tests/fixtures/phase7/solana_tx.yaml, every key
and signature invented) and answers the methods a sync sends as a provider does:
`getSignaturesForAddress` lists the records whose account keys hold the account, newest first,
paged with `limit` and `before`; `getTransaction` serves a record by signature (None when the
chain has none); `getBalance` and `getTokenAccountsByOwner` answer from the balances the test
sets; `getSlot` names the highest record's slot (or `finalized`). A `status` makes every
answer that HTTP status; `fail_on` turns one method into a JSON-RPC error. Every request is
recorded, with the URL it went to."""

import json
from copy import deepcopy

from hausbuch.rules.chain import solana

SLOT = 300_009_999  # the slot every balance is read at


def signature(record: dict) -> str:
    return record["transaction"]["signatures"][0]


def keys(record: dict) -> list[str]:
    return [
        k["pubkey"] if isinstance(k, dict) else k
        for k in record["transaction"]["message"]["accountKeys"]
    ]


def token_account(owner, pubkey, mint, units, lamports, program=solana.TOKEN_PROGRAM, decimals=6):
    """One `getTokenAccountsByOwner` entry as the RPC's jsonParsed answer shapes it."""
    return {
        "pubkey": pubkey,
        "account": {
            "lamports": lamports,
            "owner": program,
            "executable": False,
            "rentEpoch": 18446744073709551615,
            "space": 165,
            "data": {
                "program": "spl-token",
                "parsed": {
                    "type": "account",
                    "info": {
                        "mint": mint,
                        "owner": owner,
                        "isNative": False,
                        "state": "initialized",
                        "tokenAmount": {"amount": str(units), "decimals": decimals},
                    },
                },
                "space": 165,
            },
        },
    }


class FakeChain:
    def __init__(self, records=()) -> None:
        self.records: dict[str, dict] = {}
        self.lamports: dict[str, int] = {}
        self.tokens: dict[str, list[dict]] = {}  # owner -> token_account entries
        self.status: int | None = None
        self.fail_on: str | None = None
        self.missing: set[str] = set()  # listed, but getTransaction answers null (Q-O93)
        self.requests: list[dict] = []
        self.urls: list[str] = []
        self.block_times: dict[int, int] = {}  # getBlockTime by slot (absent: the RPC has none)
        self.finalized: int | None = None  # getSlot (None: the highest record's slot)
        for record in records:
            self.add(record)

    def add(self, record: dict) -> None:
        self.records[signature(record)] = deepcopy(record)

    # --- the transport (solana_rpc.Post) ---------------------------------------------------

    def __call__(self, url, headers, body, timeout, max_bytes):
        request = json.loads(body)
        self.requests.append(request)
        self.urls.append(url)
        if self.status is not None:
            return self.status, b""
        method, params = request["method"], request["params"]
        if method == self.fail_on:
            answer = {"error": {"code": -32603, "message": "Internal error"}}
        else:
            answer = {"result": getattr(self, "_" + method)(*params)}
        return 200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode()

    def calls(self, method: str) -> list[list]:
        return [r["params"] for r in self.requests if r["method"] == method]

    # --- the methods -----------------------------------------------------------------------

    def _getSignaturesForAddress(self, account, options):  # noqa: N802 - the RPC's name
        listed = sorted(
            (r for r in self.records.values() if account in keys(r)),
            key=lambda r: (r["slot"], signature(r)),
            reverse=True,
        )
        before = options.get("before")
        if before is not None:
            at = [signature(r) for r in listed].index(before)
            listed = listed[at + 1 :]
        return [
            {
                "signature": signature(r),
                "slot": r["slot"],
                "err": (r["meta"] or {}).get("err"),
                "memo": None,
                "blockTime": r["blockTime"],
                "confirmationStatus": "finalized",
            }
            for r in listed[: options["limit"]]
        ]

    def _getTransaction(self, sig, options):  # noqa: N802
        record = self.records.get(sig)
        return deepcopy(record) if record is not None and sig not in self.missing else None

    def _getBalance(self, address, options):  # noqa: N802
        return {"context": {"slot": SLOT}, "value": self.lamports.get(address, 0)}

    def _getSlot(self, options):  # noqa: N802
        if self.finalized is not None:
            return self.finalized
        return max((r["slot"] for r in self.records.values()), default=0)

    def _getBlockTime(self, slot):  # noqa: N802
        return self.block_times.get(slot)

    def _getTokenAccountsByOwner(self, owner, program, options):  # noqa: N802
        value = [
            e for e in self.tokens.get(owner, []) if e["account"]["owner"] == program["programId"]
        ]
        return {"context": {"slot": SLOT}, "value": value}


class Records:
    """Invented records for long histories (the budget and cursor tests): `owner` sends and
    receives SOL, and token accounts it owns are opened, credited without naming it (a credit
    names the token account alone), debited and closed. Signatures are numbered."""

    PAYER = "ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J"
    PAYER_TOKEN = "41frPyR5ATwWweP1VzSdHDY82VbWy3Dt6mViHk9a1nD4"
    MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    RENT = 2_039_280

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.count = 0

    def _row(self, index: int, owner: str | None, amount: int) -> dict:
        row = {
            "accountIndex": index,
            "mint": self.MINT,
            "uiTokenAmount": {"amount": str(amount), "decimals": 6},
        }
        if owner is not None:
            row["owner"] = owner
        return row

    def _record(self, slot, tag, keys, program, balances, rows=((), ()), block_time=True):
        self.count += 1
        pre, post = balances
        return {
            "slot": slot,
            "blockTime": 1_770_000_000 + slot if block_time else None,
            "transaction": {
                "signatures": [f"{tag}{self.count:05d}".ljust(88, "x")],
                "message": {
                    "accountKeys": [
                        {"pubkey": k, "signer": i == 0, "writable": True, "source": "transaction"}
                        for i, k in enumerate(keys)
                    ],
                    "instructions": [{"programId": program, "parsed": {"type": "x"}}],
                },
            },
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": list(pre),
                "postBalances": list(post),
                "preTokenBalances": list(rows[0]),
                "postTokenBalances": list(rows[1]),
                "innerInstructions": [],
            },
        }

    def sol_in(self, slot: int) -> dict:
        keys = [self.PAYER, self.owner, solana.SYSTEM_PROGRAM]
        balances = ([10**10, 10**9, 1], [10**10 - 1_005_000, 10**9 + 1_000_000, 1])
        return self._record(slot, "SOLIN", keys, solana.SYSTEM_PROGRAM, balances)

    def sol_out(self, slot: int) -> dict:
        keys = [self.owner, self.PAYER, solana.SYSTEM_PROGRAM]
        balances = ([10**9, 10**10, 1], [10**9 - 1_005_000, 10**10 + 1_000_000, 1])
        return self._record(slot, "SOLOUT", keys, solana.SYSTEM_PROGRAM, balances)

    def token_in(self, slot: int, account: str, block_time: bool = True) -> dict:
        """5 units credited to `account` by a third party; the owner is not a key."""
        keys = [self.PAYER, self.PAYER_TOKEN, account, solana.TOKEN_PROGRAM]
        rows = (
            [self._row(1, self.PAYER, 100), self._row(2, self.owner, 10)],
            [self._row(1, self.PAYER, 95), self._row(2, self.owner, 15)],
        )
        balances = ([10**9, self.RENT, self.RENT, 1], [10**9 - 5000, self.RENT, self.RENT, 1])
        return self._record(slot, "TOKIN", keys, solana.TOKEN_PROGRAM, balances, rows, block_time)

    def token_out(self, slot: int, account: str) -> dict:
        """5 units sent from `account`, signed by the owner."""
        keys = [self.owner, account, self.PAYER_TOKEN, solana.TOKEN_PROGRAM]
        rows = (
            [self._row(1, self.owner, 10), self._row(2, self.PAYER, 100)],
            [self._row(1, self.owner, 5), self._row(2, self.PAYER, 105)],
        )
        balances = ([10**9, self.RENT, self.RENT, 1], [10**9 - 5000, self.RENT, self.RENT, 1])
        return self._record(slot, "TOKOUT", keys, solana.TOKEN_PROGRAM, balances, rows)

    def open(self, slot: int, account: str, payer: str | None = None) -> dict:
        """`account` opened for the owner, paid by the owner (or by a third party `payer`,
        whose record then does not name the owner)."""
        keys = [payer or self.owner, account, solana.SYSTEM_PROGRAM, solana.TOKEN_PROGRAM]
        balances = ([10**9, 0, 1, 1], [10**9 - 5000 - self.RENT, self.RENT, 1, 1])
        rows = ([], [self._row(1, self.owner, 0)])
        return self._record(slot, "OPEN", keys, solana.TOKEN_PROGRAM, balances, rows)

    def close(self, slot: int, account: str) -> dict:
        keys = [self.owner, account, solana.TOKEN_PROGRAM]
        balances = ([10**9, self.RENT, 1], [10**9 - 5000 + self.RENT, 0, 1])
        rows = ([self._row(1, self.owner, 0)], [])
        return self._record(slot, "CLOSE", keys, solana.TOKEN_PROGRAM, balances, rows)


POISONER = "Pois1nPayerXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"  # base58, invented


def dust_into_token_account(
    owner: str, token_account: str, *, slot: int = 300_000_401, lamports: int = 1
) -> dict:
    """A stranger's address-poisoning transaction (D186 (2) amended 18 Nov 2026): the
    stranger signs and pays, and two System transfers send `lamports` each to the owner's main
    account and to its own token account. No token balance row: no Token program runs, so the
    token account's lamports are the only trace of it in the record."""
    fee = 5000
    before = 10**9
    rent = Records.RENT
    keys = [POISONER, owner, token_account, solana.SYSTEM_PROGRAM]
    return {
        "version": 0,
        "slot": slot,
        "blockTime": 1_774_600_000,
        "transaction": {
            "signatures": ["Pois1nSig".ljust(88, "x")],
            "message": {
                "accountKeys": [
                    {"pubkey": k, "signer": i == 0, "writable": i < 3, "source": "transaction"}
                    for i, k in enumerate(keys)
                ],
                "instructions": [
                    {
                        "programId": solana.SYSTEM_PROGRAM,
                        "parsed": {
                            "type": "transfer",
                            "info": {"source": POISONER, "destination": to, "lamports": lamports},
                        },
                    }
                    for to in (owner, token_account)
                ],
            },
        },
        "meta": {
            "err": None,
            "fee": fee,
            "preBalances": [before, before, rent, 1],
            "postBalances": [before - fee - 2 * lamports, before + lamports, rent + lamports, 1],
            "preTokenBalances": [],
            "postTokenBalances": [],
            "innerInstructions": [],
            "loadedAddresses": {"writable": [], "readonly": []},
        },
    }
