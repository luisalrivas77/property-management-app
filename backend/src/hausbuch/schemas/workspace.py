"""Workspace = the row-level-security tenant. Not the same thing as an Owner."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import Record, Schema


class Workspace(Schema):
    """One household. Every other table carries workspace_id and is filtered by RLS."""

    id: UUID
    name: str = Field(min_length=1, max_length=200)
    created_at: datetime
    # Phase 2 settings (migration 0005), changed only with a workspace.settings_changed event.
    ai_tenant_documents: bool = False  # tenant documents may be sent to the model (D16)
    auto_accept_llm: bool = False  # LLM categorizations may auto-confirm (D26)
    reminder_emails: bool = False  # phase 5 (migration 0019, D123): the daily digest is mailed


class AppUser(Record):
    """A person who can sign in. Auth credentials (passkey / TOTP) land with the API PR."""

    email: str = Field(pattern=r"^[^@\sA-Z]+@[^@\sA-Z]+$")  # stored lower-case
    display_name: str = Field(min_length=1)
