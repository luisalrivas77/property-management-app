"""Pure domain rules: deterministic functions over schema objects.

No I/O, no database, no clock, no randomness, no model calls (principle 1). Money and rates
are Decimal. Rules that can fail raise RuleError (hausbuch.rules.errors) with a stable code;
the contracts are in docs/phases/phase-1.md ("Rule contracts") and tests/fixtures/phase1/.
"""
