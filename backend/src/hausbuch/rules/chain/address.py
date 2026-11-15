"""Address validation and the mask (docs/phases/phase-7.md D179, §4); built in step 1.

`validate` returns the normalised form (EVM: EIP-55; Bitcoin: bech32 lower case or the
base58check form as written; Solana: base58 of 32 bytes) or raises RuleError
`wallet.address_invalid` naming the chain's expected shape in plain words. `mask` gives the
stored, logged and shown form (the first 3 or 4 characters, „…“, the last 4; migration 0041's
CHECK). Fixture tests/fixtures/phase7/chains.yaml `addresses`.
"""

from hausbuch.schemas.chain import Chain


def validate(chain: Chain, address: str) -> str:
    raise NotImplementedError("phase 7 step 1")


def mask(chain: Chain, address: str) -> str:
    raise NotImplementedError("phase 7 step 1")
