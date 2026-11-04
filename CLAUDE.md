# Hausbuch

Single-household landlord workspace (built SaaS-ready) that turns bank statements,
invoices and letters into a traceable property ledger, a draft Anlage V per tax year,
and a running profit view through to sale.

## Reference spec

The architecture spec below is the source of truth for scope, domain model, pipeline,
AI layer, tax rules, stack and delivery phases. Read it before designing or changing
anything, and check every change against its six architecture principles.

@docs/ARCHITECTURE.md

## Working rules

- If a change conflicts with the spec, stop and flag it rather than silently diverging.
- If a decision changes the architecture, update `docs/ARCHITECTURE.md` in the same PR
  (including the Decisions table).
- Each delivery phase starts with a short spec PR (schemas, rule fixtures, eval cases)
  before implementation code.
