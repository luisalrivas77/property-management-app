"""Phase 9: the household plan (docs/phases/phase-9.md §6, §7).

- `surplus`: free cash and „Anlegbar pro Monat“ (D244, §6.1), step 2.
- `savings_plans`: plans detected from the transfers, and the household's overrides (D245,
  §6.2), step 2.
- `levers`: the month function of §7.1 under the scenario's levers (D247, D248), step 2.
- `paths`: the paths to the reserve target, none recommended (D246, §7.2), step 2.
- `projection`: the net worth in some years on the household's typed rates (D247 amended,
  §7.5), step 2.

Every function is pure and computes no tax (D247); step 2 built them (the services read them
through services/plan.py).
"""
