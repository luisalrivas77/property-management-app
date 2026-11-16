"""A fake Solana chain behind the JSON-RPC transport (services/chain/solana_rpc.Post), for the
SolanaSource tests and the Solana sync test.

It holds recorded `getTransaction` records (tests/fixtures/phase7/solana_tx.yaml, every key
and signature invented) and answers the four methods a sync sends as a provider does:
`getSignaturesForAddress` lists the records whose account keys hold the account, newest first,
paged with `limit` and `before`; `getTransaction` serves a record by signature (None when the
chain has none); `getBalance` and `getTokenAccountsByOwner` answer from the balances the test
sets. A `status` makes every answer that HTTP status; `fail_on` turns one method into a
JSON-RPC error. Every request is recorded, with the URL it went to."""

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

    def _getBlockTime(self, slot):  # noqa: N802
        return self.block_times.get(slot)

    def _getTokenAccountsByOwner(self, owner, program, options):  # noqa: N802
        value = [
            e for e in self.tokens.get(owner, []) if e["account"]["owner"] == program["programId"]
        ]
        return {"context": {"slot": SLOT}, "value": value}
