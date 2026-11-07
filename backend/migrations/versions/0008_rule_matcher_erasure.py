"""Phase 2 step 2: a disabled learned rule may hold an empty matcher (tenant erasure).

Spec: docs/phases/phase-2.md D42 (3). Erasing a tenant disables every learned rule whose
matcher holds the tenant's IBAN hash or which is scoped to one of their tenancies, and nulls
the matcher's IBAN hash and name and purpose patterns. 0007's CHECK on matching_rule.matcher
required at least one of the three on every row, so that scrub could not be stored. This
revision keeps every other part of the CHECK (only the four keys, each a JSON string when
present, the amount sign's values, the hash's form) and requires "at least one of the three"
only while the rule is enabled: an enabled rule must still match on something, and a
disabled rule cannot be switched back on with an empty matcher.

The CHECK moves from a column constraint to a table constraint (it now reads `enabled`);
it keeps its name, matching_rule_matcher_check. Runs as the schema owner like every
migration; no grant changes (the app role already has UPDATE on matching_rule).

The downgrade restores 0007's CHECK and is refused while a disabled rule has an empty
matcher: those rows would violate it, and a downgrade never rewrites data.

Revision ID: 0008_rule_matcher_erasure
Revises: 0007_allocations_rules
Create Date: 2026-11-07
"""

from alembic import op

revision = "0008_rule_matcher_erasure"
down_revision = "0007_allocations_rules"
branch_labels = None
depends_on = None

CONSTRAINT = "matching_rule_matcher_check"
MATCHER_KEYS = (
    "'counterparty_iban_hmac', 'counterparty_name_pattern', 'purpose_pattern', 'amount_sign'"
)
# As in 0007: every matcher value is a JSON string when present (null counts as absent).
MATCHER_TYPED = "\n        AND ".join(
    f"(matcher ->> {key} IS NULL OR jsonb_typeof(matcher -> {key}) = 'string')"
    for key in MATCHER_KEYS.split(", ")
)
MATCHES_SOMETHING = """num_nonnulls(matcher ->> 'counterparty_iban_hmac',
                     matcher ->> 'counterparty_name_pattern',
                     matcher ->> 'purpose_pattern') >= 1"""


def _check(matches_something: str) -> str:
    return f"""
ALTER TABLE matching_rule DROP CONSTRAINT {CONSTRAINT};
ALTER TABLE matching_rule ADD CONSTRAINT {CONSTRAINT} CHECK (
    jsonb_typeof(matcher) = 'object'
        AND matcher - ARRAY[{MATCHER_KEYS}] = '{{}}'::jsonb
        AND {MATCHER_TYPED}
        AND {matches_something}
        AND (matcher ->> 'amount_sign' IS NULL
             OR matcher ->> 'amount_sign' IN ('credit', 'debit'))
        AND (matcher ->> 'counterparty_iban_hmac' IS NULL
             OR matcher ->> 'counterparty_iban_hmac' ~ '^[0-9a-f]{{64}}$'));
"""


REFUSE_EMPTY_MATCHERS = f"""
DO $$
BEGIN
    ALTER TABLE matching_rule NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM matching_rule WHERE NOT ({MATCHES_SOMETHING})) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a disabled rule has an empty matcher'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'A tenant erasure emptied them; export and remove them first.';
    END IF;
    ALTER TABLE matching_rule FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(_check(f"(NOT enabled OR {MATCHES_SOMETHING})"))


def downgrade() -> None:
    op.execute(REFUSE_EMPTY_MATCHERS)
    op.execute(_check(MATCHES_SOMETHING))
