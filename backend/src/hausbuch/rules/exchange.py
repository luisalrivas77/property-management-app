"""The exchange's pure rules (docs/phases/phase-6.md D166, D175, §8; fixture
tests/fixtures/phase6/bison_csv.yaml). Built in phase 6 step 5.

- `classify(line)`: a parsed export row (MovementLine) to its movement kind; a crypto
  Deposit is proposed as a cashback_reward, anything unknown is uncategorized.
- `movement_hash(account_id, line, position)`: the dedup key of a row, the export's
  Transaction ID when it has one, else the row's content and its position among identical
  rows (D22's fallback), so two identical rows both survive and a re-import adds nothing.
"""

from uuid import UUID

from hausbuch.schemas.household import MovementKind, MovementLine

STEP = "phase 6 step 5 (the exchange)"


def classify(line: MovementLine) -> MovementKind:
    raise NotImplementedError(STEP)


def movement_hash(account_id: UUID, line: MovementLine, position: int) -> str:
    raise NotImplementedError(STEP)
