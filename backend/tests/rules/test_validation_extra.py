"""Edge cases for the service-layer checks beyond validation.yaml."""

from hausbuch.rules.validation import check_tenancy_parties


def test_plain_string_roles_are_accepted():
    """Roles can arrive as plain strings (e.g. from a DB row); they must compare equal."""
    assert check_tenancy_parties(["primary", "co_tenant"]) == []
