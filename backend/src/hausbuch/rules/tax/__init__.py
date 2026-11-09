"""Phase 3 tax rules (docs/phases/phase-3.md §5): pure functions over facts and a rule set.

Nothing here reads the database or calls a model (principle 1). The rule set of each year is
data in `rulesets/<year>.yaml`, loaded by `registry.rule_set` (principle 5).
"""
