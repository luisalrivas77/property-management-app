"""The workspace settings over HTTP (docs/phases/phase-2.md D42 (1); phase 5's reminder_emails,
D123): all three start off; a patch
writes only what changes and appends one workspace.settings_changed event naming it; a patch
that changes nothing writes no event; each workspace sees and changes only its own row. Both
also say whether reminder emails can be sent (`reminder_emails_available`, false here: the
test settings carry no Transactional Email key)."""

import pytest
from api_support import make_settings, ok

pytestmark = pytest.mark.db

OFF = {
    "ai_tenant_documents": False,
    "auto_accept_llm": False,
    "reminder_emails": False,
    "reminder_emails_available": False,
}


def _settings_events(client) -> list[dict]:
    return [
        event
        for event in ok(client.get("/api/events?entity_type=workspace"))
        if event["type"] == "workspace.settings_changed"
    ]


def test_all_three_switches_start_off(client_a):
    assert ok(client_a.get("/api/workspace/settings")) == OFF


def test_a_change_writes_one_event_naming_only_what_changed(client_a, auth_a):
    on = ok(client_a.patch("/api/workspace/settings", json={"ai_tenant_documents": True}))
    assert on == {**OFF, "ai_tenant_documents": True}
    assert ok(client_a.get("/api/workspace/settings")) == on

    [event] = _settings_events(client_a)
    assert event["entity_type"] == "workspace"
    assert event["entity_id"] == str(auth_a.workspace_id)
    assert event["payload"] == {"changes": {"ai_tenant_documents": True}}
    assert event["actor_type"] == "user"

    # Sending both, one of them unchanged: the event names only the one that changed.
    both = ok(
        client_a.patch(
            "/api/workspace/settings",
            json={"ai_tenant_documents": True, "auto_accept_llm": True},
        )
    )
    assert both == {**OFF, "ai_tenant_documents": True, "auto_accept_llm": True}
    assert [e["payload"] for e in _settings_events(client_a)] == [
        {"changes": {"ai_tenant_documents": True}},
        {"changes": {"auto_accept_llm": True}},
    ]


def test_reminder_emails_changes_through_settings_changed(client_a, auth_a):
    """Phase 5 (D123): the digest switch rides workspace.settings_changed like the other two."""
    on = ok(client_a.patch("/api/workspace/settings", json={"reminder_emails": True}))
    assert on == {**OFF, "reminder_emails": True}
    assert ok(client_a.get("/api/workspace/settings")) == on

    [event] = _settings_events(client_a)
    assert event["entity_type"] == "workspace"
    assert event["entity_id"] == str(auth_a.workspace_id)
    assert event["payload"] == {"changes": {"reminder_emails": True}}

    assert ok(client_a.patch("/api/workspace/settings", json={"reminder_emails": False})) == OFF
    assert [e["payload"] for e in _settings_events(client_a)] == [
        {"changes": {"reminder_emails": True}},
        {"changes": {"reminder_emails": False}},
    ]


def test_a_patch_that_changes_nothing_writes_no_event(client_a):
    assert ok(client_a.patch("/api/workspace/settings", json={})) == OFF
    assert ok(client_a.patch("/api/workspace/settings", json={"auto_accept_llm": False})) == OFF
    assert _settings_events(client_a) == []


def test_unknown_and_null_settings_are_refused(client_a):
    for body in ({"something_else": True}, {"auto_accept_llm": None}, {"reminder_emails": None}):
        response = client_a.patch("/api/workspace/settings", json=body)
        assert response.status_code == 422, body
        assert response.json()["error"] == "validation"
    assert _settings_events(client_a) == []


def test_each_workspace_changes_only_its_own_settings(client_a, client_b):
    ok(client_a.patch("/api/workspace/settings", json={"auto_accept_llm": True}))
    assert ok(client_b.get("/api/workspace/settings")) == OFF
    assert _settings_events(client_b) == []


def test_reminder_emails_available_follows_the_mail_settings(client_a, api_app, tmp_path):
    """Phase 5 (D123): true once the TEM key, project and sender are set; the switch itself is
    the workspace's and unchanged by it."""
    api_app.state.settings = make_settings(
        tmp_path,
        tem_secret_key="tem-secret-0123456789abcdef",
        tem_project_id="11111111-2222-3333-4444-555555555555",
        mail_from="hausbuch@mail.example.org",
    )
    assert ok(client_a.get("/api/workspace/settings")) == {**OFF, "reminder_emails_available": True}
    on = ok(client_a.patch("/api/workspace/settings", json={"reminder_emails": True}))
    assert on == {**OFF, "reminder_emails": True, "reminder_emails_available": True}
    # The flag is never a setting a patch can send.
    response = client_a.patch("/api/workspace/settings", json={"reminder_emails_available": False})
    assert response.status_code == 422
