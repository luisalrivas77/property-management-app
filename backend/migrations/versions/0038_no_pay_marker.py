"""Phase 4b: „Kein Arbeitslohn in <Jahr>“, the return_input kind `no_pay` (D195, 15 Nov 2026).

Luis, 15 Nov 2026 („Button on the to-do“): an owner on parental leave for a whole year had no
way to say so, so the to-do „Arbeitslohn <Jahr> fehlt“ stayed open and an expected gross pay
carried from an earlier year counted a salary that did not exist. `no_pay` is an owner-scoped
row, one per owner and year (a service rule), with an empty value and no document; it is never
confirmed from last year's row, so it joins the not-carried CHECK as well as the kind CHECK.
Neither the household CHECK (it has an owner) nor the document CHECK (it has no document)
changes.

The kind CHECK is 0018's inline column CHECK, named `return_input_kind_check`; the not-carried
CHECK is 0018's sixth unnamed table CHECK, which Postgres named `return_input_check5` (checked
on a database migrated from 0001; the names are deterministic for the same CREATE TABLE). Both
keep their names, so a downgrade restores 0018's exactly.

The downgrade is refused while a `no_pay` row exists (the 0025 guard), since the old CHECK
would refuse it and a downgrade never drops data. It lifts FORCE RLS for the check, since the
owner has no workspace set, and restores it (an exception rolls the whole statement back, FORCE
included).

Phase 7's spec reserved 0038 for its step 0; it moves to 0039.

Revision ID: 0038_no_pay_marker
Revises: 0037_car_and_wage_replacement
Create Date: 2026-11-15
"""

from alembic import op

revision = "0038_no_pay_marker"
down_revision = "0037_car_and_wage_replacement"
branch_labels = None
depends_on = None

# 0018's lists (hausbuch.schemas.enums.ReturnInputKind and NOT_CARRIED there), in its order.
OLD_KINDS = (
    "wage_certificate",
    "payslip",
    "commute",
    "home_office",
    "work_room",
    "work_equipment",
    "telecom",
    "professional_association",
    "training",
    "other_expense",
    "other_insurance",
    "replacement_income",
    "vorsorge_override",
    "child",
    "childcare_cost",
    "household_services",
    "special_expense",
)
OLD_NOT_CARRIED = ("wage_certificate", "payslip", "replacement_income", "training", "child")
NEW_KIND = "no_pay"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_checks(kinds: tuple[str, ...], not_carried: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE return_input DROP CONSTRAINT return_input_kind_check")
    op.execute(
        "ALTER TABLE return_input ADD CONSTRAINT return_input_kind_check"
        f" CHECK (kind IN ({_sql_list(kinds)}))"
    )
    op.execute("ALTER TABLE return_input DROP CONSTRAINT return_input_check5")
    op.execute(
        "ALTER TABLE return_input ADD CONSTRAINT return_input_check5"
        f" CHECK (kind NOT IN ({_sql_list(not_carried)}) OR carried_from_id IS NULL)"
    )


REFUSE_IF_DATA = f"""DO $$
BEGIN
    ALTER TABLE return_input NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM return_input WHERE kind = '{NEW_KIND}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a return input of kind {NEW_KIND} exists'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE return_input FORCE ROW LEVEL SECURITY;
END
$$;"""


def upgrade() -> None:
    _set_checks((*OLD_KINDS, NEW_KIND), (*OLD_NOT_CARRIED, NEW_KIND))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_checks(OLD_KINDS, OLD_NOT_CARRIED)
