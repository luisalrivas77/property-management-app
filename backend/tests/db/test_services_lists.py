"""The list queries the API's collection endpoints read (phase 1 step 3, §12 item 5):
list_owners, list_properties, list_tenants, list_tenancies. Pure reads under RLS."""

from datetime import UTC, date, datetime
from decimal import Decimal

from db.db_support import TEST_IBAN_KEY

from hausbuch.schemas.enums import PropertyKind, RentModel, TenancyRole
from hausbuch.schemas.property import Address, OwnerCreate, PropertyCreate, UnitCreate
from hausbuch.schemas.tenancy import TenancyCreate, TenancyParty, TenantCreate
from hausbuch.services.events import list_events
from hausbuch.services.owners import create_owner, get_owner, list_owners
from hausbuch.services.properties import create_property, get_property, list_properties
from hausbuch.services.tenancies import create_tenancy, get_tenancy, list_tenancies
from hausbuch.services.tenants import create_tenant, erase_tenant, get_tenant, list_tenants
from hausbuch.services.units import create_unit

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _property(label: str) -> PropertyCreate:
    return PropertyCreate(
        label=label,
        kind=PropertyKind.ETW,
        address=Address(
            street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"
        ),
        build_year=1994,
        notarized_on=date(2024, 3, 15),
        purchase_date=date(2024, 5, 1),
        purchase_price=Decimal("289000.00"),
    )


def _unit(run, ws, property_id, label="WE 3"):
    return run(
        ws,
        create_unit,
        UnitCreate(property_id=property_id, label=label, floor_area_m2=Decimal("68.40")),
        now=NOW,
    )


def _tenant(run, ws, name):
    return run(ws, create_tenant, TenantCreate(full_name=name), iban_key=TEST_IBAN_KEY, now=NOW)


def _tenancy(run, ws, unit_id, tenant_ids, start, end=None):
    roles = [TenancyRole.PRIMARY] + [TenancyRole.CO_TENANT] * (len(tenant_ids) - 1)
    data = TenancyCreate(
        unit_id=unit_id,
        parties=[TenancyParty(tenant_id=t, role=r) for t, r in zip(tenant_ids, roles, strict=True)],
        start_date=start,
        end_date=end,
        rent_model=RentModel.FIXED,
    )
    return run(ws, create_tenancy, data, initial_rent=None, iban_key=TEST_IBAN_KEY, now=NOW)


def test_empty_workspace_lists_nothing(read, fresh_workspaces):
    a, _ = fresh_workspaces
    assert read(a, list_owners) == []
    assert read(a, list_properties) == []
    assert read(a, list_tenants) == []


def test_list_owners_by_name_and_only_the_workspaces_own(run, read, fresh_workspaces):
    a, b = fresh_workspaces
    zoe = run(a, create_owner, OwnerCreate(full_name="Zoe Rivas"), now=NOW)
    luis = run(
        a,
        create_owner,
        OwnerCreate(full_name="Luis Rivas", marginal_tax_rate=Decimal("0.4431")),
        now=NOW,
    )
    run(b, create_owner, OwnerCreate(full_name="Anna Other"), now=NOW)

    owners = read(a, list_owners)
    assert [o.id for o in owners] == [luis.id, zoe.id]
    assert owners == [read(a, get_owner, luis.id), read(a, get_owner, zoe.id)]
    assert [o.full_name for o in read(b, list_owners)] == ["Anna Other"]


def test_list_properties_by_label_and_only_the_workspaces_own(run, read, fresh_workspaces):
    a, b = fresh_workspaces
    werder = run(a, create_property, _property("Wohnung Werderstraße"), now=NOW)
    linden = run(a, create_property, _property("Wohnung Lindenstraße"), now=NOW)
    run(b, create_property, _property("B's flat"), now=NOW)

    properties = read(a, list_properties)
    assert [p.id for p in properties] == [linden.id, werder.id]
    assert properties == [read(a, get_property, linden.id), read(a, get_property, werder.id)]
    assert properties[0].address.postal_code == "10115"
    assert [p.label for p in read(b, list_properties)] == ["B's flat"]


def test_list_tenants_includes_erased_ones_last(run, read, fresh_workspaces):
    a, b = fresh_workspaces
    max_ = _tenant(run, a, "Max Muster")
    erika = _tenant(run, a, "Erika Muster")
    applicant = _tenant(run, a, "Anna Applicant")
    run(a, erase_tenant, applicant.id, now=NOW)  # no tenancy: may always be erased
    _tenant(run, b, "Other Tenant")

    tenants = read(a, list_tenants)
    assert [t.id for t in tenants] == [erika.id, max_.id, applicant.id]
    assert tenants == [read(a, get_tenant, t) for t in (erika.id, max_.id, applicant.id)]
    assert (tenants[-1].full_name, tenants[-1].erased_at) == (None, NOW)
    assert [t.full_name for t in read(b, list_tenants)] == ["Other Tenant"]


def test_list_tenancies_of_one_unit_by_move_in(run, read, fresh_workspaces):
    a, b = fresh_workspaces
    prop = run(a, create_property, _property("Wohnung"), now=NOW)
    we3, we4 = _unit(run, a, prop.id, "WE 3"), _unit(run, a, prop.id, "WE 4")
    erika, max_ = _tenant(run, a, "Erika Muster"), _tenant(run, a, "Max Muster")
    later = _tenancy(run, a, we3.id, [max_.id], date(2025, 7, 1))
    earlier = _tenancy(run, a, we3.id, [erika.id, max_.id], date(2024, 7, 1), date(2025, 6, 30))
    other_unit = _tenancy(run, a, we4.id, [erika.id], date(2024, 7, 1))

    tenancies = read(a, list_tenancies, we3.id)
    assert [t.id for t in tenancies] == [earlier.id, later.id]
    assert tenancies == [read(a, get_tenancy, earlier.id), read(a, get_tenancy, later.id)]
    assert [p.role for p in tenancies[0].parties] == [TenancyRole.PRIMARY, TenancyRole.CO_TENANT]
    assert [t.id for t in read(a, list_tenancies, we4.id)] == [other_unit.id]
    assert read(b, list_tenancies, we3.id) == []  # another workspace sees none of it


def test_lists_write_nothing(run, read, fresh_workspaces):
    a, _ = fresh_workspaces
    prop = run(a, create_property, _property("Wohnung"), now=NOW)
    unit = _unit(run, a, prop.id)
    before = read(a, list_events)
    for query, args in (
        (list_owners, ()),
        (list_properties, ()),
        (list_tenants, ()),
        (list_tenancies, (unit.id,)),
    ):
        read(a, query, *args)
    assert read(a, list_events) == before
