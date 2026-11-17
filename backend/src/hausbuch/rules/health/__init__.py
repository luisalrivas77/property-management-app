"""Phase 9: the household's key figures and their marks (docs/phases/phase-9.md §4, §5).

- `window`: closed months and the window of up to 12 (D231), step 1.
- `month`: one household month from the private and rental lines (§4, D230–D236), step 1.
- `figures`: savings rate, reserve, debt, the split and the net worth series (§5.1–§5.4),
  step 1.
- `bands`: the marks against the household's confirmed limits (D250, §5.6), step 1;
  `bands.yaml` holds the proposed limits, their origins and the sentence codes.

Every function is pure and computes on read (D230). Step 0 writes the data and the stubs; each
stub raises until its step.
"""
