"""Phase 7: the chain sources and the sync (docs/phases/phase-7.md D181, D182, D192, §5).

`base`: the ChainSource protocol's home and the source for a chain (step 1 reads the
settings, steps 2 to 4 add one module per chain family: `solana_rpc`, `evm_etherscan`,
`evm_rpc_balance`, `bitcoin_esplora`). Every module turns the provider's JSON into ChainTx
rows or Solana deltas over the HTTP client of phase 6 step 5b (services/prices/http) and
knows nothing of movements.
"""
