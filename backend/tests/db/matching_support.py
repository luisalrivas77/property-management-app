"""A small landlord world for the step 4 matching tests (phase-2.md §8).

Master data goes through the services (so rent rows, IBAN hashing and events are real);
the account, the import and its lines are inserted directly as the app role, since an import
through services.statements needs a bank file. Documents get one extraction version of an AI
kind with source `manual`, which D37 treats like a reading.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from db.db_support import TEST_IBAN_KEY
from sqlalchemy import insert, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.schemas.enums import ActorType, ImportStatus
from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.schemas.tenancy import InitialRent, TenancyCreate, TenancyParty, TenantCreate
from hausbuch.services.context import Actor, new_command
from hausbuch.services.iban import protect_iban
from hausbuch.services.loans import create_loan
from hausbuch.services.properties import create_property
from hausbuch.services.tenancies import create_tenancy
from hausbuch.services.tenants import create_tenant
from hausbuch.services.units import create_unit

T0 = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
USER = Actor(ActorType.USER, "user:test")  # no app_user row: confirmed_by stays null
TENANT_IBAN = "DE89370400440532013000"
OTHER_TENANT_IBAN = "DE02120300000000202051"
LOAN_IBAN = "DE02500105170137075030"
OWNER_IBAN = "DE75512108001245126199"
PAINTER_IBAN = "DE12500105170648489890"
STADT_IBAN = "DE44500105175407324931"
WEG_IBAN = "DE88100900001234567892"
ACCOUNT_IBAN = "DE23100000000000000099"  # not the statement fixtures' IBAN


def ev(value: Any) -> dict:
    """An ExtractedValue as stored: typed by hand, so validates only."""
    if value is None:
        return {"value": None, "confidence": "low"}
    return {"value": value, "confidence": "high", "checks": ["validates"]}


class World:
    def __init__(self, engine, ws: uuid.UUID, *, now: datetime = T0) -> None:
        self.engine = engine
        self.ws = ws
        self.now = now
        self._lines = 0
        self.property_id = self.cmd(
            create_property,
            PropertyCreate(
                label="Wohnung Lindenstraße",
                kind="etw",
                address=Address(
                    street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"
                ),
                build_year=1994,
                notarized_on=date(2024, 3, 15),
                purchase_date=date(2024, 5, 1),
                purchase_price=Decimal("289000.00"),
            ),
        ).id
        self.unit_id = self.cmd(
            create_unit,
            UnitCreate(property_id=self.property_id, label="WE 3", floor_area_m2=Decimal("68.40")),
        ).id
        with workspace_transaction(engine, ws) as conn:
            self.account_id = self._account(conn, ACCOUNT_IBAN, owner=False)
            doc = self._document_row(conn, type="statement", subtype=None, title=None)
            self.import_id = conn.execute(
                insert(import_table)
                .values(
                    workspace_id=ws,
                    account_id=self.account_id,
                    document_id=doc,
                    format="camt",
                    status=ImportStatus.RECONCILED.value,
                    balance_source="file",
                    declared_closing=Decimal("0.00"),
                    computed_closing=Decimal("0.00"),
                    created_at=now,
                    updated_at=now,
                )
                .returning(import_table.c.id)
            ).scalar_one()

    # --- helpers -------------------------------------------------------------

    def cmd(self, command, *args, actor: Actor = USER, now: datetime | None = None, **kwargs):
        with workspace_transaction(self.engine, self.ws) as conn:
            ctx = new_command(conn, self.ws, actor, now=now or self.now)
            return command(ctx, *args, **kwargs)

    def query(self, query, *args, **kwargs):
        with workspace_transaction(self.engine, self.ws) as conn:
            return query(conn, *args, **kwargs)

    def _document_row(self, conn, *, type: str, subtype: str | None, title: str | None):
        sha = hashlib.sha256(uuid.uuid4().bytes).digest()
        conn.execute(
            insert(file_table).values(
                workspace_id=self.ws,
                sha256=sha,
                size_bytes=1,
                media_type="application/pdf",
                storage_key=f"test/{sha.hex()}",
                created_at=self.now,
            )
        )
        return conn.execute(
            insert(document_table)
            .values(
                workspace_id=self.ws,
                file_sha256=sha,
                source="upload",
                type=type,
                subtype=subtype,
                title=title,
                received_at=self.now,
                created_at=self.now,
                updated_at=self.now,
            )
            .returning(document_table.c.id)
        ).scalar_one()

    def _account(self, conn, iban: str, *, owner: bool) -> uuid.UUID:
        protected = protect_iban(iban, TEST_IBAN_KEY)
        return conn.execute(
            insert(account_table)
            .values(
                workspace_id=self.ws,
                label="Konto",
                bank="Sparkasse",
                iban_masked=protected.masked,
                iban_hmac=protected.hmac,
                opening_balance=Decimal("0.00"),
                opening_balance_on=date(2024, 1, 1),
                is_owner_account=owner,
                created_at=self.now,
                updated_at=self.now,
            )
            .returning(account_table.c.id)
        ).scalar_one()

    # --- master data ---------------------------------------------------------

    def owner_account(self, iban: str = OWNER_IBAN) -> uuid.UUID:
        with workspace_transaction(self.engine, self.ws) as conn:
            return self._account(conn, iban, owner=True)

    def tenancy(
        self,
        *,
        name: str = "Erika Mustermieterin",
        iban: str = TENANT_IBAN,
        cold: str = "1050.00",
        prepayment: str = "230.00",
        start: date = date(2024, 1, 1),
        unit_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        tenant = self.cmd(
            create_tenant, TenantCreate(full_name=name, iban=iban), iban_key=TEST_IBAN_KEY
        )
        return self.cmd(
            create_tenancy,
            TenancyCreate(
                unit_id=unit_id or self.unit_id,
                parties=[TenancyParty(tenant_id=tenant.id, role="primary")],
                start_date=start,
                rent_model="fixed",
            ),
            initial_rent=InitialRent(
                cold_rent=Decimal(cold), operating_costs_prepayment=Decimal(prepayment)
            ),
            iban_key=TEST_IBAN_KEY,
        ).id

    def loan(self, *, iban: str | None = LOAN_IBAN, **overrides: Any) -> uuid.UUID:
        values: dict[str, Any] = {
            "property_id": self.property_id,
            "lender": "Beispielbank AG",
            "principal": Decimal("300000.00"),
            "nominal_rate": Decimal("0.036"),
            "monthly_payment": Decimal("1400.00"),
            "disbursed_on": date(2024, 4, 1),
            "first_payment_on": date(2024, 5, 1),
            "fixed_until": date(2034, 4, 30),
            "account_iban": iban,
        }
        return self.cmd(create_loan, LoanCreate(**(values | overrides)), iban_key=TEST_IBAN_KEY).id

    def document(
        self,
        kind: str,
        fields: dict[str, Any],
        *,
        type: str = "invoice",
        subtype: str | None = None,
        title: str | None = None,
        link_property: bool = True,
        loan_ids: list[uuid.UUID] = (),  # type: ignore[assignment]
    ) -> uuid.UUID:
        with workspace_transaction(self.engine, self.ws) as conn:
            doc = self._document_row(conn, type=type, subtype=subtype, title=title)
            conn.execute(
                insert(extraction_table).values(
                    workspace_id=self.ws,
                    document_id=doc,
                    version=1,
                    source="manual",
                    kind=kind,
                    fields={name: ev(value) for name, value in fields.items()},
                    created_at=self.now,
                )
            )
            links = [{"property_id": self.property_id}] if link_property else []
            links += [{"loan_id": loan_id} for loan_id in loan_ids]
            for link in links:
                conn.execute(
                    insert(link_table).values(
                        workspace_id=self.ws, document_id=doc, created_at=self.now, **link
                    )
                )
        return doc

    def set_import_status(self, status: ImportStatus) -> None:
        with workspace_transaction(self.engine, self.ws) as conn:
            conn.execute(
                update(import_table)
                .where(import_table.c.id == self.import_id)
                .values(
                    status=status.value,
                    error_code=(
                        "statement.unreconciled"
                        if status in (ImportStatus.UNRECONCILED, ImportStatus.FAILED)
                        else None
                    ),
                )
            )

    def set_auto_accept_llm(self, value: bool) -> None:
        with workspace_transaction(self.engine, self.ws) as conn:
            conn.execute(update(workspace_table).values(auto_accept_llm=value))

    # --- bank lines ----------------------------------------------------------

    def line(
        self,
        booking_date: date,
        amount: str,
        *,
        iban: str | None = None,
        name: str | None = None,
        purpose: str = "",
        import_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        self._lines += 1
        protected = None if iban is None else protect_iban(iban, TEST_IBAN_KEY)
        with workspace_transaction(self.engine, self.ws) as conn:
            return conn.execute(
                insert(transaction_table)
                .values(
                    workspace_id=self.ws,
                    account_id=self.account_id,
                    import_id=import_id or self.import_id,
                    line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                    booking_date=booking_date,
                    value_date=booking_date,
                    amount=Decimal(amount),
                    counterparty_name=name,
                    counterparty_iban_masked=None if protected is None else protected.masked,
                    counterparty_iban_hmac=None if protected is None else protected.hmac,
                    purpose=purpose,
                    created_at=self.now,
                )
                .returning(transaction_table.c.id)
            ).scalar_one()
